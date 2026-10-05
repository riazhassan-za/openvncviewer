# OpenVNCViewer - a VNC viewer for macOS Screen Sharing with window scaling.
# Copyright (C) 2026 The OpenVNCViewer contributors
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
"""Text insertion via simulated key presses for remote input."""

import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal, QObject


class TextSender(QObject):
    """Sends text to remote host by simulating individual key presses.

    This allows convenient password input and other text entry through
    keyboard simulation rather than clipboard manipulation.
    """

    finished = Signal()
    error = Signal(str)

    # Delay between key presses in milliseconds to avoid overwhelming the server
    DEFAULT_DELAY_MS = 50

    def __init__(self, client, delay_ms=DEFAULT_DELAY_MS):
        """Initialize the text sender.

        Args:
            client: RFBClient instance for sending key events
            delay_ms: Milliseconds to wait between key presses
        """
        super().__init__()
        self._client = client
        self._delay_ms = delay_ms
        self._thread = None
        self._stop_flag = False

    def send_text(self, text):
        """Send text by simulating key presses.

        Args:
            text: String to send to the remote host
        """
        if not self._client:
            self.error.emit("No active connection")
            return

        if not text:
            self.finished.emit()
            return

        # Run in background thread to avoid blocking UI
        self._stop_flag = False
        self._thread = threading.Thread(
            target=self._send_text_thread,
            args=(text,),
            daemon=True
        )
        self._thread.start()

    def _send_text_thread(self, text):
        """Worker thread: send each character as a key press."""
        try:
            for char in text:
                if self._stop_flag:
                    break

                keysym = self._char_to_keysym(char)
                if keysym is not None:
                    # Press the key
                    self._client.send_key(keysym, True)
                    time.sleep(self._delay_ms / 1000.0)
                    # Release the key
                    self._client.send_key(keysym, False)
                    time.sleep(self._delay_ms / 1000.0)

            self.finished.emit()
        except Exception as e:
            self.error.emit(f"Error sending text: {str(e)}")

    @staticmethod
    def _char_to_keysym(char):
        """Convert a character to X11 keysym for RFB protocol.

        Handles printable ASCII and common special characters.
        """
        code = ord(char)

        # Printable ASCII characters
        if 32 <= code <= 126:
            return code

        # Common special characters
        special_chars = {
            '\n': 0xFF0D,  # Return
            '\r': 0xFF0D,  # Return
            '\t': 0xFF09,  # Tab
            ' ': 0x20,     # Space
        }

        if char in special_chars:
            return special_chars[char]

        # For other characters, try Unicode mapping (limited support)
        if code < 0x100:
            return code

        # Extended Unicode characters may not be supported
        # Return None to skip them
        return None

    def stop(self):
        """Stop sending text if currently in progress."""
        self._stop_flag = True
        if self._thread:
            self._thread.join(timeout=1.0)
