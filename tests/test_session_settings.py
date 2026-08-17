"""Per-server session settings: wheel speed, clipboard sharing, Alt as Command.

A Retina Mac scaled into a window needs the wheel notch count amplified to feel
like anything; a standard VNC server on ordinary hardware does not. Remembering
one global value cannot serve both, so each server keeps its own - and a host
never connected to starts from what its connection type implies.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from openvncviewer.history import (SESSION_DEFAULTS,  # noqa: E402
                                   WHEEL_SPEED_RANGE, ServerHistory)
from openvncviewer.ui import (WHEEL_SPEED_DEFAULT,  # noqa: E402
                              WHEEL_SPEED_MAX, WHEEL_SPEED_MIN,
                              WHEEL_SPEED_RAW, ConnectDialog)

MAC, VNC = "mac.local", "vncbox.local"


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "servers.json"

    def test_settings_survive_a_restart(self):
        history = ServerHistory(self.path)
        history.remember(MAC, 5900, wheel_speed=63, share_clipboard=False,
                         alt_is_command=True)
        reopened = ServerHistory(self.path).find(MAC)
        self.assertEqual(reopened["wheel_speed"], 63)
        self.assertFalse(reopened["share_clipboard"])
        self.assertTrue(reopened["alt_is_command"])

    def test_each_server_keeps_its_own(self):
        history = ServerHistory(self.path)
        history.remember(MAC, 5900, wheel_speed=50, alt_is_command=True)
        history.remember(VNC, 5900, wheel_speed=1, alt_is_command=False)
        self.assertEqual(history.find(MAC)["wheel_speed"], 50)
        self.assertEqual(history.find(VNC)["wheel_speed"], 1)
        self.assertFalse(history.find(VNC)["alt_is_command"])

    def test_a_file_written_before_these_existed_still_loads(self):
        """The upgrade path: no settings recorded is not a corrupt entry."""
        self.path.write_text(json.dumps({"version": 1, "servers": [
            {"host": MAC, "port": 5900, "username": "amy", "name": "Mac",
             "password": ""}]}), encoding="utf-8")
        entry = ServerHistory(self.path).find(MAC)
        self.assertIsNotNone(entry, "an older entry was discarded")
        self.assertEqual(entry["username"], "amy")
        self.assertIsNone(entry["wheel_speed"], "should defer to the type")
        self.assertTrue(entry["share_clipboard"])

    def test_junk_settings_fall_back_without_losing_the_server(self):
        self.path.write_text(json.dumps({"version": 1, "servers": [
            {"host": MAC, "port": 5900, "wheel_speed": "fast",
             "share_clipboard": "yes", "alt_is_command": 3}]}),
            encoding="utf-8")
        entry = ServerHistory(self.path).find(MAC)
        self.assertIsNotNone(entry)
        self.assertIsNone(entry["wheel_speed"])
        self.assertTrue(entry["share_clipboard"])

    def test_an_out_of_range_speed_is_refused(self):
        """The file is user-editable; the slider range is the contract."""
        for bad in (0, -5, 10_000):
            with self.subTest(speed=bad):
                self.path.write_text(json.dumps({"version": 1, "servers": [
                    {"host": MAC, "port": 5900, "wheel_speed": bad}]}),
                    encoding="utf-8")
                self.assertIsNone(ServerHistory(self.path).find(MAC)["wheel_speed"])

    def test_an_unknown_setting_is_refused_rather_than_written(self):
        history = ServerHistory(self.path)
        with self.assertRaises(TypeError):
            history.remember(MAC, 5900, wheel_spede=50)  # typo

    def test_the_stored_range_matches_the_slider(self):
        self.assertEqual(WHEEL_SPEED_RANGE, (WHEEL_SPEED_MIN, WHEEL_SPEED_MAX))

    def test_forgetting_a_password_leaves_the_settings_alone(self):
        history = ServerHistory(self.path)
        history.remember(MAC, 5900, password="token", wheel_speed=42)
        history.forget_password(MAC)
        self.assertEqual(history.find(MAC)["wheel_speed"], 42)


class DialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.history = ServerHistory(Path(self.directory.name) / "servers.json")

    def dialog(self, host="", speed=WHEEL_SPEED_DEFAULT):
        made = ConnectDialog(host, 5900, "", speed, history=self.history)
        self.addCleanup(made.close)
        return made

    def test_selecting_a_saved_server_restores_its_settings(self):
        self.history.remember(MAC, 5900, "amy", "Mac", wheel_speed=63,
                              share_clipboard=False, alt_is_command=True)
        dialog = self.dialog()
        dialog.host.setEditText(MAC)
        self.assertEqual(dialog.wheel_speed.value(), 63)
        self.assertFalse(dialog.share_clipboard.isChecked())
        self.assertTrue(dialog.alt_is_command.isChecked())

    def test_switching_between_servers_switches_the_settings(self):
        self.history.remember(MAC, 5900, wheel_speed=63, alt_is_command=True)
        self.history.remember(VNC, 5900, wheel_speed=4, alt_is_command=False)
        dialog = self.dialog()
        dialog.host.setEditText(MAC)
        self.assertEqual(dialog.wheel_speed.value(), 63)
        dialog.host.setEditText(VNC)
        self.assertEqual(dialog.wheel_speed.value(), 4)
        self.assertFalse(dialog.alt_is_command.isChecked())

    def test_an_unseen_host_with_no_username_starts_raw(self):
        """No username means a standard VNC server, which needs no amplifying."""
        dialog = self.dialog()
        dialog.host.setEditText("brand-new.local")
        dialog.username.setText("")
        self.assertEqual(dialog.wheel_speed.value(), WHEEL_SPEED_RAW)

    def test_an_unseen_host_with_a_username_starts_amplified(self):
        """A username means an ARD login, which means a Mac."""
        dialog = self.dialog()
        dialog.host.setEditText("brand-new.local")
        dialog.username.setText("amy")
        self.assertEqual(dialog.wheel_speed.value(), WHEEL_SPEED_DEFAULT)

    def test_typing_a_username_moves_the_default_both_ways(self):
        dialog = self.dialog()
        dialog.host.setEditText("brand-new.local")
        dialog.username.setText("")
        self.assertEqual(dialog.wheel_speed.value(), WHEEL_SPEED_RAW)
        dialog.username.setText("amy")
        self.assertEqual(dialog.wheel_speed.value(), WHEEL_SPEED_DEFAULT)
        dialog.username.setText("")
        self.assertEqual(dialog.wheel_speed.value(), WHEEL_SPEED_RAW)

    def test_the_macos_tick_does_not_touch_the_speed(self):
        """It maps the keyboard; it defaults on, so it cannot mean 'is a Mac'."""
        dialog = self.dialog()
        dialog.host.setEditText("brand-new.local")
        dialog.username.setText("")
        dialog.alt_is_command.setChecked(True)
        self.assertEqual(dialog.wheel_speed.value(), WHEEL_SPEED_RAW)

    def test_the_username_does_not_disturb_a_saved_speed(self):
        """A speed you chose outranks any default."""
        self.history.remember(MAC, 5900, "amy", wheel_speed=63)
        dialog = self.dialog()
        dialog.host.setEditText(MAC)
        dialog.username.setText("")
        self.assertEqual(dialog.wheel_speed.value(), 63)

    def test_a_server_saved_before_speeds_existed_takes_the_type_default(self):
        self.history.remember(MAC, 5900, "amy", "Mac")  # no session settings
        dialog = self.dialog()
        dialog.host.setEditText(MAC)
        self.assertEqual(dialog.wheel_speed.value(), WHEEL_SPEED_DEFAULT,
                         "a saved macOS account should still imply a Mac")

    def test_the_speed_the_dialog_was_opened_with_is_not_discarded(self):
        """It continues the last session; only a host you pick gets a default."""
        dialog = self.dialog(host="unseen.local", speed=7)
        self.assertEqual(dialog.wheel_speed.value(), 7)

    def test_defaults_cover_every_setting_the_dialog_saves(self):
        """A new option must be added to SESSION_DEFAULTS to be persisted."""
        self.assertEqual(set(SESSION_DEFAULTS),
                         {"wheel_speed", "share_clipboard", "alt_is_command",
                          "auto_reconnect"})


if __name__ == "__main__":
    unittest.main()
