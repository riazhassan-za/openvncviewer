"""Modifier keys reaching the remote instead of the local menu bar."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from openvncviewer.ui import ALT_L, SUPER_L, RemoteView  # noqa: E402

REMOTE_W, REMOTE_H = 200, 120


class Stub:
    def __init__(self):
        self.framebuffer = bytearray(REMOTE_W * REMOTE_H * 4)
        self.keys = []

    def send_pointer(self, x, y, mask):
        pass

    def send_key(self, keysym, down):
        self.keys.append((keysym, down))


class ModifierTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_view(self, alt_is_command=True):
        view = RemoteView()
        client = Stub()
        view.attach(client)
        view.on_resize(REMOTE_W, REMOTE_H)
        view.alt_is_command = alt_is_command
        return view, client

    @staticmethod
    def key(view, qt_key, kind=QEvent.Type.KeyPress, text=""):
        return view.event(QKeyEvent(kind, qt_key, Qt.NoModifier, text))

    def test_alt_is_claimed_before_qt_makes_it_a_menu_shortcut(self):
        """Unclaimed, Alt opens the menu bar and never reaches the remote."""
        view, _ = self.make_view()
        event = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key_Alt,
                          Qt.NoModifier, "")
        self.assertTrue(view.event(event),
                        "Alt was left for the menu bar to swallow")
        self.assertTrue(event.isAccepted())

    def test_f11_is_left_alone_so_full_screen_is_not_one_way(self):
        view, _ = self.make_view()
        event = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key_F11,
                          Qt.NoModifier, "")
        view.event(event)
        self.assertFalse(event.isAccepted(),
                         "claiming F11 would strand the user in full screen")

    def test_nothing_is_claimed_without_a_session(self):
        view, _ = self.make_view()
        view.detach()
        event = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key_Alt,
                          Qt.NoModifier, "")
        view.event(event)
        self.assertFalse(event.isAccepted(),
                         "menus should work normally when not connected")

    def test_alt_sends_command_when_enabled(self):
        view, client = self.make_view(alt_is_command=True)
        self.key(view, Qt.Key_Alt)
        self.assertEqual(client.keys, [(SUPER_L, True)])

    def test_the_windows_key_sends_option_when_alt_is_command(self):
        view, client = self.make_view(alt_is_command=True)
        self.key(view, Qt.Key_Meta)
        self.assertEqual(client.keys, [(ALT_L, True)])

    def test_the_mapping_reverts_when_disabled(self):
        view, client = self.make_view(alt_is_command=False)
        self.key(view, Qt.Key_Alt)
        self.key(view, Qt.Key_Meta)
        self.assertEqual(client.keys, [(ALT_L, True), (SUPER_L, True)])

    def test_release_matches_the_press_that_was_sent(self):
        """A release that sent a different keysym would stick the modifier."""
        view, client = self.make_view(alt_is_command=True)
        self.key(view, Qt.Key_Alt)
        self.key(view, Qt.Key_Alt, QEvent.Type.KeyRelease)
        self.assertEqual(client.keys, [(SUPER_L, True), (SUPER_L, False)])

    def test_ordinary_keys_are_unaffected_by_the_swap(self):
        for enabled in (True, False):
            with self.subTest(alt_is_command=enabled):
                view, client = self.make_view(enabled)
                self.key(view, Qt.Key_V, text="v")
                self.assertEqual(client.keys, [(ord("v"), True)])


if __name__ == "__main__":
    unittest.main()
