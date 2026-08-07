"""Clipboard sharing, both directions.

The parts worth pinning: the wire encoding, the size bounds on a message whose
length a server chooses, and the loop that would otherwise bounce text back and
forth forever.
"""

import os
import struct
import unittest
import zlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from openvncviewer.rfb import (CLIPBOARD_HARD_LIMIT,  # noqa: E402
                               MAX_CLIPBOARD_BYTES, RFBClient, RFBError,
                               decode_clipboard, encode_clipboard)
from openvncviewer.ui import MainWindow  # noqa: E402

REMOTE_W, REMOTE_H = 200, 120


class EncodingTest(unittest.TestCase):
    def test_plain_text_round_trips(self):
        for text in ("hello", "a" * 1000, "tabs\tand spaces"):
            with self.subTest(text=text[:20]):
                self.assertEqual(decode_clipboard(encode_clipboard(text)), text)

    def test_latin1_characters_survive(self):
        self.assertEqual(decode_clipboard(encode_clipboard("café naïve")),
                         "café naïve")

    def test_characters_outside_latin1_are_substituted_not_fatal(self):
        """Losing an emoji beats refusing to share the sentence around it."""
        encoded = encode_clipboard("hello 🌍 world")
        self.assertIsInstance(encoded, bytes)
        self.assertIn(b"hello", encoded)
        self.assertIn(b"world", encoded)

    def test_windows_line_endings_are_converted_on_the_way_out(self):
        """RFB specifies a bare LF; a CR must not reach the wire."""
        self.assertEqual(encode_clipboard("one\r\ntwo\r\nthree"),
                         b"one\ntwo\nthree")
        self.assertNotIn(b"\r", encode_clipboard("a\rb"))

    def test_incoming_line_endings_are_normalised(self):
        self.assertEqual(decode_clipboard(b"one\r\ntwo"), "one\ntwo")
        self.assertEqual(decode_clipboard(b"one\rtwo"), "one\ntwo")

    def test_a_nul_terminator_does_not_reach_the_wire(self):
        """Observed live: some Windows apps copy a NUL-terminated string."""
        self.assertEqual(encode_clipboard("teest\x00"), b"teest")
        self.assertEqual(decode_clipboard(b"teest\x00"), "teest")


class ServerCutTextTest(unittest.TestCase):
    """A length field the server chooses is a length field worth bounding."""

    def make_client(self, payload):
        client = RFBClient.__new__(RFBClient)
        client.width = client.height = 0
        client.framebuffer = bytearray()
        client._zrle = zlib.decompressobj()
        client._running = True
        received = []
        client._on_clipboard = received.append

        class Reader:
            def __init__(self, data):
                self.data = data
                self.pos = 0

            def read(self, n):
                chunk = self.data[self.pos:self.pos + n]
                self.pos += n
                return chunk

        client._reader = Reader(payload)
        return client, received

    def cut_text(self, body, declared=None):
        length = len(body) if declared is None else declared
        return bytes([3]) + struct.pack("!3xI", length) + body

    def test_text_reaches_the_callback(self):
        client, received = self.make_client(self.cut_text(b"from the server"))
        client._handle_server_message()
        self.assertEqual(received, ["from the server"])

    def test_an_absurd_length_is_refused_before_reading_it(self):
        """4GB declared, nothing sent: this must not try to read it."""
        client, _ = self.make_client(self.cut_text(b"", declared=0xFFFFFFFF))
        with self.assertRaises(RFBError) as caught:
            client._handle_server_message()
        self.assertIn("clipboard", str(caught.exception))

    def test_oversized_but_plausible_text_is_dropped_not_fatal(self):
        """Between the limits it is real, just too big to be a clipboard."""
        body = b"x" * (MAX_CLIPBOARD_BYTES + 10)
        client, received = self.make_client(self.cut_text(body))
        client._handle_server_message()  # must not raise
        self.assertEqual(received, [], "oversized text should be dropped")

    def test_the_limits_are_ordered_sensibly(self):
        self.assertLess(MAX_CLIPBOARD_BYTES, CLIPBOARD_HARD_LIMIT)


class ClipboardLoopTest(unittest.TestCase):
    """Text from the server must not be sent straight back to it."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def stub_for(sent):
        class Stub:
            width, height = REMOTE_W, REMOTE_H
            desktop_name = "stub"
            framebuffer = bytearray(REMOTE_W * REMOTE_H * 4)

            def send_clipboard(self, text):
                sent.append(text)
                return True

            def stop(self):
                pass

        return Stub()

    def make_window(self):
        window = MainWindow()
        self.addCleanup(window.close)
        sent = []
        window.client = self.stub_for(sent)
        window.view.attach(window.client)
        window.share_clipboard = True
        return window, sent

    def test_text_from_the_server_is_not_echoed_back(self):
        window, sent = self.make_window()
        window._on_remote_clipboard("from the mac")
        QApplication.clipboard().text()  # the local change signal has fired
        window._on_local_clipboard()
        self.assertEqual(sent, [], "server text bounced straight back")

    def test_locally_copied_text_is_sent_once(self):
        window, sent = self.make_window()
        QApplication.clipboard().setText("typed here")
        window._on_local_clipboard()
        window._on_local_clipboard()  # a second signal for the same text
        self.assertEqual(sent, ["typed here"])

    def test_nothing_is_sent_when_sharing_is_off(self):
        window, sent = self.make_window()
        window.share_clipboard = False
        QApplication.clipboard().setText("private")
        window._on_local_clipboard()
        self.assertEqual(sent, [])

    def test_nothing_is_received_when_sharing_is_off(self):
        window, _ = self.make_window()
        QApplication.clipboard().setText("local text")
        window.share_clipboard = False
        window._on_remote_clipboard("remote text")
        self.assertEqual(QApplication.clipboard().text(), "local text")

    def test_text_copied_before_connecting_is_sent_once_the_session_is_up(self):
        """Copy on Windows, then connect: the remote must still get it."""
        window, sent = self.make_window()
        window.client = None
        QApplication.clipboard().setText("copied before connecting")
        window._on_local_clipboard()          # no session yet, goes nowhere
        self.assertEqual(sent, [])

        window.client = self.stub_for(sent)   # session comes up
        window._clipboard_echo = None
        window._clipboard_primed = False
        window._on_resize(REMOTE_W, REMOTE_H)
        self.assertEqual(sent, ["copied before connecting"],
                         "clipboard was never offered to the new session")

    def test_text_dropped_mid_handshake_is_not_marked_as_sent(self):
        """Recording it as sent would mean it was never offered again."""
        window, sent = self.make_window()

        class NotReady:
            def send_clipboard(self, text):
                return False  # the handshake has not finished

            def stop(self):
                pass

        window.client = NotReady()
        QApplication.clipboard().setText("during handshake")
        window._on_local_clipboard()
        self.assertIsNone(window._clipboard_echo,
                          "text that never left was recorded as sent")

        window.client = self.stub_for(sent)
        window._on_local_clipboard()
        self.assertEqual(sent, ["during handshake"], "the retry never happened")

    def test_nothing_is_sent_without_a_session(self):
        window, sent = self.make_window()
        window.client = None
        QApplication.clipboard().setText("no session")
        window._on_local_clipboard()
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
