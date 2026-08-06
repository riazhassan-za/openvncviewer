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
"""Paint-path benchmark: full rescale versus the cached partial rescale.

    python benchmarks/paint.py

Compares what the viewer used to do on every framebuffer update - smooth-scale
the entire remote desktop - against rescaling only the damaged region into a
cached pixmap, at the 3420x2214 resolution the project is tested against.
"""

import os
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from openvncviewer.ui import RemoteView  # noqa: E402

REMOTE_W, REMOTE_H = 3420, 2214
WINDOW_W, WINDOW_H = 1280, 800
REPEATS = 20


class FakeClient:
    def __init__(self, width, height):
        self.framebuffer = bytearray(bytes((0x40, 0x80, 0xC0, 0xFF))
                                     * (width * height))

    def send_pointer(self, x, y, mask):
        pass

    def send_key(self, keysym, down):
        pass


def time_it(work, repeats=REPEATS):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        work()
        samples.append(time.perf_counter() - start)
    return min(samples), statistics.median(samples)


def report(name, best, median):
    rate = 1 / best if best else float("inf")
    print(f"{name:<34} {best * 1000:7.2f} ms best  "
          f"{median * 1000:7.2f} ms median  {rate:7.0f} fps ceiling")


def main():
    app = QApplication.instance() or QApplication([])  # noqa: F841
    view = RemoteView()
    view.attach(FakeClient(REMOTE_W, REMOTE_H))
    view.on_resize(REMOTE_W, REMOTE_H)
    view.resize(WINDOW_W, WINDOW_H)

    print(f"Remote {REMOTE_W}x{REMOTE_H} into a {WINDOW_W}x{WINDOW_H} window, "
          f"{REPEATS} repeats\n")

    def full():
        view._scaled = None
        view._rescale()

    best, median = time_it(full)
    report("full rescale (every frame)", best, median)

    view._rescale()  # prime the cache

    for label, region in (("damage 64x64 (a tile)", (100, 100, 64, 64)),
                          ("damage 400x300 (a window)", (100, 100, 400, 300)),
                          ("damage 1710x1107 (quarter)", (0, 0, 1710, 1107))):
        best, median = time_it(lambda r=region: view._rescale(r))
        report(label, best, median)


if __name__ == "__main__":
    main()
