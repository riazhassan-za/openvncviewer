"""Verifies the remote desktop scales to the window and that clicks map back."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QSize, Qt  # noqa: E402
from PySide6.QtGui import QImage, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from openvncviewer.ui import (WHEEL_CLICK_LIMIT, WHEEL_NOTCH,  # noqa: E402
                              WHEEL_SPEED_DEFAULT, WHEEL_SPEED_MAX,
                              WHEEL_SPEED_MIN, ConnectDialog, RemoteView)

REMOTE_W, REMOTE_H = 200, 120  # 5:3
BACKGROUND = (24, 24, 24)


class StubClient:
    """Just enough of RFBClient for the view: a framebuffer and event sinks."""

    def __init__(self, width, height, colour=(0x40, 0x80, 0xC0)):
        b, g, r = colour
        self.framebuffer = bytearray(bytes((b, g, r, 0xFF)) * (width * height))
        self.pointer_events = []

    def send_pointer(self, x, y, mask):
        self.pointer_events.append((x, y, mask))

    def send_key(self, keysym, down):
        pass


class ScalingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_view(self, width=REMOTE_W, height=REMOTE_H):
        view = RemoteView()
        client = StubClient(width, height)
        view.attach(client)
        view.on_resize(width, height)
        return view, client

    def test_size_hint_is_remote_size(self):
        view, _ = self.make_view()
        self.assertEqual(view.sizeHint(), QSize(REMOTE_W, REMOTE_H))

    def test_scales_up_and_down_preserving_aspect(self):
        view, _ = self.make_view()
        cases = {
            (400, 240): (0, 0, 400, 240),      # exact 2x
            (400, 400): (0, 80, 400, 240),     # letterboxed top and bottom
            (100, 120): (0, 30, 100, 60),      # scaled down, still 5:3
            (1000, 300): (250, 0, 500, 300),   # pillarboxed left and right
            (37, 91): (0, 34, 37, 22),         # awkward size, no crash
        }
        for (width, height), expected in cases.items():
            with self.subTest(window=(width, height)):
                view.resize(width, height)
                rect = view.target_rect()
                self.assertEqual(
                    (rect.x(), rect.y(), rect.width(), rect.height()), expected)
                self.assertLessEqual(rect.width(), width)
                self.assertLessEqual(rect.height(), height)

    def test_pointer_maps_back_through_the_scale(self):
        view, client = self.make_view()
        view.resize(800, 800)  # image drawn at 800x480, offset y=160
        for point, expected in (((0, 160), (0, 0)),
                                ((799, 639), (REMOTE_W - 1, REMOTE_H - 1)),
                                ((400, 400), (100, 60))):
            with self.subTest(point=point):
                client.pointer_events.clear()
                view._send_pointer(QPointF(*point))
                self.assertEqual(client.pointer_events[-1][:2], expected)

    def test_clicks_outside_the_image_clamp_into_range(self):
        view, client = self.make_view()
        view.resize(800, 800)
        view._send_pointer(QPointF(400, 10))  # in the letterbox above the image
        x, y, _ = client.pointer_events[-1]
        self.assertEqual((x, y), (100, 0))

    def test_painted_output_fills_the_expected_area(self):
        view, _ = self.make_view()
        view.resize(400, 400)
        target = QImage(400, 400, QImage.Format_RGB32)
        target.fill(0)
        view.render(target)

        def rgb(x, y):
            colour = target.pixelColor(x, y)
            return (colour.red(), colour.green(), colour.blue())

        self.assertEqual(rgb(200, 200), (0xC0, 0x80, 0x40))  # remote pixel, centre
        self.assertEqual(rgb(200, 10), BACKGROUND)           # letterbox above
        self.assertEqual(rgb(200, 390), BACKGROUND)          # letterbox below
        self.assertEqual(rgb(5, 200), (0xC0, 0x80, 0x40))    # image spans full width

    def test_framebuffer_writes_show_up_without_recreating_the_image(self):
        view, client = self.make_view()
        client.framebuffer[0:4] = bytes((0x11, 0x22, 0x33, 0xFF))
        self.assertEqual(view._image.pixel(0, 0) & 0xFFFFFF, 0x332211)


class WheelSpeedTest(unittest.TestCase):
    """The slider multiplies scroll clicks; the minimum must stay untouched."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_view(self, speed):
        view = RemoteView()
        client = StubClient(REMOTE_W, REMOTE_H)
        view.attach(client)
        view.on_resize(REMOTE_W, REMOTE_H)
        view.resize(REMOTE_W, REMOTE_H)
        view.wheel_speed = speed
        return view, client

    def test_minimum_setting_sends_raw_notches(self):
        view, _ = self.make_view(WHEEL_SPEED_MIN)
        for notches in (1, 2, 5):
            with self.subTest(notches=notches):
                self.assertEqual(view.wheel_clicks(WHEEL_NOTCH * notches),
                                 notches)

    def test_higher_settings_multiply(self):
        for speed in (2, 5, WHEEL_SPEED_DEFAULT, WHEEL_SPEED_MAX):
            view, _ = self.make_view(speed)
            with self.subTest(speed=speed):
                self.assertEqual(view.wheel_clicks(WHEEL_NOTCH), speed)
                self.assertEqual(view.wheel_clicks(WHEEL_NOTCH * 2), speed * 2)

    def test_ceiling_does_not_cap_the_top_of_the_slider(self):
        """The flood guard must catch runaway flicks, not the max setting."""
        view, _ = self.make_view(WHEEL_SPEED_MAX)
        # A few notches at the fastest setting must pass through unclipped.
        for notches in (1, 2, 3):
            with self.subTest(notches=notches):
                self.assertEqual(view.wheel_clicks(WHEEL_NOTCH * notches),
                                 WHEEL_SPEED_MAX * notches)
        self.assertGreater(WHEEL_CLICK_LIMIT, WHEEL_SPEED_MAX)

    def test_partial_notch_still_scrolls(self):
        # High-resolution trackpads report deltas smaller than a full notch.
        view, _ = self.make_view(WHEEL_SPEED_MIN)
        self.assertEqual(view.wheel_clicks(WHEEL_NOTCH // 4), 1)

    def test_a_fast_flick_cannot_flood_the_server(self):
        view, _ = self.make_view(WHEEL_SPEED_MAX)
        self.assertEqual(view.wheel_clicks(WHEEL_NOTCH * 1000),
                         WHEEL_CLICK_LIMIT)

    def test_default_sits_in_the_middle_of_the_slider(self):
        span = WHEEL_SPEED_MAX - WHEEL_SPEED_MIN
        offset = WHEEL_SPEED_DEFAULT - WHEEL_SPEED_MIN
        self.assertAlmostEqual(offset / span, 0.5, delta=0.02)

    def test_wheel_event_emits_press_and_release_per_click(self):
        view, client = self.make_view(3)
        event = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, 0),
                            QPoint(0, WHEEL_NOTCH), Qt.NoButton,
                            Qt.NoModifier, Qt.ScrollUpdate, False)
        view.wheelEvent(event)
        # 3 clicks, each a button-down then button-up pointer event.
        self.assertEqual(len(client.pointer_events), 6)
        self.assertEqual([mask for _, _, mask in client.pointer_events],
                         [8, 0, 8, 0, 8, 0])


class ConnectDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_slider_range_and_default(self):
        dialog = ConnectDialog()
        self.assertEqual(dialog.wheel_speed.minimum(), WHEEL_SPEED_MIN)
        self.assertEqual(dialog.wheel_speed.maximum(), WHEEL_SPEED_MAX)
        self.assertEqual(dialog.wheel_speed.value(), WHEEL_SPEED_DEFAULT)

    def test_values_round_trip_the_whole_form(self):
        dialog = ConnectDialog("mac.local", 5901, "someone", 7)
        dialog.password.setText("secret")
        self.assertEqual(dialog.values(),
                         ("mac.local", 5901, "someone", "secret", 7))


if __name__ == "__main__":
    unittest.main()
