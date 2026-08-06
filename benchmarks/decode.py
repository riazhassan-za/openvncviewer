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
"""Decoder benchmark, so performance claims can be checked rather than trusted.

    python benchmarks/decode.py

Reports megapixels per second for each ZRLE tile subencoding and for a
full-screen raw blit, at the 3420x2214 resolution the project is tested
against.
"""

import statistics
import sys
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from openvncviewer.rfb import BYTES_PER_PIXEL, RFBClient  # noqa: E402
from test_protocol import (encode_packed_palette, encode_palette_rle,  # noqa: E402
                           encode_plain_rle, encode_raw, encode_solid)

WIDTH, HEIGHT = 3420, 2214
REPEATS = 5


def make_client(width, height):
    client = RFBClient.__new__(RFBClient)
    client.width = width
    client.height = height
    client.framebuffer = bytearray(width * height * BYTES_PER_PIXEL)
    client._zrle = zlib.decompressobj()
    return client


def build_rect(encoder, width, height):
    """One ZRLE rect of the given size, every tile using the same subencoding."""
    stream = bytearray()
    for tile_y in range(0, height, 64):
        th = min(64, height - tile_y)
        for tile_x in range(0, width, 64):
            tw = min(64, width - tile_x)
            encoder(stream, tw, th)
    return bytes(stream)


def time_it(work, repeats=REPEATS):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        work()
        samples.append(time.perf_counter() - start)
    return min(samples), statistics.median(samples)


def bench_zrle(encoder, width, height):
    payload = build_rect(encoder, width, height)
    client = make_client(WIDTH, HEIGHT)

    def work():
        client._zrle = zlib.decompressobj()  # fresh stream per run
        client._decode_zrle(0, 0, width, height, zlib.compress(payload))

    return time_it(work)


def bench_raw(width, height):
    client = make_client(WIDTH, HEIGHT)
    pixels = bytes(width * height * BYTES_PER_PIXEL)
    return time_it(lambda: client._blit(0, 0, width, height, pixels))


def report(name, best, median, pixels):
    megapixels = pixels / 1e6
    print(f"{name:<22} {best * 1000:8.1f} ms best  "
          f"{median * 1000:8.1f} ms median  {megapixels / best:7.1f} Mpx/s")


def main():
    print(f"Framebuffer {WIDTH}x{HEIGHT}, {REPEATS} repeats, best of run\n")

    # A quarter of the screen is a realistic single update; full screen is the
    # worst case on connect or a full redraw.
    zrle_w, zrle_h = 1712, 1108
    pixels = zrle_w * zrle_h
    for name, encoder in (("zrle raw", encode_raw),
                          ("zrle solid", encode_solid),
                          ("zrle packed palette", encode_packed_palette),
                          ("zrle plain rle", encode_plain_rle),
                          ("zrle palette rle", encode_palette_rle)):
        best, median = bench_zrle(encoder, zrle_w, zrle_h)
        report(name, best, median, pixels)

    best, median = bench_raw(WIDTH, HEIGHT)
    report("raw full screen", best, median, WIDTH * HEIGHT)


if __name__ == "__main__":
    main()
