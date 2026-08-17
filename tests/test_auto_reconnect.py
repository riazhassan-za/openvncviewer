"""Silently redialling a session that was cut off.

A link that drops for a second - a Wi-Fi roam, a Mac waking, a switch
relearning - used to end the session outright and put a modal in front of the
user. With auto-reconnect ticked the viewer dials back up to ten times, half a
second apart, and only complains once that budget is spent.

The rules that matter, and what each is here to stop:

  * only a session that actually came up is retried, so a typo'd hostname is
    reported at once instead of after five silent seconds;
  * a sequence, once started, survives attempts that never connect - otherwise
    a link that is down for a second would spend the whole budget on one try;
  * a rejected password stops it dead, because repeating one can lock an
    account out;
  * the user asking to disconnect ends it, and nothing retries afterwards.
"""

import os
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from openvncviewer import rfb, ui as ui_module  # noqa: E402
from openvncviewer.history import ServerHistory  # noqa: E402
from openvncviewer.ui import (RECONNECT_ATTEMPTS,  # noqa: E402
                              RECONNECT_DELAYS_MS, RECONNECT_WINDOW_S,
                              ConnectDialog, MainWindow)

HOST, PORT = "mac.local", 5900
REMOTE_W, REMOTE_H = 640, 480


class FakeClient:
    """An RFBClient that opens no socket and reports how far it got.

    The two attributes the window reads back are exactly the ones a real
    client sets: `was_connected` once the handshake finished, `auth_failed`
    when the failure was the server refusing the credentials.
    """

    created = []

    def __init__(self, host, port, username, password, **callbacks):
        self.host = host
        self.desktop_name = "Stub desktop"
        self.width, self.height = REMOTE_W, REMOTE_H
        self.framebuffer = bytearray(REMOTE_W * REMOTE_H * 4)
        self.was_connected = False
        self.auth_failed = False
        self.stopped = False
        FakeClient.created.append(self)

    def start(self):
        pass

    def stop(self):
        self.stopped = True

    def send_clipboard(self, text):
        return True


class ReconnectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.history = ServerHistory(Path(directory.name) / "servers.json")

        FakeClient.created = []
        original = ui_module.RFBClient
        ui_module.RFBClient = FakeClient
        self.addCleanup(setattr, ui_module, "RFBClient", original)

        # The give-up path raises a modal, which would sit there forever with
        # no one offscreen to dismiss it.
        warning = QMessageBox.warning
        QMessageBox.warning = staticmethod(lambda *a, **k: None)
        self.addCleanup(setattr, QMessageBox, "warning", warning)

        self.window = MainWindow()
        self.window.history = self.history
        self.addCleanup(self.window.close)

    # ------------------------------------------------------------- helpers

    def connect(self, auto_reconnect=True, host=HOST):
        self.window.connect_to(host, PORT, "amy", "secret",
                               auto_reconnect=auto_reconnect)

    def establish(self, auto_reconnect=True, host=HOST):
        """Connect and let the session come up, as a real one would."""
        self.connect(auto_reconnect, host)
        FakeClient.created[-1].was_connected = True
        self.window._on_resize(REMOTE_W, REMOTE_H)

    def drop(self, reason="connection closed by server"):
        """The link goes. Fires the timer by hand rather than waiting on it."""
        self.window._on_disconnect(reason)
        if self.window._reconnect_timer.isActive():
            self.window._reconnect_timer.stop()
            self.window._retry_connection()
            return True
        return False

    # -------------------------------------------------------------- the rule

    def test_a_dropped_session_is_redialled(self):
        self.establish()
        self.assertEqual(len(FakeClient.created), 1)
        self.assertTrue(self.drop(), "no reconnect was scheduled")
        self.assertEqual(len(FakeClient.created), 2, "did not dial again")
        self.assertEqual(FakeClient.created[-1].host, HOST)

    def test_nothing_is_redialled_when_the_option_is_off(self):
        self.establish(auto_reconnect=False)
        self.assertFalse(self.drop(), "retried without being asked to")
        self.assertEqual(len(FakeClient.created), 1)
        self.assertIn("Disconnected", self.window.status.text())

    def test_a_host_that_never_answered_is_not_retried(self):
        """A typo'd hostname should be reported now, not in five seconds."""
        self.connect()  # never reaches _on_resize, so was_connected stays False
        self.assertFalse(self.drop("connection refused"))
        self.assertEqual(len(FakeClient.created), 1)

    def test_the_sequence_survives_attempts_that_do_not_connect(self):
        """The point of ten tries is riding out a link that stays down."""
        self.establish()
        for expected in range(2, 6):
            self.assertTrue(self.drop(), "gave up while the budget remained")
            self.assertEqual(len(FakeClient.created), expected)

    def test_it_gives_up_after_the_configured_number_of_attempts(self):
        self.establish()
        for _ in range(RECONNECT_ATTEMPTS):
            self.assertTrue(self.drop())
        self.assertEqual(len(FakeClient.created), RECONNECT_ATTEMPTS + 1)

        self.assertFalse(self.drop(), "kept retrying past the budget")
        self.assertEqual(len(FakeClient.created), RECONNECT_ATTEMPTS + 1)
        self.assertIn("Disconnected", self.window.status.text())

    def test_a_session_that_comes_back_gets_a_fresh_budget(self):
        self.establish()
        for _ in range(RECONNECT_ATTEMPTS - 1):
            self.drop()
        # It answers this time.
        FakeClient.created[-1].was_connected = True
        self.window._on_resize(REMOTE_W, REMOTE_H)
        self.assertEqual(self.window._reconnect_attempt, 0,
                         "a live session did not replenish the budget")

        for _ in range(RECONNECT_ATTEMPTS):
            self.assertTrue(self.drop(), "the second outage got a short budget")

    # ------------------------------------------------------------ stop early

    def test_a_rejected_password_stops_the_sequence_dead(self):
        """Repeating a refused password can lock the account out."""
        self.establish()
        self.assertTrue(self.drop())
        FakeClient.created[-1].auth_failed = True
        self.assertFalse(self.drop("authentication failed - check the password"),
                         "retried a password the server had refused")
        self.assertEqual(len(FakeClient.created), 2)

    def test_disconnecting_abandons_a_reconnect_in_flight(self):
        self.establish()
        self.window._on_disconnect("connection closed by server")
        self.assertTrue(self.window._reconnect_timer.isActive())

        self.window.disconnect()
        self.assertFalse(self.window._reconnect_timer.isActive(),
                         "a retry was still armed after the user stopped")
        self.window._retry_connection()  # as a stray timer would
        self.assertEqual(len(FakeClient.created), 1, "redialled after Disconnect")
        self.assertEqual(self.window.status.text(), "Not connected")

    def test_closing_the_window_abandons_a_reconnect_in_flight(self):
        self.establish()
        self.window._on_disconnect("connection closed by server")
        self.window.close()
        self.assertFalse(self.window._reconnect_timer.isActive())

    def test_dialling_a_new_server_does_not_inherit_the_old_retry_count(self):
        """Otherwise a host that never answers borrows the last one's budget."""
        self.establish()
        for _ in range(RECONNECT_ATTEMPTS - 1):
            self.drop()
        self.assertGreater(self.window._reconnect_attempt, 0)

        self.connect(host="somewhere.else")
        self.assertEqual(self.window._reconnect_attempt, 0)
        made = len(FakeClient.created)
        self.assertFalse(self.drop("connection refused"),
                         "retried a server that never came up")
        self.assertEqual(len(FakeClient.created), made)

    # ---------------------------------------------------------- what is shown

    def test_the_retry_is_silent_but_the_status_bar_says_so(self):
        """No modal while it is trying; one only when it finally gives up."""
        shown = []
        QMessageBox.warning = staticmethod(lambda *a, **k: shown.append(a))

        self.establish()
        self.window._on_disconnect("connection closed by server")
        self.assertEqual(shown, [], "interrupted the user mid-retry")
        self.assertIn("econnecting", self.window.status.text())
        self.assertIn(f"1 of {RECONNECT_ATTEMPTS}", self.window.status.text())

        self.window._reconnect_timer.stop()
        self.window._retry_connection()
        for _ in range(RECONNECT_ATTEMPTS - 1):
            self.drop()
        self.drop()
        self.assertEqual(len(shown), 1, "no warning once the budget was spent")

    def test_the_stale_frame_is_dropped_between_attempts(self):
        """The image wraps the framebuffer of the client that just died."""
        self.establish()
        self.assertIsNotNone(self.window.view._image)
        self.window._on_disconnect("connection closed by server")
        self.assertIsNone(self.window.view._image,
                          "a dead client's framebuffer was left on screen")

    def test_the_first_retry_is_quick_enough_to_go_unnoticed(self):
        """A momentary blip should be over before the user looks up."""
        self.assertLessEqual(RECONNECT_DELAYS_MS[0], 500)
        self.establish()
        self.window._on_disconnect("connection closed by server")
        self.assertTrue(self.window._reconnect_timer.isSingleShot())
        self.assertEqual(self.window._reconnect_timer.interval(),
                         RECONNECT_DELAYS_MS[0])

    def test_the_wait_backs_off_and_is_capped(self):
        """Hammering a server that is not there helps nobody."""
        self.assertEqual(sorted(RECONNECT_DELAYS_MS), list(RECONNECT_DELAYS_MS),
                         "the schedule is not monotonic")
        self.assertLessEqual(max(RECONNECT_DELAYS_MS), 15000)

        self.establish()
        for expected in RECONNECT_DELAYS_MS:
            self.window._on_disconnect("connection closed by server")
            self.assertEqual(self.window._reconnect_timer.interval(), expected)
            self.window._reconnect_timer.stop()
            self.window._retry_connection()

    def test_the_window_outlasts_a_real_outage(self):
        """TCP needs ~15s just to notice; a minute of retrying after that is
        the point of backing off rather than firing ten times in five
        seconds."""
        self.assertGreaterEqual(
            RECONNECT_WINDOW_S, 60,
            "the sequence gives up before a one-minute outage would end")
        self.assertEqual(RECONNECT_WINDOW_S, sum(RECONNECT_DELAYS_MS) // 1000)
        self.assertEqual(RECONNECT_ATTEMPTS, len(RECONNECT_DELAYS_MS))

    def test_the_dial_itself_says_it_is_a_reconnect(self):
        """"Connecting to ..." is true but reads as a session you started."""
        self.establish()
        self.window._on_disconnect("connection closed by server")
        self.window._reconnect_timer.stop()
        self.window._retry_connection()

        text = self.window.status.text()
        self.assertTrue(text.startswith("Reconnecting to"), text)
        self.assertIn(HOST, text)
        self.assertIn(f"attempt 1 of {RECONNECT_ATTEMPTS}", text)

    def test_the_attempt_number_climbs_across_the_dials(self):
        self.establish()
        for expected in range(1, 4):
            self.window._on_disconnect("connection closed by server")
            self.window._reconnect_timer.stop()
            self.window._retry_connection()
            self.assertIn(f"attempt {expected} of {RECONNECT_ATTEMPTS}",
                          self.window.status.text())

    def test_a_session_the_user_started_is_not_called_a_reconnect(self):
        self.connect()
        self.assertTrue(self.window.status.text().startswith("Connecting to"),
                        self.window.status.text())

    def test_the_status_bar_names_the_wait_once_it_is_long(self):
        """A 15-second gap with no explanation reads as a hung window."""
        self.establish()
        for _ in range(len(RECONNECT_DELAYS_MS) - 1):
            self.drop()
        self.window._on_disconnect("connection closed by server")
        self.assertIn("15s", self.window.status.text())


class PersistenceTest(unittest.TestCase):
    """The tick is remembered per server, like the other session options."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.history = ServerHistory(Path(directory.name) / "servers.json")

    def dialog(self):
        made = ConnectDialog(history=self.history)
        self.addCleanup(made.close)
        return made

    def test_each_server_keeps_its_own_answer(self):
        self.history.remember(HOST, PORT, auto_reconnect=False)
        self.history.remember("other.local", PORT, auto_reconnect=True)
        dialog = self.dialog()
        dialog.host.setEditText(HOST)
        self.assertFalse(dialog.auto_reconnect.isChecked())
        dialog.host.setEditText("other.local")
        self.assertTrue(dialog.auto_reconnect.isChecked())

    def test_it_survives_a_restart(self):
        self.history.remember(HOST, PORT, auto_reconnect=False)
        self.assertFalse(ServerHistory(self.history.path).find(HOST)
                         ["auto_reconnect"])

    def test_a_server_saved_before_the_option_existed_defaults_on(self):
        self.history.remember(HOST, PORT)
        self.assertTrue(self.history.find(HOST)["auto_reconnect"])

    def test_the_tick_reaches_the_stored_entry(self):
        window = None
        original = ui_module.RFBClient
        ui_module.RFBClient = FakeClient
        self.addCleanup(setattr, ui_module, "RFBClient", original)
        window = MainWindow()
        window.history = self.history
        self.addCleanup(window.close)

        window.connect_to(HOST, PORT, "amy", "secret", auto_reconnect=False)
        window._on_resize(REMOTE_W, REMOTE_H)
        self.assertFalse(self.history.find(HOST)["auto_reconnect"])


class BareVNCServer(threading.Thread):
    """The shortest handshake RFB allows: 3.8, security type 1, no password.

    Enough to carry a real RFBClient to the point where it declares itself
    connected, which is the one thing FakeClient above cannot prove - it sets
    `was_connected` by hand, so a client that never set it would leave every
    test in this file green while auto-reconnect silently never fired.
    """

    def __init__(self, accept_auth=True):
        super().__init__(daemon=True)
        self.accept_auth = accept_auth
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.error = None
        self.done = threading.Event()

    def run(self):
        try:
            conn, _ = self.listener.accept()
            with conn:
                conn.sendall(b"RFB 003.008\n")
                assert conn.recv(12) == b"RFB 003.008\n"
                conn.sendall(bytes([1, 1]))          # one type on offer: none
                assert conn.recv(1) == b"\x01"
                if not self.accept_auth:
                    reason = b"too many authentication failures"
                    conn.sendall(struct.pack("!II", 1, len(reason)) + reason)
                    return
                conn.sendall(struct.pack("!I", 0))   # SecurityResult: OK
                assert conn.recv(1) == b"\x01"       # ClientInit, shared
                name = b"Bare desktop"
                conn.sendall(struct.pack("!HH", 64, 48)
                             + struct.pack("!BBBBHHHBBB3x", 32, 24, 0, 1,
                                           255, 255, 255, 16, 8, 0)
                             + struct.pack("!I", len(name)) + name)
                conn.recv(20)                        # SetPixelFormat
                # Dropping the link here is the case under test: a session
                # that came up and then went away.
        except Exception as exc:  # surfaced by the test
            self.error = exc
        finally:
            self.listener.close()
            self.done.set()


class LiveHandshakeTest(unittest.TestCase):
    def client_against(self, server):
        self.addCleanup(server.done.wait, 5)
        server.start()
        finished = threading.Event()
        reasons = []

        def ended(reason):
            reasons.append(reason)
            finished.set()

        client = rfb.RFBClient("127.0.0.1", server.port, "", "",
                               on_resize=lambda *a: None,
                               on_damage=lambda *a: None,
                               on_disconnect=ended)
        self.addCleanup(client.stop)
        client.start()
        self.assertTrue(finished.wait(10), "the session never ended")
        self.assertIsNone(server.error, f"the server tripped: {server.error}")
        return client, reasons[0]

    def test_a_real_handshake_marks_the_client_connected(self):
        client, _ = self.client_against(BareVNCServer())
        self.assertTrue(client.was_connected,
                        "a session that came up did not say so, so nothing "
                        "would ever be reconnected")
        self.assertFalse(client.auth_failed)

    def test_a_refused_handshake_does_not(self):
        client, reason = self.client_against(BareVNCServer(accept_auth=False))
        self.assertFalse(client.was_connected)
        self.assertTrue(client.auth_failed, f"not flagged as auth: {reason}")
        self.assertIn("authentication failures", reason)


class KeepaliveTest(unittest.TestCase):
    """Detecting a peer that vanished without closing.

    This is what makes the rest of the file worth anything against a real
    outage. The read blocks with no timeout, and with no updates arriving
    there is nothing being written to fail either, so a Mac that loses Wi-Fi
    hangs the session rather than ending it - and a session that never ends is
    never reconnected.
    """

    def test_it_is_turned_on(self):
        with socket.socket() as sock:
            self.assertFalse(sock.getsockopt(socket.SOL_SOCKET,
                                             socket.SO_KEEPALIVE))
            rfb.enable_keepalive(sock)
            self.assertTrue(sock.getsockopt(socket.SOL_SOCKET,
                                            socket.SO_KEEPALIVE),
                            "a silently dropped link would go unnoticed")

    def test_it_probes_far_sooner_than_the_system_default(self):
        """Windows waits two hours out of the box, which is no use here."""
        self.assertLessEqual(rfb.KEEPALIVE_IDLE_MS, 10_000)
        self.assertLessEqual(rfb.KEEPALIVE_INTERVAL_MS, rfb.KEEPALIVE_IDLE_MS)

    def test_detection_fits_inside_the_retry_window(self):
        """Noticing the drop must leave time to still be retrying afterwards."""
        detect_s = (rfb.KEEPALIVE_IDLE_MS
                    + rfb.KEEPALIVE_PROBES * rfb.KEEPALIVE_INTERVAL_MS) / 1000
        self.assertLess(detect_s, RECONNECT_WINDOW_S / 2,
                        "the sequence would be nearly spent before the drop "
                        "was even noticed")

    def test_a_socket_that_refuses_the_tuning_still_connects(self):
        """Best-effort: the fine tuning is a nicety, the session is not."""
        class Awkward(socket.socket):
            def ioctl(self, *args):
                raise OSError("not supported here")

        with Awkward() as sock:
            rfb.enable_keepalive(sock)  # must not raise
            self.assertTrue(sock.getsockopt(socket.SOL_SOCKET,
                                            socket.SO_KEEPALIVE))

    def test_the_client_actually_applies_it(self):
        """Wired into _connect, not merely available to be called."""
        applied = []
        original = rfb.enable_keepalive
        rfb.enable_keepalive = lambda sock: (applied.append(sock),
                                             original(sock))
        self.addCleanup(setattr, rfb, "enable_keepalive", original)

        server = BareVNCServer()
        self.addCleanup(server.done.wait, 5)
        server.start()
        finished = threading.Event()
        client = rfb.RFBClient("127.0.0.1", server.port, "", "",
                               on_resize=lambda *a: None,
                               on_damage=lambda *a: None,
                               on_disconnect=lambda reason: finished.set())
        self.addCleanup(client.stop)
        client.start()
        self.assertTrue(finished.wait(10), "the session never ended")
        self.assertEqual(len(applied), 1,
                         "the client connected without enabling keepalive")


class AuthErrorTest(unittest.TestCase):
    """The protocol layer has to say *why* it failed, or the rule above cannot
    be applied. A string comparison on the reason would break the first time
    a server worded its refusal differently."""

    def test_an_auth_error_is_an_rfb_error(self):
        self.assertTrue(issubclass(rfb.AuthError, rfb.RFBError))

    def test_a_refused_password_is_flagged_as_an_auth_failure(self):
        client = self._client_failing_with(
            rfb.AuthError("authentication failed - check the password"))
        self.assertTrue(client.auth_failed)

    def test_a_network_failure_is_not(self):
        client = self._client_failing_with(OSError("connection reset by peer"))
        self.assertFalse(client.auth_failed)

    def test_a_protocol_error_is_not(self):
        client = self._client_failing_with(rfb.RFBError("not a VNC server"))
        self.assertFalse(client.auth_failed)

    def test_was_connected_starts_false(self):
        client = rfb.RFBClient("h", 1, "", "", lambda *a: None, lambda *a: None,
                               lambda *a: None)
        self.assertFalse(client.was_connected)

    def _client_failing_with(self, error):
        """Run the client's own loop against a _connect that raises."""
        reasons = []
        client = rfb.RFBClient("h", 1, "", "", lambda *a: None, lambda *a: None,
                               reasons.append)
        client._connect = lambda: (_ for _ in ()).throw(error)
        client._running = True
        client._run()
        self.assertEqual(len(reasons), 1, "the disconnect was not reported")
        return client


if __name__ == "__main__":
    unittest.main()
