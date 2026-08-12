"""Alt combinations must reach the remote, not the local menu bar.

`test_modifiers.py` hands synthetic events straight to `RemoteView.event()`.
That proves the view claims what it is given, but it cannot catch a key the
view never receives - and menu mnemonics are matched application-wide rather
than at the focused widget, so Alt+V opened the View menu while those tests
passed. These go through Qt's own dispatch instead, and assert on the menu bar.
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from openvncviewer.ui import MainWindow  # noqa: E402

REMOTE_W, REMOTE_H = 200, 120
# Every letter that is a top-level menu mnemonic, plus two that are not.
MNEMONIC_KEYS = {"V": Qt.Key_V, "F": Qt.Key_F, "H": Qt.Key_H}
PLAIN_KEYS = {"C": Qt.Key_C, "X": Qt.Key_X}


class Stub:
    width, height = REMOTE_W, REMOTE_H
    desktop_name = "stub"

    def __init__(self):
        self.framebuffer = bytearray(REMOTE_W * REMOTE_H * 4)
        self.keys = []

    def send_key(self, keysym, down):
        self.keys.append((keysym, down))

    def send_pointer(self, *args):
        pass

    def stop(self):
        pass


class MenuPassthroughTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def connected_window(self):
        window = MainWindow()
        self.addCleanup(window.close)
        client = Stub()
        window.client = client
        window.view.attach(client)
        window._set_menu_mnemonics(False)
        window.show()
        window.view.setFocus()
        QTest.qWaitForWindowExposed(window)
        self.app.processEvents()
        return window, client

    def press(self, window, key, modifier=Qt.AltModifier):
        QTest.keyClick(window.view, key, modifier)
        self.app.processEvents()

    def active_menu(self, window):
        action = window.menuBar().activeAction()
        return action.text() if action else None

    def test_alt_plus_a_menu_mnemonic_does_not_open_the_menu(self):
        for label, key in MNEMONIC_KEYS.items():
            with self.subTest(key=f"Alt+{label}"):
                window, _ = self.connected_window()
                self.press(window, key)
                self.assertIsNone(self.active_menu(window),
                                  f"Alt+{label} opened a menu")

    def test_alt_plus_a_menu_mnemonic_reaches_the_remote(self):
        for label, key in MNEMONIC_KEYS.items():
            with self.subTest(key=f"Alt+{label}"):
                window, client = self.connected_window()
                self.press(window, key)
                sent = [keysym for keysym, down in client.keys if down]
                self.assertIn(ord(label.lower()), sent,
                              f"Alt+{label} never reached the remote")

    def test_alt_plus_an_ordinary_letter_still_reaches_the_remote(self):
        for label, key in PLAIN_KEYS.items():
            with self.subTest(key=f"Alt+{label}"):
                window, client = self.connected_window()
                self.press(window, key)
                sent = [keysym for keysym, down in client.keys if down]
                self.assertIn(ord(label.lower()), sent)

    def test_bare_alt_does_not_activate_the_menu_bar(self):
        window, _ = self.connected_window()
        self.press(window, Qt.Key_Alt, Qt.NoModifier)
        self.assertIsNone(self.active_menu(window),
                          "Alt alone activated the menu bar")

    def test_f11_is_still_ours_so_full_screen_is_not_one_way(self):
        """The one key the remote does not get, or there is no way back."""
        window, client = self.connected_window()
        self.assertTrue(window.fullscreen_action.isEnabled())
        self.press(window, Qt.Key_F11, Qt.NoModifier)
        self.assertTrue(window.fullscreen_action.isChecked(),
                        "F11 no longer toggles full screen")
        window.fullscreen_action.setChecked(False)

    def test_the_menus_are_still_reachable_when_not_connected(self):
        """Giving up Alt+F is only acceptable while something else needs it."""
        window = MainWindow()
        self.addCleanup(window.close)
        titles = [menu.title() for menu, _ in window._menus]
        self.assertEqual(titles, ["&File", "&View", "&Help"])

    def test_mnemonics_come_back_on_disconnect(self):
        window, _ = self.connected_window()
        self.assertEqual([menu.title() for menu, _ in window._menus],
                         ["File", "View", "Help"])
        window.disconnect()
        self.assertEqual([menu.title() for menu, _ in window._menus],
                         ["&File", "&View", "&Help"])

    def test_a_modal_dialog_outranks_the_session(self):
        """Typing a hostname while connected must not be eaten by the filter."""
        window, _ = self.connected_window()
        from PySide6.QtWidgets import QDialog, QLineEdit

        dialog = QDialog(window)
        field = QLineEdit(dialog)
        dialog.setModal(True)
        dialog.show()
        field.setFocus()
        self.app.processEvents()
        self.addCleanup(dialog.close)

        QTest.keyClicks(field, "host")
        self.app.processEvents()
        self.assertEqual(field.text(), "host",
                         "the event filter swallowed dialog typing")


if __name__ == "__main__":
    unittest.main()
