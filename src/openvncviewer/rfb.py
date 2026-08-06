# OpenVNCViewer - a VNC viewer for macOS Screen Sharing with window scaling.
# Copyright (C) 2026 The OpenVNCViewer contributors
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
"""RFB (VNC) protocol client with Apple/ARD authentication.

Deliberately Qt-free: the framebuffer is a plain ``bytearray`` of 32-bit
little-endian BGRX pixels, which QImage can wrap without copying.
"""

import hashlib
import os
import socket
import struct
import threading
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SEC_NONE = 1
SEC_VNC = 2
SEC_ARD = 30

ENC_RAW = 0
ENC_COPYRECT = 1
ENC_ZRLE = 16
ENC_DESKTOP_SIZE = -223

BYTES_PER_PIXEL = 4

# Diffie-Hellman limits for ARD authentication. macOS Screen Sharing offers a
# 4096-bit group with generator 5; the floor is well below that because older
# releases may not, and the ceiling only exists to stop a server forcing
# arbitrarily expensive modular exponentiation on us.
DH_MIN_KEY_BYTES = 128
DH_MAX_KEY_BYTES = 1024
DH_MIN_PRIME_BITS = 1024
# Miller-Rabin rounds. Each is one modexp over the modulus; the chance of
# accepting a composite is below 4**-MILLER_RABIN_ROUNDS.
MILLER_RABIN_ROUNDS = 20
_SMALL_PRIMES = (3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59)

# Proving a 4096-bit modulus prime costs about 2.5 seconds, which would
# otherwise be paid on every single connect. These are groups already checked
# with _is_probable_prime, so a match skips the test. This is a fast path and
# not an allowlist: a prime that is not listed is still validated in full, so
# the worst an unrecognised group costs is time.
_VERIFIED_PRIME_DIGESTS = frozenset({
    # macOS Screen Sharing, 4096-bit, generator 5. Verified prime - and a safe
    # prime, so (p-1)/2 is prime too - against a live server.
    "4ee95187682bcb230ad26a95205f6920e84708f6251b3894329b09ec23919e33",
    # RFC 2409 group 2, 1024-bit. Used by the fake server in the tests.
    "3f35a3f5f6c4376a744acad409bb22f8d897f949d2311d885adaa890981b67a0",
})


class RFBError(Exception):
    pass


def _ignore(*args):
    """Callback stand-in for a client that has been stopped."""


def _pack_credential(text):
    """64-byte null-terminated field, random-padded, as the ARD server expects."""
    raw = text.encode("utf-8")[:63]
    return raw + b"\x00" + os.urandom(63 - len(raw))


class RFBClient:
    """Connects on a background thread and reports updates through callbacks.

    Callbacks fire on the network thread; the caller is responsible for
    marshalling them onto its own thread.
    """

    def __init__(self, host, port, username, password,
                 on_resize, on_damage, on_disconnect):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._on_resize = on_resize
        self._on_damage = on_damage
        self._on_disconnect = on_disconnect

        self.width = 0
        self.height = 0
        self.desktop_name = ""
        self.framebuffer = bytearray()

        self._sock = None
        self._reader = None
        self._send_lock = threading.Lock()
        self._zrle = zlib.decompressobj()
        self._running = False
        self._ready = False
        self._thread = None

    # ---------------------------------------------------------------- lifecycle

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._ready = False
        # Silence the callbacks before closing: the network thread is about to
        # report the disconnect, and by then the caller may already have
        # started a new session that this one must not clobber.
        self._on_resize = self._on_damage = self._on_disconnect = _ignore
        if self._sock is not None:
            self._sock.close()

    def _run(self):
        reason = None
        try:
            self._connect()
            while self._running:
                self._handle_server_message()
        except Exception as exc:  # network/protocol failure ends the session
            if self._running:
                reason = str(exc) or exc.__class__.__name__
        finally:
            self._running = False
            if self._sock is not None:
                self._sock.close()
            self._on_disconnect(reason)

    # ------------------------------------------------------------------- io

    def _read(self, count):
        data = self._reader.read(count)
        if data is None or len(data) < count:
            raise RFBError("connection closed by server")
        return data

    def _send(self, data):
        with self._send_lock:
            if self._sock is not None:
                self._sock.sendall(data)

    # ------------------------------------------------------------- handshake

    def _connect(self):
        self._sock = socket.create_connection((self.host, self.port), timeout=15)
        self._sock.settimeout(None)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._reader = self._sock.makefile("rb")

        version = self._read(12)
        if not version.startswith(b"RFB "):
            raise RFBError("not a VNC server")
        # macOS Screen Sharing announces 003.889; ask it to speak plain 3.8.
        minor = int(version[8:11])
        proto = 8 if minor >= 8 else (7 if minor == 7 else 3)
        self._send(b"RFB 003.%03d\n" % proto)

        self._authenticate(proto)

        self._send(b"\x01")  # ClientInit, shared=1
        width, height = struct.unpack("!HH", self._read(4))
        self._read(16)  # server pixel format, replaced below
        name_len = struct.unpack("!I", self._read(4))[0]
        self.desktop_name = self._read(name_len).decode("utf-8", "replace")

        self._set_pixel_format()
        self._set_encodings()
        # Only now may input be sent: anything written before this point would
        # be spliced into the handshake and the server would drop us.
        self._ready = True
        self._resize(width, height)
        self.request_update(incremental=False)

    def _authenticate(self, proto):
        if proto == 3:
            sec_type = struct.unpack("!I", self._read(4))[0]
            offered = [sec_type]
        else:
            count = self._read(1)[0]
            if count == 0:
                raise RFBError(self._read_failure_reason())
            offered = list(self._read(count))

        if SEC_ARD in offered:
            self._send(bytes([SEC_ARD]))
            self._auth_ard()
        elif SEC_NONE in offered:
            if proto != 3:
                self._send(bytes([SEC_NONE]))
        else:
            raise RFBError(
                "server does not offer macOS user authentication (security types "
                f"{offered}). In System Settings > General > Sharing > Screen "
                "Sharing, allow access for your user account."
            )

        if proto == 8 or (proto == 7 and SEC_ARD in offered):
            if struct.unpack("!I", self._read(4))[0] != 0:
                raise RFBError(self._read_failure_reason() if proto == 8
                               else "authentication failed")

    def _read_failure_reason(self):
        length = struct.unpack("!I", self._read(4))[0]
        return self._read(length).decode("utf-8", "replace")

    def _auth_ard(self):
        """Apple Remote Desktop auth: Diffie-Hellman, then AES-128-ECB creds.

        The group is chosen by the server, so it is checked before the password
        is encrypted under a key derived from it. This does not defend against
        a hostile server - it holds the other private key and can read the
        credentials whatever we do - it stops the password going out over an
        exchange a passive eavesdropper could unwind, whether the parameters
        were tampered with in transit or the server is simply broken.
        """
        generator, key_len = struct.unpack("!HH", self._read(4))
        if not DH_MIN_KEY_BYTES <= key_len <= DH_MAX_KEY_BYTES:
            raise RFBError(
                f"server proposed a {key_len * 8}-bit Diffie-Hellman group; "
                f"refusing anything outside {DH_MIN_KEY_BYTES * 8}-"
                f"{DH_MAX_KEY_BYTES * 8} bits")

        prime = int.from_bytes(self._read(key_len), "big")
        peer_key = int.from_bytes(self._read(key_len), "big")
        _validate_dh_group(generator, prime, peer_key)

        private = int.from_bytes(os.urandom(key_len), "big")
        public = pow(generator, private, prime)
        shared = pow(peer_key, private, prime)
        # Catches a peer key sitting in a tiny subgroup: the shared secret
        # collapses to a constant and the AES key becomes guessable.
        if shared <= 1 or shared >= prime - 1:
            raise RFBError("Diffie-Hellman produced a degenerate shared "
                           "secret; refusing to send credentials")

        aes_key = hashlib.md5(shared.to_bytes(key_len, "big")).digest()
        credentials = _pack_credential(self.username) + _pack_credential(self.password)
        encryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).encryptor()

        self._send(encryptor.update(credentials) + encryptor.finalize()
                   + public.to_bytes(key_len, "big"))

    def _set_pixel_format(self):
        # 32bpp little-endian true colour with shifts 16/8/0 == QImage RGB32.
        self._send(struct.pack("!B3x BBBB HHH BBB 3x", 0,
                               32, 24, 0, 1, 255, 255, 255, 16, 8, 0))

    def _set_encodings(self):
        encodings = [ENC_ZRLE, ENC_COPYRECT, ENC_RAW, ENC_DESKTOP_SIZE]
        self._send(struct.pack("!B x H", 2, len(encodings))
                   + b"".join(struct.pack("!i", e) for e in encodings))

    def _resize(self, width, height):
        self.width = width
        self.height = height
        self.framebuffer = bytearray(width * height * BYTES_PER_PIXEL)
        self._on_resize(width, height)

    # -------------------------------------------------------- server messages

    def _handle_server_message(self):
        msg = self._read(1)[0]
        if msg == 0:
            self._handle_framebuffer_update()
        elif msg == 1:  # SetColourMapEntries
            _, _, count = struct.unpack("!xHH", self._read(5))
            self._read(count * 6)
        elif msg == 2:  # Bell
            pass
        elif msg == 3:  # ServerCutText
            length = struct.unpack("!3xI", self._read(7))[0]
            self._read(length)
        else:
            raise RFBError(f"unsupported server message type {msg}")

    def _handle_framebuffer_update(self):
        count = struct.unpack("!xH", self._read(3))[0]
        resized = False
        damage = None
        for _ in range(count):
            x, y, w, h, encoding = struct.unpack("!HHHHi", self._read(12))
            if encoding == ENC_RAW:
                self._blit(x, y, w, h, self._read(w * h * BYTES_PER_PIXEL))
            elif encoding == ENC_COPYRECT:
                self._copy_rect(x, y, w, h)
            elif encoding == ENC_ZRLE:
                length = struct.unpack("!I", self._read(4))[0]
                self._decode_zrle(x, y, w, h, self._read(length))
            elif encoding == ENC_DESKTOP_SIZE:
                self._resize(w, h)
                resized = True
                continue
            else:
                raise RFBError(f"server used unrequested encoding {encoding}")
            # Report what actually changed so the view can repaint just that
            # much instead of rescaling the whole desktop.
            damage = _union(damage, x, y, w, h)

        if resized:
            self.request_update(incremental=False)
            return
        if damage is not None:
            self._on_damage(*damage)
        self.request_update(incremental=True)

    def _blit(self, x, y, w, h, pixels):
        """Copy a w*h block of BGRX pixels into the framebuffer at (x, y).

        A full-width block is contiguous in both buffers, so it looks like it
        should move in a single copy - but measured on a 3420x2214 frame that
        is 8.0ms against 3.4ms for the loop below, consistently. Copying in
        row-sized pieces stays in cache; one 30MB memcpy does not. See
        benchmarks/decode.py.
        """
        # Slice assignment past the end of a bytearray *grows* it rather than
        # failing, and QImage holds a raw pointer into this buffer - so an
        # out-of-bounds rectangle would reallocate underneath the view. One
        # O(1) check per blit is far cheaper than that going wrong.
        if x < 0 or y < 0 or x + w > self.width or y + h > self.height:
            raise RFBError(
                f"server sent a {w}x{h} rectangle at ({x}, {y}), outside the "
                f"{self.width}x{self.height} framebuffer")

        stride = self.width * BYTES_PER_PIXEL
        fb = self.framebuffer
        row_len = w * BYTES_PER_PIXEL
        for row in range(h):
            start = (y + row) * stride + x * BYTES_PER_PIXEL
            fb[start:start + row_len] = pixels[row * row_len:(row + 1) * row_len]

    def _copy_rect(self, x, y, w, h):
        src_x, src_y = struct.unpack("!HH", self._read(4))
        if src_x + w > self.width or src_y + h > self.height:
            raise RFBError(
                f"CopyRect source ({src_x}, {src_y}) {w}x{h} lies outside the "
                f"{self.width}x{self.height} framebuffer")
        stride = self.width * BYTES_PER_PIXEL
        row_len = w * BYTES_PER_PIXEL
        fb = self.framebuffer
        # Snapshot the source first so overlapping copies stay correct.
        rows = [bytes(fb[(src_y + r) * stride + src_x * BYTES_PER_PIXEL:
                         (src_y + r) * stride + src_x * BYTES_PER_PIXEL + row_len])
                for r in range(h)]
        for row, data in enumerate(rows):
            start = (y + row) * stride + x * BYTES_PER_PIXEL
            fb[start:start + row_len] = data

    # ------------------------------------------------------------------ ZRLE

    def _decode_zrle(self, x, y, w, h, data):
        # Nothing legitimate expands past 4 bytes per pixel - that is plain RLE
        # with every run one pixel long - plus a little per-tile overhead for
        # subencoding bytes and palettes. Without a ceiling, a compression bomb
        # turns a few hundred kilobytes into gigabytes of resident memory.
        tiles = ((w + 63) // 64) * ((h + 63) // 64)
        limit = w * h * 4 + tiles * 128 + 1024
        raw = self._zrle.decompress(data, limit)
        if self._zrle.unconsumed_tail:
            raise RFBError(
                f"ZRLE data for a {w}x{h} rectangle expanded past {limit} "
                "bytes; refusing to keep decompressing")

        pos = 0
        try:
            for tile_y in range(0, h, 64):
                th = min(64, h - tile_y)
                for tile_x in range(0, w, 64):
                    tw = min(64, w - tile_x)
                    pixels, pos = self._read_tile(raw, pos, tw, th)
                    # Straight into the framebuffer. Staging the whole rect in
                    # a scratch buffer first meant copying every pixel twice.
                    self._blit(x + tile_x, y + tile_y, tw, th, pixels)
        except (IndexError, ValueError) as exc:
            # A truncated or malformed tile runs off the end of the buffer.
            # Report it as the protocol error it is, not a stray IndexError.
            raise RFBError(f"malformed or truncated ZRLE tile data ({exc})") from exc

    def _read_tile(self, raw, pos, tw, th):
        """Return (tile pixels as tw*th*4 bytes, new position)."""
        count = tw * th
        sub = raw[pos]
        pos += 1

        if sub == 0:  # raw CPIXELs
            end = pos + count * 3
            # Slicing past the end truncates silently, and the shortfall would
            # only surface later as a confusing ValueError from _expand_cpixels.
            if end > len(raw):
                raise RFBError("truncated raw ZRLE tile")
            return _expand_cpixels(raw[pos:end], count), end

        if sub == 1:  # solid colour
            return _cpixel(raw, pos) * count, pos + 3

        if 2 <= sub <= 16:  # packed palette
            palette = [_cpixel(raw, pos + i * 3) for i in range(sub)]
            pos += sub * 3
            bits = 1 if sub == 2 else (2 if sub <= 4 else 4)
            row_bytes = (tw * bits + 7) // 8
            mask = (1 << bits) - 1
            shifts = tuple(range(8 - bits, -1, -bits))
            # The spare bits at the end of a row are padding and may hold any
            # value, so every index the mask can produce must be addressable.
            # The old per-pixel loop stopped at the tile width and never looked
            # at them; expanding a whole byte at a time does.
            palette += [palette[0]] * ((1 << bits) - len(palette))
            # Unpacking a byte yields the same pixels every time that byte
            # appears, and a 64x64 tile drawn from <=16 colours repeats bytes
            # heavily. Memoising per tile turns the per-pixel loop into one
            # dict lookup per byte, which is where nearly all the time went.
            expanded = {}
            row_width = tw * BYTES_PER_PIXEL
            rows = []
            for row in range(th):
                base = pos + row * row_bytes
                chunks = []
                for byte in raw[base:base + row_bytes]:
                    chunk = expanded.get(byte)
                    if chunk is None:
                        chunk = expanded[byte] = b"".join(
                            palette[(byte >> shift) & mask] for shift in shifts)
                    chunks.append(chunk)
                # The final byte of a row can hold more pixels than the tile is
                # wide; trim the overhang.
                rows.append(b"".join(chunks)[:row_width])
            return b"".join(rows), pos + row_bytes * th

        if sub == 128:  # plain RLE
            pixels = []
            written = 0
            while written < count:
                colour = _cpixel(raw, pos)
                pos += 3
                run, pos = _read_run_length(raw, pos)
                # A run may claim far more pixels than the tile holds. Left
                # unclamped, `colour * run` allocates on the attacker's word:
                # 39KB of input produced 40MB before this check existed.
                # Compared inline rather than via min(): this is the innermost
                # loop of the slowest decode path.
                remaining = count - written
                if run > remaining:
                    run = remaining
                pixels.append(colour * run)
                written += run
            return b"".join(pixels), pos

        if sub >= 130:  # palette RLE
            size = sub - 128
            palette = [_cpixel(raw, pos + i * 3) for i in range(size)]
            pos += size * 3
            pixels = []
            written = 0
            while written < count:
                index = raw[pos]
                pos += 1
                if index & 0x80:
                    index &= 0x7F
                    run, pos = _read_run_length(raw, pos)
                    remaining = count - written  # never allocate past the tile
                    if run > remaining:
                        run = remaining
                else:
                    run = 1
                if index >= size:
                    raise RFBError(
                        f"ZRLE palette index {index} outside a {size}-entry "
                        "palette")
                pixels.append(palette[index] * run)
                written += run
            return b"".join(pixels), pos

        raise RFBError(f"invalid ZRLE subencoding {sub}")

    # ---------------------------------------------------------- client events

    def request_update(self, incremental=True):
        self._send(struct.pack("!BBHHHH", 3, 1 if incremental else 0,
                               0, 0, self.width, self.height))

    def send_pointer(self, x, y, button_mask):
        if self._ready:
            self._send(struct.pack("!BBHH", 5, button_mask, x, y))

    def send_key(self, keysym, down):
        if self._ready:
            self._send(struct.pack("!BB2xI", 4, 1 if down else 0, keysym))


def _is_probable_prime(candidate, rounds=MILLER_RABIN_ROUNDS):
    """Miller-Rabin with random bases.

    A composite modulus can have a smooth multiplicative order, which makes the
    discrete logarithm - and so the shared secret, and so the password -
    tractable for anyone watching the exchange.
    """
    if candidate < 2 or candidate % 2 == 0:
        return candidate == 2
    for small in _SMALL_PRIMES:
        if candidate == small:
            return True
        if candidate % small == 0:
            return False

    odd, power = candidate - 1, 0
    while odd % 2 == 0:
        odd //= 2
        power += 1

    for _ in range(rounds):
        base = 2 + int.from_bytes(os.urandom(16), "big") % (candidate - 3)
        witness = pow(base, odd, candidate)
        if witness in (1, candidate - 1):
            continue
        for _ in range(power - 1):
            witness = witness * witness % candidate
            if witness == candidate - 1:
                break
        else:
            return False
    return True


def _prime_digest(prime):
    """Digest of the minimal big-endian encoding, so it is padding-independent."""
    return hashlib.sha256(
        prime.to_bytes((prime.bit_length() + 7) // 8, "big")).hexdigest()


def _validate_dh_group(generator, prime, peer_key):
    """Reject a Diffie-Hellman group that would not protect the credentials."""
    if prime.bit_length() < DH_MIN_PRIME_BITS:
        raise RFBError(
            f"server sent a {prime.bit_length()}-bit Diffie-Hellman prime; "
            f"at least {DH_MIN_PRIME_BITS} bits are required")
    if generator < 2 or generator >= prime:
        raise RFBError(f"invalid Diffie-Hellman generator {generator}")
    # 0, 1 and p-1 all give a shared secret with at most two possible values,
    # so the AES key derived from it is guessable without breaking anything.
    if peer_key <= 1 or peer_key >= prime - 1:
        raise RFBError("server sent a degenerate Diffie-Hellman public key")
    if _prime_digest(prime) in _VERIFIED_PRIME_DIGESTS:
        return
    if not _is_probable_prime(prime):
        raise RFBError("server's Diffie-Hellman modulus is not prime")


def _union(current, x, y, w, h):
    """Bounding box of the damage so far and one more rectangle."""
    if current is None:
        return (x, y, w, h)
    cx, cy, cw, ch = current
    left, top = min(cx, x), min(cy, y)
    right, bottom = max(cx + cw, x + w), max(cy + ch, y + h)
    return (left, top, right - left, bottom - top)


def _cpixel(raw, pos):
    """A ZRLE compressed pixel (3 bytes BGR) widened to 4-byte BGRX."""
    return raw[pos:pos + 3] + b"\xff"


def _expand_cpixels(data, count):
    """Widen a run of 3-byte CPIXELs to 4-byte BGRX using C-speed slicing.

    The X byte is set to 0xFF to match every other decode path. Format_RGB32
    ignores it, so leaving it at zero renders identically - but it makes the
    framebuffer differ byte-for-byte depending on which encoding painted a
    pixel, which defeats exact comparison in the tests. Returned as a bytearray
    because the caller only slices it.
    """
    out = bytearray(count * 4)
    out[0::4] = data[0::3]
    out[1::4] = data[1::3]
    out[2::4] = data[2::3]
    out[3::4] = b"\xff" * count
    return out


def _read_run_length(raw, pos):
    length = 1
    while raw[pos] == 255:
        length += 255
        pos += 1
    return length + raw[pos], pos + 1
