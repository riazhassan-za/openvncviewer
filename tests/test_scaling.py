"""Verifies the remote desktop scales to the window and that clicks map back."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QSize, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QImage, QKeySequence, QMouseEvent,  # noqa: E402
                           QWheelEvent)
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

import openvncviewer.ui as ui_module  # noqa: E402
from openvncviewer import secretstore  # noqa: E402
from openvncviewer.history import ServerHistory  # noqa: E402
from openvncviewer.ui import (WHEEL_CLICK_LIMIT, WHEEL_NOTCH,  # noqa: E402
                              WHEEL_SPEED_DEFAULT, WHEEL_SPEED_MAX,
                              WHEEL_SPEED_MIN, DONATION_ADDRESS,
                              ConnectDialog, MainWindow, RemoteView)

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

    def test_detaching_clears_the_viewport(self):
        """A stale last frame after disconnect implies a session that is gone."""
        view, _ = self.make_view()
        view.resize(400, 400)
        view._rescale()
        self.assertIsNotNone(view._image)
        self.assertIsNotNone(view._scaled)

        view.detach()
        self.assertIsNone(view._image)
        self.assertIsNone(view._scaled)
        self.assertTrue(view.target_rect().isEmpty())

        target = QImage(400, 400, QImage.Format_RGB32)
        target.fill(0xFFFF0000)  # red, so leftover remote pixels would show
        view.render(target)
        for point in ((200, 200), (5, 5), (395, 395)):
            with self.subTest(point=point):
                colour = target.pixelColor(*point)
                self.assertEqual((colour.red(), colour.green(), colour.blue()),
                                 BACKGROUND, "remote image still on screen")

    def test_detaching_drops_held_buttons_and_keys(self):
        view, _ = self.make_view()
        view._pressed[Qt.Key_Shift] = 0xFFE1
        view._buttons = 1
        view.detach()
        self.assertEqual(view._pressed, {})
        self.assertEqual(view._buttons, 0)

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


class PartialRepaintTest(unittest.TestCase):
    """Damage drives a partial rescale; the result must still be correct."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_view(self, width=REMOTE_W, height=REMOTE_H):
        view = RemoteView()
        client = StubClient(width, height)
        view.attach(client)
        view.on_resize(width, height)
        view.resize(400, 400)
        return view, client

    def test_damage_repaints_only_the_affected_area(self):
        view, _ = self.make_view()
        view._rescale()  # prime the cache
        dirty = view._rescale((0, 0, 20, 12))
        target = view.target_rect()
        self.assertIsNotNone(dirty)
        # 20x12 of a 200x120 desktop is a tenth in each direction, so the
        # repaint must be far smaller than the drawn image.
        self.assertLess(dirty.width(), target.width() / 2)
        self.assertLess(dirty.height(), target.height() / 2)

    def test_partial_rescale_matches_a_full_rescale(self):
        """A patched region must look the same as rebuilding from scratch."""
        view, client = self.make_view()
        view._rescale()

        # Repaint a block in the framebuffer, then patch just that region.
        for y in range(0, 40):
            for x in range(0, 60):
                offset = (y * REMOTE_W + x) * 4
                client.framebuffer[offset:offset + 4] = bytes((9, 9, 200, 0xFF))
        view._rescale((0, 0, 60, 40))
        patched = view._scaled.toImage()

        view._scaled = None
        view._rescale()
        full = view._scaled.toImage()

        self.assertEqual(patched.size(), full.size())
        differing = sum(1 for y in range(0, full.height(), 3)
                        for x in range(0, full.width(), 3)
                        if patched.pixel(x, y) != full.pixel(x, y))
        self.assertEqual(differing, 0, "partial rescale diverged from a full one")

    def test_cache_is_never_used_at_a_stale_size(self):
        view, _ = self.make_view()
        view._rescale()
        self.assertEqual(view._scaled.size(), view.target_rect().size())

        # resizeEvent drops the cache eagerly, but a widget that was never
        # shown gets no such event - so the size check in _rescale, not the
        # event, is what has to guarantee this.
        view.resize(640, 480)
        view._rescale((0, 0, 10, 10))
        self.assertEqual(view._scaled.size(), view.target_rect().size())
        self.assertEqual(view._scaled.size(), QSize(640, 384))  # 5:3 letterboxed

    def test_paint_rebuilds_the_cache_on_demand(self):
        view, _ = self.make_view()
        self.assertIsNone(view._scaled)
        target = QImage(400, 400, QImage.Format_RGB32)
        target.fill(0)
        view.render(target)
        self.assertIsNotNone(view._scaled)
        colour = target.pixelColor(200, 200)
        self.assertEqual((colour.red(), colour.green(), colour.blue()),
                         (0xC0, 0x80, 0x40))


class PointerCoalescingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_view(self):
        view = RemoteView()
        client = StubClient(REMOTE_W, REMOTE_H)
        view.attach(client)
        view.on_resize(REMOTE_W, REMOTE_H)
        view.resize(REMOTE_W, REMOTE_H)
        return view, client

    @staticmethod
    def move(view, x, y):
        view.mouseMoveEvent(QMouseEvent(
            QEvent.Type.MouseMove, QPointF(x, y), QPointF(x, y),
            Qt.NoButton, Qt.NoButton, Qt.NoModifier))

    def test_a_burst_of_motion_sends_one_event_then_coalesces(self):
        view, client = self.make_view()
        for step in range(20):
            self.move(view, 10 + step, 10 + step)
        # First move goes immediately; the other 19 collapse into one pending.
        self.assertEqual(len(client.pointer_events), 1)
        self.assertEqual(client.pointer_events[0][:2], (10, 10))

    def test_the_latest_position_is_what_finally_gets_sent(self):
        view, client = self.make_view()
        for step in range(20):
            self.move(view, 10 + step, 10 + step)
        view._flush_motion()
        self.assertEqual(client.pointer_events[-1][:2], (29, 29))

    def test_idle_flush_stops_the_timer(self):
        view, _ = self.make_view()
        self.move(view, 5, 5)
        self.assertTrue(view._motion_timer.isActive())
        view._flush_motion()  # delivers the pending move
        view._flush_motion()  # nothing left, so the timer should stop
        self.assertFalse(view._motion_timer.isActive())

    def test_button_press_is_never_delayed_behind_a_pending_move(self):
        view, client = self.make_view()
        self.move(view, 10, 10)
        self.move(view, 80, 60)  # pending, not yet sent
        view.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(80, 60), QPointF(80, 60),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        x, y, mask = client.pointer_events[-1]
        self.assertEqual((x, y, mask), (80, 60, 1))
        self.assertIsNone(view._pending_motion)


class FullScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self):
        window = MainWindow()
        window.view.attach(StubClient(REMOTE_W, REMOTE_H))
        window.view.on_resize(REMOTE_W, REMOTE_H)
        return window

    def test_chrome_is_hidden_and_restored(self):
        window = self.make_window()
        window.show()
        self.assertTrue(window.menuBar().isVisible())

        window.fullscreen_action.setChecked(True)
        self.assertFalse(window.menuBar().isVisible())
        self.assertFalse(window.statusBar().isVisible())

        window.fullscreen_action.setChecked(False)
        self.assertTrue(window.menuBar().isVisible())
        self.assertTrue(window.statusBar().isVisible())
        window.close()

    def test_window_state_toggles(self):
        window = self.make_window()
        window.show()
        window.fullscreen_action.setChecked(True)
        self.assertTrue(window.isFullScreen())
        window.fullscreen_action.setChecked(False)
        self.assertFalse(window.isFullScreen())
        window.close()

    def test_leaving_full_screen_restores_a_maximized_window(self):
        window = self.make_window()
        window.showMaximized()
        window.fullscreen_action.setChecked(True)
        window.fullscreen_action.setChecked(False)
        self.assertTrue(window.isMaximized(),
                        "a maximized window came back as a normal one")
        window.close()

    def test_f11_is_the_shortcut_and_the_window_owns_it(self):
        window = self.make_window()
        self.assertEqual(window.fullscreen_action.shortcut(),
                         QKeySequence(Qt.Key_F11))
        # Owned by the window, not just the menu, or F11 would stop working
        # the moment the menu bar is hidden.
        self.assertIn(window.fullscreen_action, window.actions())
        window.close()

    def test_held_keys_are_released_on_toggle(self):
        """Otherwise a modifier held while toggling stays stuck on the Mac."""
        window = self.make_window()
        window.show()
        sent = []
        window.view._client.send_key = lambda keysym, down: sent.append(
            (keysym, down))
        window.view._pressed[Qt.Key_Shift] = 0xFFE1

        window.fullscreen_action.setChecked(True)
        self.assertIn((0xFFE1, False), sent)
        self.assertEqual(window.view._pressed, {})
        window.close()


BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def bech32_checksum_ok(address):
    """BIP-173 verification, written out here rather than trusting the string.

    A mistyped donation address is silent: it looks fine, and the money goes
    somewhere nobody can retrieve it from.
    """
    if address.lower() != address:
        return False
    separator = address.rfind("1")
    hrp, data = address[:separator], address[separator + 1:]
    if any(char not in BECH32_CHARSET for char in data):
        return False
    values = ([ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
              + [BECH32_CHARSET.index(c) for c in data])

    generator = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = (checksum & 0x1FFFFFF) << 5 ^ value
        for bit in range(5):
            checksum ^= generator[bit] if (top >> bit) & 1 else 0
    return checksum == 1


class AboutDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_donation_address_is_a_valid_bitcoin_address(self):
        self.assertTrue(DONATION_ADDRESS.startswith("bc1"),
                        "not a mainnet bech32 address")
        self.assertEqual(len(DONATION_ADDRESS), 42,
                         "wrong length for a P2WPKH address")
        self.assertTrue(bech32_checksum_ok(DONATION_ADDRESS),
                        "bech32 checksum failed - the address is mistyped")

    def test_about_shows_the_donation_notice_and_licence(self):
        window = MainWindow()
        shown = {}

        def capture(self_box):
            shown["text"] = self_box.text()
            return 0

        original = QMessageBox.exec
        QMessageBox.exec = capture
        try:
            window.show_about()
        finally:
            QMessageBox.exec = original

        text = shown.get("text", "")
        self.assertIn(DONATION_ADDRESS, text)
        self.assertIn(f"bitcoin:{DONATION_ADDRESS}", text, "address is not a link")
        self.assertIn("great justice", text)
        # The GPL asks interactive programs to carry a warranty notice.
        self.assertIn("NO WARRANTY", text)
        window.close()


class RecentServersTest(unittest.TestCase):
    """The dropdown, the linked Server name box, and clearing the list."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.history = ServerHistory(Path(self.directory.name) / "servers.json")
        self.history.remember("192.168.0.8", 5900, "someone", "Studio Mac")
        self.history.remember("vnc.example.com", 5901, "", "")

    def dialog(self, host=""):
        return ConnectDialog(host, 5900, "", WHEEL_SPEED_DEFAULT,
                             history=self.history)

    def test_dropdown_lists_recent_servers_newest_first(self):
        dialog = self.dialog()
        labels = [dialog.host.itemText(i) for i in range(dialog.host.count())]
        self.assertEqual(labels, ["vnc.example.com", "Studio Mac — 192.168.0.8"])

    def test_the_host_is_the_item_data_not_the_decorated_label(self):
        dialog = self.dialog()
        hosts = [dialog.host.itemData(i) for i in range(dialog.host.count())]
        self.assertEqual(hosts, ["vnc.example.com", "192.168.0.8"])

    def test_selecting_a_server_fills_its_details(self):
        dialog = self.dialog()
        dialog.host.setEditText("192.168.0.8")
        self.assertEqual(dialog.server_name.text(), "Studio Mac")
        self.assertEqual(dialog.port.value(), 5900)
        self.assertEqual(dialog.username.text(), "someone")

    def test_switching_servers_replaces_the_previous_details(self):
        """Stale username from the previous server would break a mixed setup."""
        dialog = self.dialog("192.168.0.8")
        self.assertEqual(dialog.username.text(), "someone")
        dialog.host.setEditText("vnc.example.com")
        self.assertEqual(dialog.server_name.text(), "")
        self.assertEqual(dialog.port.value(), 5901)
        self.assertEqual(dialog.username.text(), "")

    def test_an_unseen_host_clears_the_name(self):
        dialog = self.dialog("192.168.0.8")
        self.assertEqual(dialog.server_name.text(), "Studio Mac")
        dialog.host.setEditText("brand.new.host")
        self.assertEqual(dialog.server_name.text(), "")

    def test_picking_from_the_dropdown_puts_the_host_in_the_box(self):
        dialog = self.dialog()
        dialog.host.setCurrentIndex(1)
        dialog._history_selected(1)
        self.assertEqual(dialog.host.currentText(), "192.168.0.8")
        self.assertEqual(dialog.values()[0], "192.168.0.8")

    def test_values_include_the_server_name(self):
        dialog = self.dialog("192.168.0.8")
        dialog.password.setText("secret")
        (host, port, username, password, wheel, name, save,
         clipboard, alt_cmd, reconnect) = dialog.values()
        self.assertEqual((host, port, username, password, name),
                         ("192.168.0.8", 5900, "someone", "secret", "Studio Mac"))
        self.assertFalse(save, "nothing was saved for this server")
        self.assertTrue(clipboard, "clipboard sharing defaults on")

    def test_a_saved_password_is_restored_and_the_box_ticked(self):
        if not secretstore.available():
            self.skipTest("no encryption backend on this platform")
        self.history.remember("192.168.0.8", 5900, "someone", "Studio Mac",
                              password=secretstore.encrypt("hunter2"))
        dialog = self.dialog("192.168.0.8")
        self.assertEqual(dialog.password.text(), "hunter2")
        self.assertTrue(dialog.save_password.isChecked())

    def test_switching_to_a_server_without_a_saved_password_clears_it(self):
        if not secretstore.available():
            self.skipTest("no encryption backend on this platform")
        self.history.remember("192.168.0.8", 5900, "someone", "Studio Mac",
                              password=secretstore.encrypt("hunter2"))
        dialog = self.dialog("192.168.0.8")
        self.assertEqual(dialog.password.text(), "hunter2")

        dialog.host.setEditText("vnc.example.com")
        self.assertEqual(dialog.password.text(), "",
                         "the previous server's password stayed in the box")
        self.assertFalse(dialog.save_password.isChecked())

    def test_an_unreadable_token_is_treated_as_no_saved_password(self):
        """A blob written by another user or machine must not half-fill the form."""
        self.history.remember("192.168.0.8", 5900, "someone", "Studio Mac",
                              password="AQAAgibberish==")
        dialog = self.dialog("192.168.0.8")
        self.assertEqual(dialog.password.text(), "")
        self.assertFalse(dialog.save_password.isChecked())

    def test_ticking_the_box_stores_an_encrypted_token_not_the_password(self):
        if not secretstore.available():
            self.skipTest("no encryption backend on this platform")
        window = self.make_window()
        window.connect_to("new.host", 5900, "user", "hunter2",
                          WHEEL_SPEED_DEFAULT, "Fresh", save_password=True)
        window._on_resize(REMOTE_W, REMOTE_H)

        entry = self.history.find("new.host")
        self.assertTrue(entry["password"], "nothing was saved")
        self.assertNotIn("hunter2", entry["password"])
        self.assertNotIn("hunter2",
                         self.history.path.read_text(encoding="utf-8"))
        self.assertEqual(secretstore.decrypt(entry["password"]), "hunter2")

    def test_leaving_the_box_unticked_stores_no_password(self):
        window = self.make_window()
        window.connect_to("new.host", 5900, "user", "hunter2",
                          WHEEL_SPEED_DEFAULT, "Fresh", save_password=False)
        window._on_resize(REMOTE_W, REMOTE_H)

        self.assertEqual(self.history.find("new.host")["password"], "")
        self.assertNotIn("hunter2",
                         self.history.path.read_text(encoding="utf-8"))

    def test_unticking_later_forgets_a_previously_saved_password(self):
        if not secretstore.available():
            self.skipTest("no encryption backend on this platform")
        window = self.make_window()
        window.connect_to("new.host", 5900, "user", "hunter2",
                          WHEEL_SPEED_DEFAULT, "Fresh", save_password=True)
        window._on_resize(REMOTE_W, REMOTE_H)
        self.assertTrue(self.history.find("new.host")["password"])

        window.connect_to("new.host", 5900, "user", "hunter2",
                          WHEEL_SPEED_DEFAULT, "Fresh", save_password=False)
        window._on_resize(REMOTE_W, REMOTE_H)
        self.assertEqual(self.history.find("new.host")["password"], "",
                         "unticking did not forget the stored password")

    def test_the_buttons_read_connect_cancel_remove_in_that_order(self):
        dialog = self.dialog()
        self.assertEqual(dialog.connect_button.text(), "Connect")
        self.assertEqual(dialog.remove_button.text(), "Remove Server")
        self.assertTrue(dialog.connect_button.isDefault(),
                        "Enter should still connect")

        outer = dialog.layout()
        row = next((outer.itemAt(i).layout() for i in range(outer.count())
                    if outer.itemAt(i).layout() is not None
                    and outer.itemAt(i).layout().indexOf(dialog.remove_button) >= 0),
                   None)
        self.assertIsNotNone(row, "the buttons are not laid out in a row")
        order = [row.itemAt(i).widget().text() for i in range(row.count())
                 if row.itemAt(i).widget() is not None]
        self.assertEqual(order, ["Connect", "Cancel", "Remove Server"])

    def test_remove_takes_only_the_selected_server(self):
        dialog = self.dialog()
        dialog.host.setEditText("192.168.0.8")
        dialog.remove_selected()

        self.assertEqual(self.history.hosts(), ["vnc.example.com"],
                         "removed more than the selected server")
        self.assertEqual(dialog.host.count(), 1)

    def test_removing_repeatedly_empties_the_list(self):
        """Three servers should take three clicks, not one."""
        self.history.remember("third.host", 5900, "", "Third")
        # Opened on the newest server, as it is when reached from a session.
        dialog = self.dialog("third.host")
        self.assertEqual(dialog.host.count(), 3)

        for expected_remaining in (2, 1, 0):
            dialog.remove_selected()
            self.assertEqual(dialog.host.count(), expected_remaining)
        self.assertEqual(self.history.entries(), [])

    def test_remove_does_nothing_when_the_box_is_empty(self):
        """Opened with no host chosen, there is nothing selected to remove."""
        dialog = self.dialog()
        self.assertEqual(dialog.host.currentText(), "")
        self.assertFalse(dialog.remove_button.isEnabled())
        dialog.remove_selected()
        self.assertEqual(len(self.history.entries()), 2)

    def test_removing_lands_on_the_next_server(self):
        dialog = self.dialog()
        dialog.host.setEditText("vnc.example.com")  # the newest
        dialog.remove_selected()
        self.assertEqual(dialog.host.currentText(), "192.168.0.8")
        self.assertEqual(dialog.server_name.text(), "Studio Mac",
                         "details did not follow the newly selected server")

    def test_remove_is_disabled_for_a_host_not_in_the_list(self):
        dialog = self.dialog("192.168.0.8")
        self.assertTrue(dialog.remove_button.isEnabled())
        dialog.host.setEditText("brand.new.host")
        self.assertFalse(dialog.remove_button.isEnabled())

    def test_removing_the_last_server_leaves_an_empty_box(self):
        self.history.remove("vnc.example.com")
        dialog = self.dialog("192.168.0.8")
        dialog.remove_selected()
        self.assertEqual(dialog.host.count(), 0)
        self.assertEqual(dialog.host.currentText(), "")
        self.assertFalse(dialog.remove_button.isEnabled())

    def make_window(self):
        """A window whose connect_to opens no socket.

        MainWindow.connect_to starts a real RFBClient; left alone the tests
        below would block on a DNS lookup for a host that does not exist.
        """
        class DummyClient:
            def __init__(self, *args, **kwargs):
                self.desktop_name = "Stub desktop"
                self.width, self.height = REMOTE_W, REMOTE_H
                self.framebuffer = bytearray(REMOTE_W * REMOTE_H * 4)

            def start(self):
                pass

            def stop(self):
                pass

            def send_clipboard(self, text):
                return True

        original = ui_module.RFBClient
        ui_module.RFBClient = DummyClient
        self.addCleanup(setattr, ui_module, "RFBClient", original)

        window = MainWindow()
        window.history = self.history
        self.addCleanup(window.close)
        return window

    def test_a_server_is_remembered_only_after_it_connects(self):
        window = self.make_window()
        window.connect_to("new.host", 5900, "user", "pw", WHEEL_SPEED_DEFAULT,
                          "Fresh")
        self.assertIsNone(self.history.find("new.host"),
                          "remembered before the session came up")

        window._on_resize(REMOTE_W, REMOTE_H)
        entry = self.history.find("new.host")
        self.assertIsNotNone(entry, "not remembered after connecting")
        self.assertEqual(entry["name"], "Fresh")
        self.assertEqual(entry["username"], "user")

    def test_a_failed_connection_is_not_remembered(self):
        window = self.make_window()
        # _on_disconnect raises a modal warning, which blocks forever offscreen.
        original = QMessageBox.warning
        QMessageBox.warning = staticmethod(lambda *args, **kwargs: None)
        self.addCleanup(setattr, QMessageBox, "warning", original)

        window.connect_to("bad.host", 5900, "", "", WHEEL_SPEED_DEFAULT, "Nope")
        window._on_disconnect("connection refused")
        self.assertIsNone(self.history.find("bad.host"))

    def test_sidebar_connection_decrypts_the_saved_password(self):
        """The sidebar must not send History's encrypted token as a password."""
        token = "encrypted-password-token"
        self.history.remember("panel.example", 5900, "amy", "Panel Mac",
                              password=token)
        window = self.make_window()
        calls = []
        window.connect_to = lambda *args, **kwargs: calls.append((args, kwargs))

        original = ui_module.secretstore.decrypt
        ui_module.secretstore.decrypt = lambda value: "hunter2" if value == token else None
        self.addCleanup(setattr, ui_module.secretstore, "decrypt", original)

        window._on_panel_connect_requested("panel.example", 5900, "Panel Mac")

        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args[:4], ("panel.example", 5900, "amy", "hunter2"))
        self.assertTrue(kwargs["save_password"])

    def test_sidebar_edit_opens_the_full_connect_dialog(self):
        self.history.remember(
            "panel.example", 5901, "amy", "Panel Mac",
            wheel_speed=5, share_clipboard=False, alt_is_command=False,
            auto_reconnect=False,
        )
        window = self.make_window()
        created = []

        class FakeDialog:
            def __init__(self, *args, **kwargs):
                created.append((args, kwargs))

            def exec(self):
                return QDialog.Rejected

        original = ui_module.ConnectDialog
        ui_module.ConnectDialog = FakeDialog
        self.addCleanup(setattr, ui_module, "ConnectDialog", original)

        window._on_panel_edit_requested("panel.example", 5901)

        self.assertEqual(len(created), 1)
        args, kwargs = created[0]
        self.assertEqual(args, ("panel.example", 5901, "amy", 5,
                                False, False, False))
        self.assertIs(kwargs["history"], self.history)
        self.assertIs(kwargs["parent"], window)

    def test_sidebar_new_opens_an_empty_connect_dialog(self):
        window = self.make_window()
        created = []

        class FakeDialog:
            def __init__(self, *args, **kwargs):
                created.append((args, kwargs))

            def exec(self):
                return QDialog.Rejected

        original = ui_module.ConnectDialog
        ui_module.ConnectDialog = FakeDialog
        self.addCleanup(setattr, ui_module, "ConnectDialog", original)

        window._on_panel_add_requested()

        self.assertEqual(len(created), 1)
        args, kwargs = created[0]
        self.assertEqual(args, ())
        self.assertIs(kwargs["history"], self.history)
        self.assertIs(kwargs["parent"], window)

    def test_sidebar_connection_fills_in_a_missing_wheel_speed(self):
        """A server saved before wheel speeds existed has None, not a number."""
        self.history.remember("old.mac", 5900, "amy")
        self.history.remember("old.vnc", 5900)
        window = self.make_window()
        calls = []
        window.connect_to = lambda *args, **kwargs: calls.append(kwargs)

        window._on_panel_connect_requested("old.mac", 5900, "")
        window._on_panel_connect_requested("old.vnc", 5900, "")

        self.assertEqual(calls[0]["wheel_speed"], WHEEL_SPEED_DEFAULT)
        self.assertEqual(calls[1]["wheel_speed"], ui_module.WHEEL_SPEED_RAW)

    def test_sidebar_connection_keeps_the_saved_auto_reconnect(self):
        self.history.remember("panel.example", 5900, "amy",
                              auto_reconnect=False)
        window = self.make_window()
        calls = []
        window.connect_to = lambda *args, **kwargs: calls.append(kwargs)

        window._on_panel_connect_requested("panel.example", 5900, "")

        self.assertFalse(calls[0]["auto_reconnect"])

    def test_sidebar_edit_opens_for_a_server_with_no_wheel_speed(self):
        self.history.remember("old.mac", 5900, "amy")
        window = self.make_window()
        dialogs = []
        original = ui_module.ConnectDialog

        class RejectingDialog(original):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                dialogs.append(self)

            def exec(self):
                return QDialog.Rejected

        ui_module.ConnectDialog = RejectingDialog
        self.addCleanup(setattr, ui_module, "ConnectDialog", original)

        window._on_panel_edit_requested("old.mac", 5900)

        self.assertEqual(dialogs[0].wheel_speed.value(), WHEEL_SPEED_DEFAULT)

    def sidebar_hosts(self, window):
        panel = window.host_panel
        return [panel.list_widget.item(index).data(Qt.UserRole)["host"]
                for index in range(panel.list_widget.count())]

    def test_sidebar_lists_a_server_once_it_connects(self):
        window = self.make_window()
        window.host_panel.history = self.history
        window.host_panel.refresh_list()

        window.connect_to("new.host", 5900, "", "")
        window._on_resize(REMOTE_W, REMOTE_H)

        self.assertIn("new.host", self.sidebar_hosts(window))

    def test_sidebar_drops_a_server_removed_in_the_connect_dialog(self):
        window = self.make_window()
        window.host_panel.history = self.history
        window.host_panel.refresh_list()
        self.assertIn("192.168.0.8", self.sidebar_hosts(window))
        history = self.history

        class RemovingDialog:
            def exec(self):
                history.remove("192.168.0.8")
                return QDialog.Rejected

        window._connect_from_dialog(RemovingDialog())

        self.assertNotIn("192.168.0.8", self.sidebar_hosts(window))

    def test_sidebar_is_alphabetical_unless_sorting_by_last_used(self):
        self.history.remember("alpha.host", 5900)  # now the most recent
        window = self.make_window()
        window.host_panel.history = self.history
        window.host_panel.refresh_list()

        # Sorted on what is shown: "Studio Mac", not its address.
        self.assertEqual(self.sidebar_hosts(window),
                         ["alpha.host", "192.168.0.8", "vnc.example.com"])

        window.host_panel.sort_by_last_used.setChecked(True)
        self.assertEqual(self.sidebar_hosts(window), self.history.hosts())

    def test_sidebar_collapse_keeps_the_menu_in_step(self):
        window = self.make_window()
        panel = window.host_panel

        panel.collapse_btn.click()
        self.assertTrue(panel.body.isHidden())
        self.assertFalse(panel.expand_btn.isHidden())
        self.assertFalse(window.toggle_sidebar_action.isChecked())

        window.toggle_sidebar_action.trigger()
        self.assertFalse(panel.body.isHidden())
        self.assertTrue(panel.expand_btn.isHidden())

    def test_sidebar_takes_a_fifth_of_the_window_until_collapsed(self):
        window = self.make_window()
        window.resize(1280, 800)
        window.show()
        QApplication.processEvents()
        self.assertAlmostEqual(window.host_panel.width() / 1280, 0.2,
                               delta=0.01)

        window.host_panel.set_collapsed(True)
        QApplication.processEvents()
        self.assertEqual(window.host_panel.width(),
                         window.host_panel.expand_btn.width())

    def test_sidebar_reopens_at_the_width_it_was_dragged_to(self):
        window = self.make_window()
        window.resize(1280, 800)
        window.show()
        QApplication.processEvents()

        window.splitter.moveSplitter(500, 1)
        window._remember_sidebar_width()  # splitterMoved fires only for a mouse drag
        window.host_panel.set_collapsed(True)
        window.host_panel.set_collapsed(False)
        QApplication.processEvents()

        self.assertEqual(window.host_panel.width(), 500)

    def test_sidebar_disconnect_uses_the_normal_disconnect_handler(self):
        window = self.make_window()
        calls = []
        window.disconnect = lambda: calls.append(True)

        window._on_panel_disconnect_requested()

        self.assertEqual(calls, [True])

    def test_the_server_name_reaches_the_window_title_and_status(self):
        window = self.make_window()
        window.connect_to("192.168.0.8", 5900, "someone", "pw",
                          WHEEL_SPEED_DEFAULT, "Studio Mac")
        window._on_resize(REMOTE_W, REMOTE_H)
        self.assertIn("Studio Mac", window.windowTitle())
        self.assertIn("Studio Mac", window.status.text())

    def test_sidebar_highlights_the_connected_host_and_clears_on_disconnect(self):
        window = self.make_window()
        window.host_panel.history = self.history
        window.host_panel.refresh_list()

        window.connect_to("192.168.0.8", 5900, "someone", "pw",
                          WHEEL_SPEED_DEFAULT, "Studio Mac")
        window._on_resize(REMOTE_W, REMOTE_H)

        item = next(window.host_panel.list_widget.item(index)
                    for index in range(window.host_panel.list_widget.count())
                    if window.host_panel.list_widget.item(index).data(Qt.UserRole)["host"]
                    == "192.168.0.8")
        self.assertEqual(item.background().color(), QColor("#d0e8ff"))

        window.disconnect()
        self.assertEqual(item.background().color(), QColor("white"))

    def test_disconnecting_resets_the_title_and_status(self):
        window = self.make_window()
        window.connect_to("192.168.0.8", 5900, "someone", "pw",
                          WHEEL_SPEED_DEFAULT, "Studio Mac")
        window._on_resize(REMOTE_W, REMOTE_H)
        self.assertIn("Studio Mac", window.windowTitle())

        window.disconnect()
        self.assertEqual(window.windowTitle(), "OpenVNCViewer")
        self.assertEqual(window.status.text(), "Not connected")
        self.assertIsNone(window.view._image, "viewport still holds a frame")

    def test_a_dropped_session_also_resets_the_title(self):
        original = QMessageBox.warning
        QMessageBox.warning = staticmethod(lambda *args, **kwargs: None)
        self.addCleanup(setattr, QMessageBox, "warning", original)

        window = self.make_window()
        window.connect_to("192.168.0.8", 5900, "someone", "pw",
                          WHEEL_SPEED_DEFAULT, "Studio Mac")
        window._on_resize(REMOTE_W, REMOTE_H)
        window._on_disconnect("connection closed by server")

        self.assertEqual(window.windowTitle(), "OpenVNCViewer")
        self.assertIn("Disconnected", window.status.text())
        self.assertIsNone(window.view._image)

    def test_an_unnamed_server_falls_back_to_the_desktop_name(self):
        window = self.make_window()
        window.connect_to("vnc.example.com", 5901, "", "", WHEEL_SPEED_DEFAULT,
                          "")
        window._on_resize(REMOTE_W, REMOTE_H)
        self.assertIn("Stub desktop", window.windowTitle())


class ConnectDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # An empty history in a temporary directory, so these never read or
        # write the real config file.
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.history = ServerHistory(Path(self.directory.name) / "servers.json")

    def test_slider_range_and_default(self):
        dialog = ConnectDialog(history=self.history)
        self.assertEqual(dialog.wheel_speed.minimum(), WHEEL_SPEED_MIN)
        self.assertEqual(dialog.wheel_speed.maximum(), WHEEL_SPEED_MAX)
        self.assertEqual(dialog.wheel_speed.value(), WHEEL_SPEED_DEFAULT)

    def test_values_round_trip_the_whole_form(self):
        dialog = ConnectDialog("mac.local", 5901, "someone", 7,
                               history=self.history)
        dialog.password.setText("secret")
        dialog.server_name.setText("Studio")
        self.assertEqual(
            dialog.values(),
            ("mac.local", 5901, "someone", "secret", 7, "Studio", False,
             True, True, True))


if __name__ == "__main__":
    unittest.main()
