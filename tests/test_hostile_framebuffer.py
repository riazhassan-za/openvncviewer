"""Framebuffer writes a hostile server can reach.

The framebuffer is a `bytearray` that QImage wraps zero-copy, so its *length*
is an invariant, not an implementation detail: slice assignment past the end
grows it and a short assignment shrinks it, either of which reallocates the
buffer out from under a live native pointer.

`_blit` has guarded the geometry since 0.6.0. These cover the two ways found
since of reaching the same sink around that guard - a truncated ZRLE tile
returning fewer pixels than it claims, and CopyRect writing to an unchecked
destination - plus the checked reads that now stop truncation earlier.
"""

import struct
import unittest
import zlib

from openvncviewer.rfb import BYTES_PER_PIXEL, RFBClient, RFBError


def client(width, height):
    """A client with a framebuffer and no socket."""
    instance = RFBClient.__new__(RFBClient)
    instance.width, instance.height = width, height
    instance.framebuffer = bytearray(width * height * BYTES_PER_PIXEL)
    instance._zrle = zlib.decompressobj()
    instance._running = True
    return instance


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def read(self, n):
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk


class TruncatedZRLETest(unittest.TestCase):
    """A tile that declares more pixels than it carries must not be accepted."""

    def assert_framebuffer_intact(self, instance, size, action):
        with self.assertRaises(RFBError):
            action()
        self.assertEqual(len(instance.framebuffer), size,
                         "framebuffer was resized by a rejected tile")

    def test_solid_tile_without_its_colour_is_rejected(self):
        """Subencoding 1 promising a colour and sending none took 16384 -> 10240."""
        instance = client(64, 64)
        self.assert_framebuffer_intact(
            instance, 64 * 64 * BYTES_PER_PIXEL,
            lambda: instance._decode_zrle(0, 0, 64, 64, zlib.compress(b"\x01")))

    def test_every_subencoding_truncated_to_one_byte_is_rejected(self):
        """1-16 all returned successfully before, every one resizing the buffer."""
        for sub in range(256):
            with self.subTest(subencoding=sub):
                instance = client(8, 8)
                size = len(instance.framebuffer)
                with self.assertRaises(RFBError):
                    instance._decode_zrle(0, 0, 8, 8,
                                          zlib.compress(bytes([sub])))
                self.assertEqual(len(instance.framebuffer), size)

    def test_packed_palette_with_a_complete_palette_but_no_rows_is_rejected(self):
        """The palette reads cleanly; the row data behind it is missing."""
        instance = client(8, 8)
        body = bytes([2]) + b"\x01\x02\x03" + b"\x04\x05\x06"  # 2 colours, 0 rows
        size = len(instance.framebuffer)
        with self.assertRaises(RFBError):
            instance._decode_zrle(0, 0, 8, 8, zlib.compress(body))
        self.assertEqual(len(instance.framebuffer), size)

    def test_a_short_payload_is_refused_by_blit_itself(self):
        """The invariant is enforced at the sink, not only by its callers."""
        instance = client(4, 4)
        size = len(instance.framebuffer)
        with self.assertRaises(RFBError) as caught:
            instance._blit(0, 0, 2, 2, b"\x00" * 4)  # needs 16 bytes
        self.assertIn("16 bytes", str(caught.exception))
        self.assertEqual(len(instance.framebuffer), size)

    def test_a_valid_solid_tile_still_decodes(self):
        """The guard must not cost correctness on well-formed input."""
        instance = client(8, 8)
        instance._decode_zrle(0, 0, 8, 8,
                              zlib.compress(bytes([1]) + b"\x0a\x0b\x0c"))
        self.assertEqual(len(instance.framebuffer), 8 * 8 * BYTES_PER_PIXEL)
        self.assertEqual(bytes(instance.framebuffer[:4]), b"\x0a\x0b\x0c\xff")


class CopyRectDestinationTest(unittest.TestCase):
    """CopyRect validated its source and wrote wherever it was told."""

    def copy(self, instance, dest_x, dest_y, w, h, src=(0, 0)):
        instance._reader = Reader(struct.pack("!HH", *src))
        instance._copy_rect(dest_x, dest_y, w, h)

    def test_destination_past_the_end_is_rejected(self):
        """Previously grew a 64-byte framebuffer to 68."""
        instance = client(4, 4)
        with self.assertRaises(RFBError) as caught:
            self.copy(instance, 65535, 0, 1, 1)
        self.assertIn("destination", str(caught.exception))
        self.assertEqual(len(instance.framebuffer), 64)

    def test_destination_overlapping_the_edge_is_rejected(self):
        """Not just absurd values: one pixel over is still out of bounds."""
        instance = client(4, 4)
        with self.assertRaises(RFBError):
            self.copy(instance, 3, 3, 2, 2)
        self.assertEqual(len(instance.framebuffer), 64)

    def test_source_past_the_end_is_still_rejected(self):
        instance = client(4, 4)
        with self.assertRaises(RFBError) as caught:
            self.copy(instance, 0, 0, 2, 2, src=(3, 3))
        self.assertIn("source", str(caught.exception))

    def test_a_legitimate_copy_still_works(self):
        instance = client(4, 4)
        instance.framebuffer[0:4] = b"\x01\x02\x03\xff"
        self.copy(instance, 2, 0, 1, 1, src=(0, 0))
        self.assertEqual(bytes(instance.framebuffer[8:12]), b"\x01\x02\x03\xff")
        self.assertEqual(len(instance.framebuffer), 64)


if __name__ == "__main__":
    unittest.main()
