"""Ceilings on anything the peer sizes.

Every one of these is a small number of bytes on the wire asking for a large
amount of work: a `u16` pair that becomes an allocation, a `u32` length that
becomes a read. The point of each limit is that the refusal happens *before*
the allocation or the read, not after.
"""

import struct
import unittest
import zlib

from openvncviewer.rfb import (BYTES_PER_PIXEL, MAX_COMPRESSED_BYTES,
                               MAX_DESKTOP_NAME_BYTES, MAX_DIMENSION,
                               MAX_PIXELS, RFBClient, RFBError)


def client(width, height):
    instance = RFBClient.__new__(RFBClient)
    instance.width, instance.height = width, height
    instance.framebuffer = bytearray(width * height * BYTES_PER_PIXEL)
    instance._zrle = zlib.decompressobj()
    instance._running = True
    instance._on_resize = lambda w, h: None
    instance._on_damage = lambda *a: None
    return instance


class Recorder:
    """Serves bytes, but records every read size and refuses absurd ones."""

    def __init__(self, data):
        self.data, self.pos, self.sizes = data, 0, []

    def read(self, n):
        self.sizes.append(n)
        if n > 50_000_000:
            raise AssertionError(f"a {n}-byte read was issued")
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk


class RectangleBodyTest(unittest.TestCase):
    """Geometry is checked before the body is read, not after."""

    def update(self, rects, count=None):
        header = struct.pack("!xH", len(rects) if count is None else count)
        return header + b"".join(rects)

    def test_a_huge_raw_rectangle_is_refused_before_the_read(self):
        """Previously requested 17,179,344,900 bytes before _blit rejected it."""
        instance = client(1, 1)
        reader = Recorder(self.update(
            [struct.pack("!HHHHi", 0, 0, 65535, 65535, 0)]))
        instance._reader = reader
        with self.assertRaises(RFBError) as caught:
            instance._handle_framebuffer_update()
        self.assertIn("outside", str(caught.exception))
        self.assertTrue(all(size < 1000 for size in reader.sizes),
                        f"a large read was issued: {reader.sizes}")

    def test_an_oversized_compressed_body_is_refused_before_the_read(self):
        instance = client(64, 64)
        rect = struct.pack("!HHHHi", 0, 0, 64, 64, 16) + struct.pack("!I",
                                                                     0xFFFFFFFF)
        reader = Recorder(self.update([rect]))
        instance._reader = reader
        with self.assertRaises(RFBError) as caught:
            instance._handle_framebuffer_update()
        self.assertIn("compressed", str(caught.exception))
        self.assertTrue(all(size < 1000 for size in reader.sizes),
                        f"a large read was issued: {reader.sizes}")

    def test_the_compressed_limit_is_stated_in_the_error(self):
        self.assertGreater(MAX_COMPRESSED_BYTES, 1024 * 1024)


class DesktopSizeTest(unittest.TestCase):
    """The announced desktop is a `u16` pair whose product is an allocation."""

    def test_the_maximum_u16_desktop_is_refused(self):
        """65535x65535x4 is a 17GB bytearray asked for by four bytes."""
        instance = client(1, 1)
        with self.assertRaises(RFBError) as caught:
            instance._resize(65535, 65535)
        self.assertIn("beyond", str(caught.exception))

    def test_a_zero_dimension_is_refused(self):
        instance = client(1, 1)
        for width, height in ((0, 100), (100, 0)):
            with self.subTest(size=(width, height)):
                with self.assertRaises(RFBError):
                    instance._resize(width, height)

    def test_the_pixel_cap_catches_a_thin_enormous_desktop(self):
        """Both dimensions legal, product not: 16384x16384 is 1GB."""
        self.assertLess(MAX_PIXELS, MAX_DIMENSION * MAX_DIMENSION,
                        "the pixel cap must bind before the dimension cap")
        instance = client(1, 1)
        with self.assertRaises(RFBError):
            instance._resize(MAX_DIMENSION, MAX_DIMENSION)

    def test_a_real_retina_desktop_is_still_accepted(self):
        """The test Mac is 3420x2214; limits must not break real hardware."""
        instance = client(1, 1)
        instance._resize(3420, 2214)
        self.assertEqual(len(instance.framebuffer), 3420 * 2214 * BYTES_PER_PIXEL)

    def test_a_generous_multi_monitor_desktop_is_still_accepted(self):
        instance = client(1, 1)
        instance._resize(7680, 2160)  # two 4K monitors side by side
        self.assertEqual(instance.width, 7680)


class StringLengthTest(unittest.TestCase):
    """`u32` lengths on strings that are labels and sentences."""

    def test_an_absurd_desktop_name_is_refused_before_the_read(self):
        instance = RFBClient.__new__(RFBClient)
        instance._reader = Recorder(struct.pack("!I", 0xFFFFFFFF))
        with self.assertRaises(RFBError) as caught:
            length = struct.unpack("!I", instance._read(4))[0]
            if length > MAX_DESKTOP_NAME_BYTES:
                raise RFBError(f"server announced a {length}-byte desktop name")
        self.assertIn("desktop name", str(caught.exception))

    def test_an_absurd_failure_reason_does_not_end_the_world(self):
        """Reachable pre-authentication by anything answering on 5900."""
        instance = RFBClient.__new__(RFBClient)
        reader = Recorder(struct.pack("!I", 0xFFFFFFFF))
        instance._reader = reader
        reason = instance._read_failure_reason()
        self.assertIn("oversized", reason)
        self.assertTrue(all(size < 1000 for size in reader.sizes),
                        f"a large read was issued: {reader.sizes}")

    def test_a_normal_failure_reason_still_comes_through(self):
        instance = RFBClient.__new__(RFBClient)
        message = b"wrong password"
        instance._reader = Recorder(struct.pack("!I", len(message)) + message)
        self.assertEqual(instance._read_failure_reason(), "wrong password")


if __name__ == "__main__":
    unittest.main()
