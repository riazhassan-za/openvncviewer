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
"""Qt user interface: a remote view that always scales to the window size."""

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel,
                               QLineEdit, QMainWindow, QMessageBox, QSpinBox,
                               QWidget)

from . import __version__
from .rfb import RFBClient

HOMEPAGE = "https://github.com/riazhassan-za/openvncviewer"

# Qt key -> X11 keysym. Meta maps to Super_L, which macOS treats as Command.
KEYSYMS = {
    Qt.Key_Backspace: 0xFF08, Qt.Key_Tab: 0xFF09, Qt.Key_Return: 0xFF0D,
    Qt.Key_Enter: 0xFF8D, Qt.Key_Escape: 0xFF1B, Qt.Key_Insert: 0xFF63,
    Qt.Key_Delete: 0xFFFF, Qt.Key_Home: 0xFF50, Qt.Key_End: 0xFF57,
    Qt.Key_PageUp: 0xFF55, Qt.Key_PageDown: 0xFF56, Qt.Key_Left: 0xFF51,
    Qt.Key_Up: 0xFF52, Qt.Key_Right: 0xFF53, Qt.Key_Down: 0xFF54,
    Qt.Key_Shift: 0xFFE1, Qt.Key_Control: 0xFFE3, Qt.Key_Alt: 0xFFE9,
    Qt.Key_Meta: 0xFFEB, Qt.Key_CapsLock: 0xFFE5, Qt.Key_Space: 0x20,
}
KEYSYMS.update({getattr(Qt, f"Key_F{n}"): 0xFFBD + n for n in range(1, 13)})

BUTTONS = {Qt.LeftButton: 1, Qt.MiddleButton: 2, Qt.RightButton: 4}


def _keysym(event):
    key = event.key()
    if key in KEYSYMS:
        return KEYSYMS[key]
    text = event.text()
    if text and text.isprintable():
        code = ord(text[0])
        return code if code < 0x100 else 0x01000000 + code
    if Qt.Key_A <= key <= Qt.Key_Z:  # e.g. Ctrl+A, whose text() is a control char
        return key + 0x20
    if 0x20 <= key <= 0xFF:
        return key
    return None


class ClientSignals(QObject):
    """Moves RFBClient callbacks from the network thread onto the UI thread."""

    resized = Signal(int, int)
    damaged = Signal()
    disconnected = Signal(object)


class RemoteView(QWidget):
    """Draws the remote framebuffer scaled to fill the widget."""

    def __init__(self):
        super().__init__()
        # Set before touching Qt: setters below dispatch events straight into
        # our event() override, which reads these attributes.
        self._client = None
        self._image = None
        self._buttons = 0
        self._pressed = {}
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAutoFillBackground(False)

    def attach(self, client):
        self._client = client
        self._image = None
        self._pressed.clear()
        self._buttons = 0

    def detach(self):
        self._client = None

    def on_resize(self, width, height):
        if self._client is None:
            return
        # Wraps the client's framebuffer without copying, so later updates
        # to that bytearray show up on the next repaint.
        self._image = QImage(self._client.framebuffer, width, height,
                             width * 4, QImage.Format_RGB32)
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        return self._image.size() if self._image else super().sizeHint()

    # -------------------------------------------------------------- painting

    def target_rect(self):
        """Where the remote desktop is drawn: largest aspect-correct fit."""
        if not self._image or self._image.isNull():
            return QRect()
        remote = self._image.size()
        scale = min(self.width() / remote.width(), self.height() / remote.height())
        width = max(1, round(remote.width() * scale))
        height = max(1, round(remote.height() * scale))
        return QRect((self.width() - width) // 2, (self.height() - height) // 2,
                     width, height)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(24, 24, 24))
        if self._image and not self._image.isNull():
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawImage(self.target_rect(), self._image, self._image.rect())

    # ----------------------------------------------------------------- input

    def _remote_point(self, position):
        """Map a widget position back through the scale to remote pixels."""
        rect = self.target_rect()
        if rect.isEmpty():
            return None
        x = (position.x() - rect.x()) * self._image.width() / rect.width()
        y = (position.y() - rect.y()) * self._image.height() / rect.height()
        return QPoint(min(max(int(x), 0), self._image.width() - 1),
                      min(max(int(y), 0), self._image.height() - 1))

    def _send_pointer(self, position):
        point = self._remote_point(position)
        if point is not None and self._client:
            self._client.send_pointer(point.x(), point.y(), self._buttons)

    def mouseMoveEvent(self, event):
        self._send_pointer(event.position())

    def mousePressEvent(self, event):
        self.setFocus()
        self._buttons |= BUTTONS.get(event.button(), 0)
        self._send_pointer(event.position())

    def mouseReleaseEvent(self, event):
        self._buttons &= ~BUTTONS.get(event.button(), 0)
        self._send_pointer(event.position())

    def wheelEvent(self, event):
        point = self._remote_point(event.position())
        if point is None or not self._client:
            return
        steps = event.angleDelta()
        for delta, down, up in ((steps.y(), 8, 16), (steps.x(), 32, 64)):
            if delta:
                button = down if delta > 0 else up
                self._client.send_pointer(point.x(), point.y(), self._buttons | button)
                self._client.send_pointer(point.x(), point.y(), self._buttons)

    def event(self, event):
        # Handled here rather than in keyPressEvent so Tab reaches the remote
        # host instead of moving focus.
        if self._client and event.type() in (QEvent.Type.KeyPress,
                                             QEvent.Type.KeyRelease):
            if self._handle_key(event):
                return True
        return super().event(event)

    def _handle_key(self, event):
        down = event.type() == QEvent.Type.KeyPress
        if down:
            keysym = _keysym(event)
            if keysym is None:
                return False
            self._pressed[event.key()] = keysym
        else:
            # Release the keysym we pressed: modifiers may have changed since.
            keysym = self._pressed.pop(event.key(), None) or _keysym(event)
            if keysym is None:
                return False
        self._client.send_key(keysym, down)
        return True

    def release_all_keys(self):
        for keysym in self._pressed.values():
            if self._client:
                self._client.send_key(keysym, False)
        self._pressed.clear()

    def focusOutEvent(self, event):
        self.release_all_keys()
        super().focusOutEvent(event)


class ConnectDialog(QDialog):
    def __init__(self, host="", port=5900, username="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to macOS Screen Sharing")
        self.host = QLineEdit(host)
        self.host.setPlaceholderText("hostname or IP")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(port)
        self.username = QLineEdit(username)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QFormLayout(self)
        layout.addRow("Host", self.host)
        layout.addRow("Port", self.port)
        layout.addRow("macOS user", self.username)
        layout.addRow("Password", self.password)
        layout.addRow(buttons)

    def values(self):
        return (self.host.text().strip(), self.port.value(),
                self.username.text(), self.password.text())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OpenVNCViewer")
        self.view = RemoteView()
        self.setCentralWidget(self.view)
        self.status = QLabel("Not connected")
        self.statusBar().addWidget(self.status)
        self.resize(1280, 800)

        self.client = None
        self.signals = ClientSignals()
        self.signals.resized.connect(self._on_resize)
        self.signals.damaged.connect(self.view.update)
        self.signals.disconnected.connect(self._on_disconnect)

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction("&Connect...", self.prompt_connect)
        file_menu.addAction("&Disconnect", self.disconnect)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("&About", self.show_about)

        self.last_connection = ("", 5900, "")

    def show_about(self):
        # The GPL asks interactive programs to carry a short warranty notice.
        QMessageBox.about(
            self, "About OpenVNCViewer",
            f"<b>OpenVNCViewer {__version__}</b>"
            "<p>A VNC viewer for macOS Screen Sharing that scales the remote "
            "desktop to whatever size the client window is.</p>"
            "<p>Copyright &copy; 2026 The OpenVNCViewer contributors.<br>"
            "This program comes with ABSOLUTELY NO WARRANTY. It is free "
            "software, and you are welcome to redistribute it under the terms "
            "of the GNU General Public License, version 3 or later.</p>"
            f'<p><a href="{HOMEPAGE}">{HOMEPAGE}</a></p>')

    def prompt_connect(self):
        dialog = ConnectDialog(*self.last_connection, parent=self)
        if dialog.exec() == QDialog.Accepted:
            host, port, username, password = dialog.values()
            if host:
                self.connect_to(host, port, username, password)

    def connect_to(self, host, port, username, password):
        self.disconnect()
        self.last_connection = (host, port, username)
        self.status.setText(f"Connecting to {host}:{port}...")
        self.client = RFBClient(
            host, port, username, password,
            on_resize=self.signals.resized.emit,
            on_damage=self.signals.damaged.emit,
            on_disconnect=self.signals.disconnected.emit,
        )
        self.view.attach(self.client)
        self.client.start()

    def disconnect(self):
        if self.client:
            self.client.stop()
            self.client = None
        self.view.detach()

    def _on_resize(self, width, height):
        self.view.on_resize(width, height)
        name = self.client.desktop_name if self.client else ""
        self.setWindowTitle(f"{name} - OpenVNCViewer" if name else "OpenVNCViewer")
        self.status.setText(f"Connected - remote desktop {width}x{height}, "
                            "scaled to window")

    def _on_disconnect(self, reason):
        self.view.detach()
        self.client = None
        self.status.setText(f"Disconnected: {reason}" if reason else "Disconnected")
        if reason:
            QMessageBox.warning(self, "Disconnected", reason)

    def closeEvent(self, event):
        self.disconnect()
        super().closeEvent(event)
