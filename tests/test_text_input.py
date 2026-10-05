"""Tests for the text input feature."""

import time
import unittest
from unittest.mock import Mock

from openvncviewer.text_input import TextSender


class TestTextSenderCharConversion(unittest.TestCase):
    """Test character to keysym conversion."""

    def test_char_to_keysym_printable_ascii(self):
        """Test conversion of printable ASCII characters."""
        test_cases = [
            ('A', ord('A')),
            ('z', ord('z')),
            ('0', ord('0')),
            ('9', ord('9')),
            (' ', 0x20),
            ('!', ord('!')),
            ('~', ord('~')),
        ]
        for char, expected_sym in test_cases:
            with self.subTest(char=char):
                result = TextSender._char_to_keysym(char)
                self.assertEqual(result, expected_sym)

    def test_char_to_keysym_special_chars(self):
        """Test conversion of special characters."""
        test_cases = [
            ('\n', 0xFF0D),  # Return
            ('\r', 0xFF0D),  # Return
            ('\t', 0xFF09),  # Tab
        ]
        for char, expected_sym in test_cases:
            with self.subTest(char=repr(char)):
                result = TextSender._char_to_keysym(char)
                self.assertEqual(result, expected_sym)

    def test_char_to_keysym_unsupported(self):
        """Test unsupported characters return None."""
        # Extended Unicode character
        result = TextSender._char_to_keysym('\u0100')  # Ā (A with macron)
        self.assertIsNone(result)

    def test_char_to_keysym_ascii_range(self):
        """Test that all ASCII printable characters map correctly."""
        # Test the full printable ASCII range
        for i in range(32, 127):  # Space to ~
            char = chr(i)
            keysym = TextSender._char_to_keysym(char)
            self.assertIsNotNone(
                keysym,
                f"Character {repr(char)} (ord={i}) should have a keysym"
            )
            self.assertEqual(
                keysym, i,
                f"Character {repr(char)} should map to its ASCII code"
            )


class TestTextSenderInit(unittest.TestCase):
    """Test TextSender initialization."""

    def test_init_defaults(self):
        """Test initialization with default parameters."""
        mock_client = Mock()
        sender = TextSender(mock_client)

        self.assertEqual(sender._client, mock_client)
        self.assertEqual(sender._delay_ms, TextSender.DEFAULT_DELAY_MS)
        self.assertFalse(sender._stop_flag)
        self.assertIsNone(sender._thread)

    def test_init_custom_delay(self):
        """Test initialization with custom delay."""
        mock_client = Mock()
        sender = TextSender(mock_client, delay_ms=100)

        self.assertEqual(sender._client, mock_client)
        self.assertEqual(sender._delay_ms, 100)


class TestTextSenderSendText(unittest.TestCase):
    """Test TextSender send_text functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_client = Mock()
        self.sender = TextSender(self.mock_client, delay_ms=5)

    def test_send_text_empty(self):
        """Test sending empty text completes immediately."""
        finished = []
        self.sender.finished.connect(lambda: finished.append(True))

        self.sender.send_text("")

        self.assertEqual(finished, [True])

    def test_send_text_no_client(self):
        """Test sending text without a client returns error."""
        self.sender._client = None
        errors = []
        self.sender.error.connect(errors.append)

        self.sender.send_text("test")

        self.assertEqual(errors, ["No active connection"])

    def test_send_text_single_character(self):
        """Test sending a single character."""
        self.sender.send_text("A")

        # Give thread time to process
        if self.sender._thread:
            self.sender._thread.join(timeout=1.0)

        # Verify key was pressed and released
        self.mock_client.send_key.assert_called()
        # Should be called at least twice: press and release
        self.assertGreaterEqual(self.mock_client.send_key.call_count, 2)

    def test_send_text_multiple_characters(self):
        """Test sending multiple characters."""
        text = "Test123"
        self.sender.send_text(text)

        if self.sender._thread:
            self.sender._thread.join(timeout=2.0)

        # Each character should generate at least 2 calls (press + release)
        self.assertGreaterEqual(
            self.mock_client.send_key.call_count,
            len(text) * 2
        )

    def test_send_text_with_special_chars(self):
        """Test sending text with special characters."""
        text = "Hello\tWorld\n"
        self.sender.send_text(text)

        if self.sender._thread:
            self.sender._thread.join(timeout=2.0)

        # All characters should be processed
        self.mock_client.send_key.assert_called()

    def test_stop(self):
        """Test stopping text transmission."""
        self.sender.send_text("A" * 100)
        time.sleep(0.1)  # Let some characters be sent
        self.sender.stop()

        if self.sender._thread:
            self.sender._thread.join(timeout=2.0)

        # Should have sent at least one but not all characters
        call_count = self.mock_client.send_key.call_count
        self.assertGreater(call_count, 0)
        self.assertLess(call_count, 100 * 2)  # Not all characters


class TestTextSenderPassword(unittest.TestCase):
    """Test sending password-like strings."""

    def test_send_password_format(self):
        """Test sending a password-like string."""
        mock_client = Mock()
        sender = TextSender(mock_client, delay_ms=5)

        password = "P@ss123"
        sender.send_text(password)

        if sender._thread:
            sender._thread.join(timeout=2.0)

        # Password should be sent successfully
        self.assertGreater(mock_client.send_key.call_count, 0)

    def test_send_complex_password(self):
        """Test sending a complex password with special characters."""
        mock_client = Mock()
        sender = TextSender(mock_client, delay_ms=5)

        password = "P@ss!123#"
        sender.send_text(password)

        if sender._thread:
            sender._thread.join(timeout=2.0)

        # All characters should be processed
        expected_calls = len(password) * 2  # press + release for each
        self.assertEqual(mock_client.send_key.call_count, expected_calls)


if __name__ == '__main__':
    unittest.main()
