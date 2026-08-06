"""End-to-end tests against fake servers, with no real host required.

Covers the macOS ARD handshake (security type 30), the legacy VNC password
(type 2) used by other servers, RFB 3.3/3.7/3.8 differences, and the Raw /
CopyRect / ZRLE decoders - the last compared against an independently
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

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from openvncviewer.rfb import SEC_NONE, SEC_VNC, RFBClient, RFBError

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
                           on_damage=lambda *bounds: damaged.set(),
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


class HostileDHServer(threading.Thread):
    """Offers ARD auth with a Diffie-Hellman group of our choosing."""

    def __init__(self, generator=GENERATOR, prime=PRIME, peer_key=None,
                 key_len=KEY_LEN):
        super().__init__(daemon=True)
        self.generator = generator
        self.prime = prime
        self.key_len = key_len
        self.peer_key = peer_key
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.credentials_received = b""
        self.done = threading.Event()

    def run(self):
        try:
            conn, _ = self.listener.accept()
            reader = conn.makefile("rb")
            conn.sendall(b"RFB 003.889\n")
            reader.read(12)
            conn.sendall(bytes([1, 30]))
            reader.read(1)

            peer = self.peer_key
            if peer is None:
                peer = pow(self.generator, 0x1234567, self.prime)
            conn.sendall(struct.pack("!HH", self.generator, self.key_len)
                         + self.prime.to_bytes(self.key_len, "big")
                         + peer.to_bytes(self.key_len, "big"))

            # Anything arriving now is the encrypted credential block. A client
            # that validated properly sends nothing and hangs up, so a short
            # wait is enough - and keeps the suite quick.
            conn.settimeout(1.5)
            try:
                self.credentials_received = conn.recv(256)
            except OSError:
                pass
            conn.close()
        except Exception:
            pass
        finally:
            self.done.set()


class DiffieHellmanValidationTest(unittest.TestCase):
    """A weak or degenerate group must stop the password reaching the wire.

    This does not protect against a hostile server, which holds the other
    private key and can read the credentials regardless. It stops the password
    going out over an exchange a passive eavesdropper could unwind.
    """

    def connect_expecting_refusal(self, server):
        server.start()
        failure = []
        finished = threading.Event()

        def on_disconnect(reason):
            failure.append(reason)
            finished.set()

        client = RFBClient("127.0.0.1", server.port, USERNAME, PASSWORD,
                           on_resize=lambda w, h: None,
                           on_damage=lambda *bounds: None,
                           on_disconnect=on_disconnect)
        client.start()
        self.assertTrue(finished.wait(30), "client neither connected nor failed")
        client.stop()
        server.done.wait(10)
        self.assertEqual(server.credentials_received, b"",
                         "credentials were sent despite a bad DH group")
        return failure[0] or ""

    def test_rejects_a_short_prime(self):
        # 1023-bit prime: under the floor, so cheap to attack.
        reason = self.connect_expecting_refusal(
            HostileDHServer(prime=(1 << 1023) - 1525, key_len=128))
        self.assertIn("Diffie-Hellman", reason)

    def test_rejects_a_composite_modulus(self):
        # PRIME squared: a 2048-bit composite with no small factors, so it can
        # only be caught by the primality test itself.
        composite = PRIME * PRIME
        for small in (3, 5, 7, 11, 13):
            self.assertNotEqual(composite % small, 0, "caught by trial division")
        reason = self.connect_expecting_refusal(
            HostileDHServer(prime=composite, key_len=256))
        self.assertIn("not prime", reason)

    def test_rejects_a_degenerate_peer_key(self):
        for peer_key in (0, 1, PRIME - 1):
            with self.subTest(peer_key=peer_key):
                reason = self.connect_expecting_refusal(
                    HostileDHServer(peer_key=peer_key))
                self.assertIn("degenerate", reason)

    def test_rejects_a_bad_generator(self):
        for generator in (0, 1):
            with self.subTest(generator=generator):
                reason = self.connect_expecting_refusal(
                    HostileDHServer(generator=generator))
                self.assertIn("generator", reason)

    def test_rejects_an_absurd_key_length(self):
        # A tiny group the client must refuse on the advertised length alone,
        # before it even reads the prime. The prime has to fit in key_len bytes
        # or the server, not the client, is what fails.
        reason = self.connect_expecting_refusal(
            HostileDHServer(generator=2, prime=97, peer_key=5, key_len=8))
        self.assertIn("Diffie-Hellman", reason)

    def test_accepts_the_group_the_tests_use(self):
        from openvncviewer.rfb import _validate_dh_group
        _validate_dh_group(GENERATOR, PRIME, pow(GENERATOR, 12345, PRIME))

    def test_known_groups_skip_the_expensive_primality_test(self):
        """Otherwise every connect pays seconds of Miller-Rabin."""
        from openvncviewer.rfb import _prime_digest, _VERIFIED_PRIME_DIGESTS
        self.assertIn(_prime_digest(PRIME), _VERIFIED_PRIME_DIGESTS)

        start = time.perf_counter()
        from openvncviewer.rfb import _validate_dh_group
        _validate_dh_group(GENERATOR, PRIME, pow(GENERATOR, 999, PRIME))
        self.assertLess(time.perf_counter() - start, 0.05,
                        "a known-good group took the slow path")


class FakeVNCServer(threading.Thread):
    """A plain RFB server: no ARD, just the legacy VNC password (type 2)."""

    def __init__(self, password="secret12", version=b"RFB 003.008\n",
                 offer=(SEC_VNC,), fail_auth=False):
        super().__init__(daemon=True)
        self.password = password
        self.version = version
        self.offer = offer
        self.fail_auth = fail_auth
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.chosen_type = None
        self.response_valid = None
        self.error = None
        self.ready = threading.Event()

    def expected_response(self, challenge):
        """Computed here independently of the client's own key derivation."""
        raw = self.password.encode("utf-8")[:8].ljust(8, b"\x00")
        key = bytes(int(f"{byte:08b}"[::-1], 2) for byte in raw)
        encryptor = Cipher(TripleDES(key * 3), modes.ECB()).encryptor()
        return encryptor.update(challenge) + encryptor.finalize()

    def run(self):
        try:
            self.conn, _ = self.listener.accept()
            reader = self.conn.makefile("rb")
            self.conn.sendall(self.version)
            reader.read(12)

            minor = int(self.version[8:11])
            if minor >= 7:
                self.conn.sendall(bytes([len(self.offer)]) + bytes(self.offer))
                self.chosen_type = reader.read(1)[0]
            else:
                self.conn.sendall(struct.pack("!I", self.offer[0]))
                self.chosen_type = self.offer[0]

            if self.chosen_type == SEC_VNC:
                challenge = os.urandom(16)
                self.conn.sendall(challenge)
                response = reader.read(16)
                self.response_valid = response == self.expected_response(challenge)

            failed = self.fail_auth or self.response_valid is False
            if minor >= 8 or self.chosen_type != SEC_NONE:
                self.conn.sendall(struct.pack("!I", 1 if failed else 0))
                if failed and minor >= 8:
                    reason = b"bad password"
                    self.conn.sendall(struct.pack("!I", len(reason)) + reason)
            if failed:
                self.ready.set()
                self.conn.close()
                return

            reader.read(1)  # ClientInit
            name = b"Plain VNC"
            self.conn.sendall(struct.pack("!HH", WIDTH, HEIGHT)
                         + struct.pack("!BBBBHHHBBB3x", 32, 24, 0, 1,
                                       255, 255, 255, 16, 8, 0)
                         + struct.pack("!I", len(name)) + name)

            while True:  # consume setup, then send one raw full-screen rect
                kind = reader.read(1)[0]
                if kind == 0:
                    reader.read(19)
                elif kind == 2:
                    count = struct.unpack("!xH", reader.read(3))[0]
                    reader.read(count * 4)
                elif kind == 3:
                    reader.read(9)
                    break
                elif kind == 4:
                    reader.read(7)
                elif kind == 5:
                    reader.read(5)
            body = bytes((7, 8, 9, 255)) * (WIDTH * HEIGHT)
            self.conn.sendall(struct.pack("!BxH", 0, 1)
                         + struct.pack("!HHHHi", 0, 0, WIDTH, HEIGHT, 0) + body)
            self.ready.set()
        except Exception as exc:
            self.error = exc
            self.ready.set()


class StandardVNCTest(unittest.TestCase):
    """Connecting to a non-Apple server that uses the legacy VNC password."""

    def run_client(self, server, username="", password="secret12", timeout=20):
        server.start()
        connected = threading.Event()
        failure = []
        client = RFBClient("127.0.0.1", server.port, username, password,
                           on_resize=lambda w, h: None,
                           on_damage=lambda *bounds: connected.set(),
                           on_disconnect=lambda reason: (failure.append(reason),
                                                         connected.set()))
        client.start()
        connected.wait(timeout)
        client.stop()
        server.ready.wait(5)
        return client, (failure[0] if failure else None)

    def test_des_key_matches_a_published_vector(self):
        """Guards the bit-reversal and the single-DES-via-3DES construction."""
        from openvncviewer.rfb import _vnc_auth_key
        # FIPS 81 vector: key 0123456789abcdef, "Now is t" -> 3fa40e8a984d4815.
        encryptor = Cipher(TripleDES(bytes.fromhex("0123456789abcdef") * 3),
                           modes.ECB()).encryptor()
        out = encryptor.update(b"Now is t") + encryptor.finalize()
        self.assertEqual(out.hex(), "3fa40e8a984d4815")
        # And the VNC quirk: every bit of every password byte is reversed.
        self.assertEqual(_vnc_auth_key("\x01"), bytes([0x80]) + b"\x00" * 7)

    def test_connects_with_a_vnc_password(self):
        server = FakeVNCServer(password="secret12")
        client, failure = self.run_client(server)
        self.assertIsNone(server.error, f"server failed: {server.error!r}")
        self.assertEqual(server.chosen_type, SEC_VNC)
        self.assertTrue(server.response_valid, "DES challenge response rejected")
        self.assertIsNone(failure)
        self.assertEqual(client.desktop_name, "Plain VNC")
        self.assertEqual((client.width, client.height), (WIDTH, HEIGHT))

    def test_a_wrong_password_is_reported_clearly(self):
        server = FakeVNCServer(password="correct1")
        _, failure = self.run_client(server, password="wrong999")
        self.assertFalse(server.response_valid)
        self.assertIsNotNone(failure)
        self.assertIn("password", failure.lower())

    def test_passwords_past_eight_characters_are_ignored(self):
        """The protocol truncates; the viewer must truncate identically."""
        server = FakeVNCServer(password="12345678")
        _, failure = self.run_client(server, password="12345678ignored")
        self.assertTrue(server.response_valid)
        self.assertIsNone(failure)

    def test_a_server_needing_no_authentication_works(self):
        server = FakeVNCServer(offer=(SEC_NONE,))
        client, failure = self.run_client(server, password="")
        self.assertEqual(server.chosen_type, SEC_NONE)
        self.assertIsNone(failure)
        self.assertEqual(client.desktop_name, "Plain VNC")

    def test_rfb_3_3_servers_work(self):
        """3.3 dictates the security type instead of offering a list."""
        server = FakeVNCServer(password="secret12", version=b"RFB 003.003\n")
        client, failure = self.run_client(server)
        self.assertIsNone(server.error, f"server failed: {server.error!r}")
        self.assertTrue(server.response_valid)
        self.assertIsNone(failure)
        self.assertEqual(client.desktop_name, "Plain VNC")

    def test_security_type_choice(self):
        from openvncviewer.rfb import RFBClient as C

        def choose(offered, username, password):
            client = C.__new__(C)
            client.username, client.password = username, password
            return client._choose_security(offered)

        # A macOS account beats the legacy password when both are on offer.
        self.assertEqual(choose([30, 2, 1], "someone", "pw"), 30)
        # No account name given, so the VNC password is the sensible pick.
        self.assertEqual(choose([30, 2, 1], "", "pw"), 2)
        # Neither credential: take the server at its word that none is needed.
        self.assertEqual(choose([30, 2, 1], "", ""), 1)
        self.assertEqual(choose([2], "", "pw"), 2)
        self.assertEqual(choose([1], "", ""), 1)
        # Nothing we can speak.
        with self.assertRaises(RFBError):
            choose([18, 19], "someone", "pw")


class HostileStreamTest(unittest.TestCase):
    """A malicious server must not be able to spend our memory or corrupt state.

    Every case here is something a server can send. Going public means the
    other end is not necessarily a Mac you own.
    """

    def make_client(self, width=WIDTH, height=HEIGHT):
        client = RFBClient.__new__(RFBClient)
        client.width, client.height = width, height
        client.framebuffer = bytearray(width * height * 4)
        client._zrle = zlib.decompressobj()
        return client

    def test_a_run_longer_than_the_tile_cannot_allocate(self):
        """39KB of input used to produce 40MB of pixels."""
        tile = bytearray([128])          # plain RLE
        tile += bytes((1, 2, 3))         # one CPIXEL
        remaining = 10_000_000 - 1       # a run far past the 4096-pixel tile
        while remaining >= 255:
            tile.append(255)
            remaining -= 255
        tile.append(remaining)

        client = self.make_client()
        pixels, _ = client._read_tile(bytes(tile), 0, 64, 64)
        self.assertEqual(len(pixels), 64 * 64 * 4,
                         "decoder produced more pixels than the tile holds")

    def test_a_palette_run_longer_than_the_tile_cannot_allocate(self):
        tile = bytearray([130])          # palette RLE, 2 entries
        tile += bytes((1, 2, 3)) + bytes((4, 5, 6))
        tile.append(0 | 0x80)            # index 0, with a run length following
        remaining = 5_000_000 - 1
        while remaining >= 255:
            tile.append(255)
            remaining -= 255
        tile.append(remaining)

        client = self.make_client()
        pixels, _ = client._read_tile(bytes(tile), 0, 64, 64)
        self.assertEqual(len(pixels), 64 * 64 * 4)

    def test_a_compression_bomb_is_refused(self):
        client = self.make_client()
        bomb = zlib.compress(b"\x00" * 50_000_000, 9)
        self.assertLess(len(bomb), 100_000, "test bomb is not actually compressed")
        with self.assertRaises(RFBError) as caught:
            client._decode_zrle(0, 0, 64, 64, bomb)
        self.assertIn("expanded past", str(caught.exception))

    def test_a_rectangle_outside_the_framebuffer_is_refused(self):
        """Slice assignment past the end grows the bytearray QImage points into."""
        client = self.make_client()
        original = len(client.framebuffer)
        for x, y, w, h in ((WIDTH - 5, 0, 10, 10),      # off the right edge
                           (0, HEIGHT - 5, 10, 10),     # off the bottom
                           (0, 0, WIDTH + 1, 1),        # wider than the screen
                           (0, 0, 1, HEIGHT + 1)):      # taller than the screen
            with self.subTest(rect=(x, y, w, h)):
                with self.assertRaises(RFBError):
                    client._blit(x, y, w, h, bytes(w * h * 4))
                self.assertEqual(len(client.framebuffer), original,
                                 "framebuffer was resized under the view")

    def test_a_valid_rectangle_at_the_edge_still_works(self):
        client = self.make_client()
        client._blit(WIDTH - 10, HEIGHT - 10, 10, 10, bytes(10 * 10 * 4))
        self.assertEqual(len(client.framebuffer), WIDTH * HEIGHT * 4)

    def test_a_bad_palette_index_is_a_protocol_error(self):
        tile = bytearray([130])          # palette RLE, 2 entries
        tile += bytes((1, 2, 3)) + bytes((4, 5, 6))
        tile.append(9)                   # index 9 into a 2-entry palette
        client = self.make_client()
        with self.assertRaises(RFBError):
            client._read_tile(bytes(tile), 0, 8, 8)

    def test_truncated_tile_data_is_a_protocol_error(self):
        client = self.make_client()
        # Claims a 64x64 raw tile but supplies almost none of it.
        payload = zlib.compress(bytes([0]) + b"\x01\x02\x03" * 4)
        with self.assertRaises(RFBError) as caught:
            client._decode_zrle(0, 0, 64, 64, payload)
        self.assertIn("ZRLE", str(caught.exception))


class DamageBoundsTest(unittest.TestCase):
    """The view repaints only what changed, so the bounds must be right."""

    def test_reports_the_union_of_every_rect_in_the_update(self):
        server = FakeMacServer()
        server.start()

        reported = []
        damaged = threading.Event()

        def on_damage(x, y, w, h):
            reported.append((x, y, w, h))
            damaged.set()

        client = RFBClient("127.0.0.1", server.port, USERNAME, PASSWORD,
                           on_resize=lambda w, h: None,
                           on_damage=on_damage,
                           on_disconnect=lambda reason: None)
        client.start()
        self.assertTrue(damaged.wait(20), "no damage reported")
        client.stop()

        # The fake server sends a full-screen raw rect, a ZRLE rect at (10,20)
        # and a copyrect at (150,80): the union is the whole framebuffer.
        self.assertEqual(reported[0], (0, 0, WIDTH, HEIGHT))

    def test_union_covers_every_corner(self):
        from openvncviewer.rfb import _union
        self.assertEqual(_union(None, 5, 6, 10, 10), (5, 6, 10, 10))
        # Disjoint rects: the box must span both, not just the newer one.
        self.assertEqual(_union((5, 6, 10, 10), 100, 200, 4, 4),
                         (5, 6, 99, 198))
        # A rect wholly inside the current box must not shrink it.
        self.assertEqual(_union((0, 0, 50, 50), 10, 10, 5, 5), (0, 0, 50, 50))


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
            on_damage=lambda *bounds: events.append("damage"),
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
            on_damage=lambda *bounds: damaged.set(),
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
