"""End-to-end test against a fake server that mimics macOS Screen Sharing.

Checks the ARD (security type 30) handshake and the Raw / CopyRect / ZRLE
decoders by comparing the client framebuffer against an independently
computed reference image.
"""

import hashlib
import os
import socket
import struct
import threading
import time
import unittest
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from openvncviewer.rfb import RFBClient

# RFC 2409 group 2 (1024-bit), the size Apple's server uses.
PRIME = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74"
    "020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F1437"
    "4FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE65381FFFFFFFFFFFFFFFF", 16)
GENERATOR = 2
KEY_LEN = 128

WIDTH, HEIGHT = 200, 120
USERNAME, PASSWORD = "testuser", "s3cret-pass"


def pixel(b, g, r):
    return bytes((b, g, r, 0xFF))


def cpixel(b, g, r):
    return bytes((b, g, r))


class Reference:
    """A plain, obvious framebuffer model to compare the client against."""

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.data = bytearray(width * height * 4)

    def set(self, x, y, value):
        offset = (y * self.width + x) * 4
        self.data[offset:offset + 4] = value

    def get(self, x, y):
        offset = (y * self.width + x) * 4
        return bytes(self.data[offset:offset + 4])


def build_zrle_tiles(rect_w, rect_h, reference, origin_x, origin_y):
    """Encode a rect as ZRLE, one subencoding per tile, updating `reference`."""
    stream = bytearray()
    tile_index = 0
    for tile_y in range(0, rect_h, 64):
        th = min(64, rect_h - tile_y)
        for tile_x in range(0, rect_w, 64):
            tw = min(64, rect_w - tile_x)
            colours = ENCODERS[tile_index % len(ENCODERS)](stream, tw, th)
            tile_index += 1
            for row in range(th):
                for col in range(tw):
                    reference.set(origin_x + tile_x + col, origin_y + tile_y + row,
                                  colours[row * tw + col])
    return stream


def encode_raw(stream, tw, th):
    stream.append(0)
    colours = []
    for row in range(th):
        for col in range(tw):
            b, g, r = (col * 3) % 256, (row * 5) % 256, (col + row) % 256
            stream += cpixel(b, g, r)
            colours.append(pixel(b, g, r))
    return colours


def encode_solid(stream, tw, th):
    stream.append(1)
    stream += cpixel(10, 20, 30)
    return [pixel(10, 20, 30)] * (tw * th)


def encode_packed_palette(stream, tw, th):
    """Five colours -> 4 bits per index."""
    palette = [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255), (128, 128, 128)]
    stream.append(len(palette))
    for b, g, r in palette:
        stream += cpixel(b, g, r)

    bits, colours = 4, []
    row_bytes = (tw * bits + 7) // 8
    for row in range(th):
        packed = bytearray(row_bytes)
        for col in range(tw):
            index = (col + row) % len(palette)
            colours.append(pixel(*palette[index]))
            shift = 4 if col % 2 == 0 else 0
            packed[col // 2] |= index << shift
        stream += packed
    return colours


def encode_plain_rle(stream, tw, th):
    stream.append(128)
    total = tw * th
    # A run over 255 exercises the multi-byte length encoding.
    runs = [(pixel(1, 2, 3), 300), (pixel(9, 8, 7), 1), (pixel(4, 5, 6), 17)]
    colours = []
    written = 0
    step = 0
    while written < total:
        colour, run = runs[step % len(runs)]
        run = min(run, total - written)
        stream += colour[:3]
        stream += encode_run_length(run)
        colours += [colour] * run
        written += run
        step += 1
    return colours


def encode_palette_rle(stream, tw, th):
    palette = [(200, 100, 50), (5, 6, 7), (77, 88, 99)]
    stream.append(128 + len(palette))
    for b, g, r in palette:
        stream += cpixel(b, g, r)

    total = tw * th
    colours = []
    written = 0
    index = 0
    while written < total:
        run = min(7 if index % 2 else 1, total - written)
        entry = index % len(palette)
        if run == 1:
            stream.append(entry)
        else:
            stream.append(entry | 0x80)
            stream += encode_run_length(run)
        colours += [pixel(*palette[entry])] * run
        written += run
        index += 1
    return colours


def encode_run_length(run):
    """Run length as the spec writes it: (run - 1) in base-255 chunks."""
    remaining = run - 1
    out = bytearray()
    while remaining >= 255:
        out.append(255)
        remaining -= 255
    out.append(remaining)
    return bytes(out)


ENCODERS = [encode_raw, encode_solid, encode_packed_palette,
            encode_plain_rle, encode_palette_rle]


class FakeMacServer(threading.Thread):
    def __init__(self, delay=0.0):
        super().__init__(daemon=True)
        self.delay = delay  # widens the handshake window for the racing test
        self.input_messages = 0
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.reference = Reference(WIDTH, HEIGHT)
        self.credentials = None
        self.error = None
        self.ready = threading.Event()

    def run(self):
        try:
            self.conn, _ = self.listener.accept()
            self.reader = self.conn.makefile("rb")
            self.handshake()
            self.authenticate()
            self.initialise()
            self.send_updates()
            self.ready.set()
        except Exception as exc:  # surfaced by the test
            self.error = exc
            self.ready.set()

    def read(self, count):
        data = self.reader.read(count)
        assert len(data) == count, "client closed early"
        return data

    def handshake(self):
        self.conn.sendall(b"RFB 003.889\n")  # what macOS actually announces
        time.sleep(self.delay)
        reply = self.read(12)
        assert reply == b"RFB 003.008\n", f"handshake corrupted: {reply!r}"

    def authenticate(self):
        self.conn.sendall(bytes([2, 30, 33]))
        time.sleep(self.delay)
        chosen = self.read(1)[0]
        assert chosen == 30, f"expected ARD auth, got {chosen} (stray input?)"

        private = int.from_bytes(os.urandom(KEY_LEN), "big")
        public = pow(GENERATOR, private, PRIME)
        self.conn.sendall(struct.pack("!HH", GENERATOR, KEY_LEN)
                          + PRIME.to_bytes(KEY_LEN, "big")
                          + public.to_bytes(KEY_LEN, "big"))

        time.sleep(self.delay)
        encrypted = self.read(128)
        peer_public = int.from_bytes(self.read(KEY_LEN), "big")
        shared = pow(peer_public, private, PRIME)
        key = hashlib.md5(shared.to_bytes(KEY_LEN, "big")).digest()
        decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        plain = decryptor.update(encrypted) + decryptor.finalize()
        self.credentials = (plain[:64].split(b"\x00")[0].decode(),
                            plain[64:].split(b"\x00")[0].decode())

        self.conn.sendall(struct.pack("!I", 0))  # SecurityResult: OK

    def initialise(self):
        time.sleep(self.delay)
        client_init = self.read(1)
        assert client_init == b"\x01", f"expected ClientInit, got {client_init!r}"
        name = b"Test Mac"
        self.conn.sendall(struct.pack("!HH", WIDTH, HEIGHT)
                          + struct.pack("!BBBBHHHBBB3x", 32, 24, 0, 1,
                                        255, 255, 255, 16, 8, 0)
                          + struct.pack("!I", len(name)) + name)

        # Parse by message type, the way a real server does, so legitimately
        # interleaved input events do not desynchronise us.
        self.pixel_format = None
        self.encodings = []
        while True:
            kind = self.read(1)[0]
            if kind == 0:      # SetPixelFormat
                self.pixel_format = b"\x00" + self.read(19)
            elif kind == 2:    # SetEncodings
                count = struct.unpack("!xH", self.read(3))[0]
                self.encodings = [struct.unpack("!i", self.read(4))[0]
                                  for _ in range(count)]
            elif kind == 3:    # FramebufferUpdateRequest - ready for pixels
                self.read(9)
                return
            elif kind == 4:    # KeyEvent
                self.read(7)
                self.input_messages += 1
            elif kind == 5:    # PointerEvent
                self.read(5)
                self.input_messages += 1
            else:
                raise AssertionError(f"bad client message type {kind}")

    def send_updates(self):
        rects = [self.raw_rect(), self.zrle_rect(), self.copy_rect()]
        self.conn.sendall(struct.pack("!BxH", 0, len(rects)) + b"".join(rects))

    def raw_rect(self):
        """Fill the whole screen with a gradient."""
        body = bytearray()
        for y in range(HEIGHT):
            for x in range(WIDTH):
                value = pixel(x % 256, y % 256, (x * y) % 256)
                body += value
                self.reference.set(x, y, value)
        return struct.pack("!HHHHi", 0, 0, WIDTH, HEIGHT, 0) + bytes(body)

    def zrle_rect(self):
        """130x70 at (10, 20): three tile columns, two tile rows."""
        x, y, w, h = 10, 20, 130, 70
        tiles = build_zrle_tiles(w, h, self.reference, x, y)
        payload = zlib.compress(bytes(tiles))
        return (struct.pack("!HHHHi", x, y, w, h, 16)
                + struct.pack("!I", len(payload)) + payload)

    def copy_rect(self):
        """Copy 40x30 from (0, 0) to (150, 80)."""
        src_x, src_y, x, y, w, h = 0, 0, 150, 80, 40, 30
        snapshot = [[self.reference.get(src_x + c, src_y + r) for c in range(w)]
                    for r in range(h)]
        for r in range(h):
            for c in range(w):
                self.reference.set(x + c, y + r, snapshot[r][c])
        return struct.pack("!HHHHi", x, y, w, h, 1) + struct.pack("!HH", src_x, src_y)


class HandshakeRaceTest(unittest.TestCase):
    """Input must not reach the socket until the session is initialised.

    The connect dialog closes on Enter, and the key *release* is delivered to
    the remote view microseconds later - while the network thread is still
    negotiating. Writing that KeyEvent into the stream splices 8 bytes into the
    handshake and a real macOS server closes the connection.
    """

    def test_input_during_handshake_does_not_corrupt_it(self):
        server = FakeMacServer(delay=0.4)  # widen the handshake window
        server.start()

        damaged = threading.Event()
        failure = []
        client = RFBClient("127.0.0.1", server.port, USERNAME, PASSWORD,
                           on_resize=lambda w, h: None,
                           on_damage=damaged.set,
                           on_disconnect=lambda reason: failure.append(reason))
        client.start()

        stop = threading.Event()

        def hammer_input():
            while not stop.is_set():
                client.send_key(0xFF0D, False)   # the Enter release
                client.send_pointer(5, 5, 0)
                time.sleep(0.002)

        spam = threading.Thread(target=hammer_input, daemon=True)
        spam.start()
        try:
            self.assertTrue(damaged.wait(30),
                            f"session died during handshake (error={failure})")
        finally:
            stop.set()
            spam.join(5)
            client.stop()

        server.ready.wait(5)
        self.assertIsNone(server.error, f"server rejected the stream: {server.error!r}")
        self.assertEqual(server.credentials, (USERNAME, PASSWORD))
        self.assertEqual(bytes(client.framebuffer), bytes(server.reference.data))


class StoppedClientTest(unittest.TestCase):
    def test_stopped_client_never_reports_a_disconnect(self):
        """Reconnecting stops the old client; its dying callback must not fire.

        MainWindow.connect_to() disconnects before dialling again, so a late
        callback from the old session would tear down the new one.
        """
        server = FakeMacServer()
        server.start()

        resized = threading.Event()
        events = []
        client = RFBClient(
            "127.0.0.1", server.port, USERNAME, PASSWORD,
            on_resize=lambda w, h: (events.append("resize"), resized.set()),
            on_damage=lambda: events.append("damage"),
            on_disconnect=lambda reason: events.append("disconnect"),
        )
        client.start()
        self.assertTrue(resized.wait(20), "never connected")

        client.stop()
        time.sleep(1.5)  # let the network thread unwind
        self.assertNotIn("disconnect", events)


class ProtocolTest(unittest.TestCase):
    def test_ard_auth_and_encodings(self):
        server = FakeMacServer()
        server.start()

        damaged = threading.Event()
        resized = []
        failure = []

        client = RFBClient(
            "127.0.0.1", server.port, USERNAME, PASSWORD,
            on_resize=lambda w, h: resized.append((w, h)),
            on_damage=damaged.set,
            on_disconnect=lambda reason: failure.append(reason),
        )
        client.start()

        self.assertTrue(damaged.wait(20), f"no framebuffer update (error={failure})")
        server.ready.wait(5)
        client.stop()

        self.assertIsNone(server.error, f"server failed: {server.error!r}")
        self.assertEqual(server.credentials, (USERNAME, PASSWORD))
        self.assertEqual(resized, [(WIDTH, HEIGHT)])
        self.assertEqual(server.pixel_format,
                         struct.pack("!B3xBBBBHHHBBB3x", 0, 32, 24, 0, 1,
                                     255, 255, 255, 16, 8, 0))
        self.assertIn(16, server.encodings)

        expected = bytes(server.reference.data)
        actual = bytes(client.framebuffer)
        self.assertEqual(len(actual), len(expected))
        if actual != expected:
            for index in range(0, len(expected), 4):
                if actual[index:index + 4] != expected[index:index + 4]:
                    pos = index // 4
                    self.fail(f"pixel mismatch at ({pos % WIDTH}, {pos // WIDTH}): "
                              f"{actual[index:index + 4].hex()} != "
                              f"{expected[index:index + 4].hex()}")


if __name__ == "__main__":
    unittest.main()
