"""Rendering at Windows display scaling above 100%.

Run in a separate process from the rest of the suite: Qt reads QT_SCALE_FACTOR
once, when the QApplication is created, so a scale factor cannot be changed
part-way through a run.
"""

import os
import subprocess
import sys
import textwrap
import unittest

REMOTE_W, REMOTE_H = 200, 120  # 5:3

# Runs inside the child, which owns its own QApplication at a chosen scale.
PROBE = textwrap.dedent("""
    import os, sys
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = sys.argv[1]
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from openvncviewer.ui import RemoteView

    W, H = 200, 120

    class Stub:
        def __init__(self):
            self.framebuffer = bytearray(bytes((0x40, 0x80, 0xC0, 0xFF)) * (W * H))
            self.sent = []
        def send_pointer(self, x, y, mask): self.sent.append((x, y))
        def send_key(self, keysym, down): pass

    app = QApplication([])
    view = RemoteView()
    client = Stub()
    view.attach(client)
    view.on_resize(W, H)
    view.resize(400, 400)
    view._rescale()

    ratio = view.devicePixelRatioF()
    target = view.target_rect()
    physical_w = round(view.width() * ratio)

    # Pointer: the centre of the drawn image must be the centre of the desktop.
    view._send_pointer(QPointF(target.x() + target.width() / 2,
                               target.y() + target.height() / 2))
    px, py = client.sent[-1]

    # Render and sample, in logical coordinates scaled to the buffer.
    image = QImage(round(400 * ratio), round(400 * ratio), QImage.Format_RGB32)
    image.setDevicePixelRatio(ratio)
    image.fill(0xFFFF0000)
    view.render(image)
    def rgb(x, y):
        c = image.pixelColor(round(x * ratio), round(y * ratio))
        return (c.red(), c.green(), c.blue())

    print(repr({
        "ratio": ratio,
        "pixmap_w": view._scaled.width(),
        "pixmap_dpr": view._scaled.devicePixelRatio(),
        "physical_w": physical_w,
        "pointer": (px, py),
        "centre": rgb(200, 200),
        "above": rgb(200, 10),
        "below": rgb(200, 390),
    }))
""")


def probe(scale):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    environment = dict(os.environ, PYTHONPATH=os.path.join(root, "src"))
    result = subprocess.run([sys.executable, "-c", PROBE, scale],
                            capture_output=True, text=True, timeout=180,
                            env=environment)
    if result.returncode != 0:
        raise AssertionError(f"probe failed at scale {scale}:\n{result.stderr}")
    return eval(result.stdout.strip().splitlines()[-1])


class HiDpiTest(unittest.TestCase):
    """Scaling is the headline feature, so it has to hold up on a scaled display."""

    def test_the_cache_is_allocated_in_device_pixels(self):
        """A logical-sized pixmap gets stretched, and the desktop arrives soft."""
        for scale in ("1", "1.5", "2"):
            with self.subTest(scale=scale):
                result = probe(scale)
                coverage = result["pixmap_w"] / result["physical_w"]
                self.assertGreater(
                    coverage, 0.9,
                    f"pixmap covers only {coverage:.0%} of the physical width "
                    f"at {scale}x; the image would be upscaled")
                self.assertAlmostEqual(result["pixmap_dpr"], float(scale),
                                       places=3)

    def test_clicks_still_land_where_they_are_pointed(self):
        for scale in ("1", "1.5", "2"):
            with self.subTest(scale=scale):
                x, y = probe(scale)["pointer"]
                self.assertAlmostEqual(x, REMOTE_W // 2, delta=2)
                self.assertAlmostEqual(y, REMOTE_H // 2, delta=2)

    def test_the_image_is_not_drawn_at_the_wrong_size(self):
        """A mismatched ratio shows as misplaced content, never as an error."""
        for scale in ("1", "1.5", "2"):
            with self.subTest(scale=scale):
                result = probe(scale)
                self.assertEqual(result["centre"], (0xC0, 0x80, 0x40))
                self.assertEqual(result["above"], (24, 24, 24))
                self.assertEqual(result["below"], (24, 24, 24))


if __name__ == "__main__":
    unittest.main()
