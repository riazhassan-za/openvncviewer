"""Unticking "Save password" must revoke the stored token, connection or not.

The saved-password token was written by `_on_resize`, which only runs once a
session is up, and `_on_disconnect` discarded the pending write. So unticking
the box and then failing to connect - wrong port, server down, bad credentials -
left the old token on disk while the UI reported nothing. The user asked for the
credential to be gone; a failed TCP connection is not a reason to keep it.
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from openvncviewer.history import ServerHistory  # noqa: E402
from openvncviewer.ui import MainWindow  # noqa: E402

HOST, PORT = "vnc.example", 5900


class RevocationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "servers.json"

        self.window = MainWindow()
        self.addCleanup(self.window.close)
        self.window.history = ServerHistory(self.path)
        # A real connect would open a socket and start a thread; neither is
        # needed to exercise what connect_to persists.
        self.window.client = None
        self.started = []

        class Stub:
            def __init__(self, *args, **kwargs):
                pass

            def start(self_inner):
                self.started.append(True)

            def stop(self_inner):
                pass

        import openvncviewer.ui as ui
        self._real_client = ui.RFBClient
        ui.RFBClient = Stub
        self.addCleanup(lambda: setattr(ui, "RFBClient", self._real_client))

        # _on_disconnect reports a reason with a modal dialog, which would sit
        # there forever with nobody to dismiss it.
        real_warning = ui.QMessageBox.warning
        ui.QMessageBox.warning = staticmethod(lambda *args, **kwargs: None)
        self.addCleanup(lambda: setattr(ui.QMessageBox, "warning", real_warning))

    def saved_token(self):
        entry = self.window.history.find(HOST)
        return entry["password"] if entry else None

    def test_unticking_revokes_even_though_the_connection_never_comes_up(self):
        self.window.history.remember(HOST, PORT, "amy", "Work", "old-token")
        self.assertEqual(self.saved_token(), "old-token")

        self.window.connect_to(HOST, PORT, "amy", "secret", save_password=False)
        # No _on_resize: the session never came up.
        self.window._on_disconnect("connection refused")

        self.assertEqual(self.saved_token(), "",
                         "the revoked password survived a failed connection")

    def test_the_entry_itself_survives_revocation(self):
        """Forgetting a password is not forgetting the server."""
        self.window.history.remember(HOST, PORT, "amy", "Work", "old-token")
        self.window.connect_to(HOST, PORT, "amy", "secret", server_name="Work",
                               save_password=False)
        self.window._on_disconnect("connection refused")

        entry = self.window.history.find(HOST)
        self.assertIsNotNone(entry, "the server was dropped from history")
        self.assertEqual(entry["username"], "amy")
        self.assertEqual(entry["name"], "Work")

    def test_an_unknown_host_is_not_recorded_by_an_unticked_box(self):
        """Revocation must not become a back door for remembering typos."""
        self.window.connect_to("never-seen", PORT, "", "", save_password=False)
        self.window._on_disconnect("no route to host")
        self.assertEqual(self.window.history.hosts(), [])

    def test_ticking_still_defers_the_save_until_the_session_is_up(self):
        """The history rule is unchanged: only servers actually connected to."""
        self.window.connect_to(HOST, PORT, "amy", "secret", save_password=True)
        self.assertEqual(self.window.history.hosts(), [],
                         "a host was recorded before connecting")
        self.window._on_disconnect("connection refused")
        self.assertEqual(self.window.history.hosts(), [])


if __name__ == "__main__":
    unittest.main()
