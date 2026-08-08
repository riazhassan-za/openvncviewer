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

from pathlib import Path

from PySide6.QtCore import (QEvent, QObject, QPoint, QRect, QRectF, QSize,
                            QStandardPaths, Qt, QTimer, Signal)
from PySide6.QtGui import (QAction, QColor, QImage, QKeySequence, QPainter,
                           QPixmap)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QPushButton, QSlider,
                               QSpinBox, QVBoxLayout, QWidget)

from . import __version__, secretstore
from .history import ServerHistory
from .rfb import RFBClient

HOMEPAGE = "https://github.com/riazhassan-za/openvncviewer"

# Verified as a valid bech32 segwit v0 mainnet address before being shipped:
# a mistyped address would send donations nowhere recoverable.
DONATION_ADDRESS = "bc1qxq4n6x3safp6wglz76gdy93zhpfcw9af29cv3g"
DONATION_MESSAGE = (
    "Tired of being ripped off for basic software that should be free? "
    "Send donations to help fund ad-free/subs-free software for great justice.")

# Separates the friendly name from the host in the recent-servers dropdown.
HISTORY_SEPARATOR = " — "


def default_history_path():
    """Where the recent-servers list lives, per platform conventions."""
    root = QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)
    return Path(root or ".") / "servers.json"


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

# One notch of a standard mouse wheel, in eighths of a degree (Qt's unit).
WHEEL_NOTCH = 120
# Clicks sent per notch. The minimum is deliberately 1 - the far left of the
# slider passes the wheel through untouched - and the default sits mid-track.
WHEEL_SPEED_MIN, WHEEL_SPEED_MAX = 1, 100
WHEEL_SPEED_DEFAULT = (WHEEL_SPEED_MIN + WHEEL_SPEED_MAX) // 2
# Ceiling on the clicks one wheel event may produce, so a fast flick cannot
# flood the server. Must stay well above WHEEL_SPEED_MAX or it would quietly
# cap the top of the slider instead of just catching runaway flicks.
WHEEL_CLICK_LIMIT = 500

# RFB has no motion compression, so every mouse-move event becomes a message on
# the wire. Coalesce them to roughly one frame's worth.
POINTER_INTERVAL_MS = 16


# X11 keysyms for the two modifiers whose meaning we may swap.
SUPER_L = 0xFFEB  # macOS reads this as Command
ALT_L = 0xFFE9    # macOS reads this as Option


def _keysym(event, alt_is_command=False):
    key = event.key()
    if alt_is_command:
        # The key beside the space bar is Alt on a PC and Command on a Mac, so
        # this is the positionally faithful mapping - and the only reachable
        # one, since Windows swallows most Win+key combinations itself.
        if key == Qt.Key_Alt:
            return SUPER_L
        if key == Qt.Key_Meta:
            return ALT_L
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
    damaged = Signal(int, int, int, int)
    disconnected = Signal(object)
    clipboard = Signal(str)


class RemoteView(QWidget):
    """Draws the remote framebuffer scaled to fill the widget."""

    def __init__(self):
        super().__init__()
        # Set before touching Qt: setters below dispatch events straight into
        # our event() override, which reads these attributes.
        self._client = None
        self._image = None
        self._scaled = None
        self._buttons = 0
        self._pressed = {}
        self._pending_motion = None
        self.wheel_speed = WHEEL_SPEED_DEFAULT
        self.alt_is_command = True
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAutoFillBackground(False)

        self._motion_timer = QTimer(self)
        self._motion_timer.setInterval(POINTER_INTERVAL_MS)
        self._motion_timer.timeout.connect(self._flush_motion)

    def attach(self, client):
        self._client = client
        self._image = None
        self._scaled = None
        self._pressed.clear()
        self._buttons = 0

    def detach(self):
        self._client = None
        self._motion_timer.stop()
        self._pending_motion = None
        # Drop the last frame. Leaving it up implies a live session, and the
        # image wraps a framebuffer whose owner has just gone away.
        self._image = None
        self._scaled = None
        self._pressed.clear()
        self._buttons = 0
        self.updateGeometry()
        self.update()

    def on_resize(self, width, height):
        if self._client is None:
            return
        # Wraps the client's framebuffer without copying, so later updates
        # to that bytearray show up on the next repaint.
        self._image = QImage(self._client.framebuffer, width, height,
                             width * 4, QImage.Format_RGB32)
        self._scaled = None
        self.updateGeometry()
        self.update()

    def resizeEvent(self, event):
        self._scaled = None  # widget size changed, so the cache is the wrong size
        super().resizeEvent(event)

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

    def _rescale(self, region=None):
        """Refresh the cached scaled pixmap; return the widget rect to repaint.

        Smooth-scaling the whole desktop on every frame is the expensive part
        of painting, and almost all of it is redundant when a few hundred
        pixels changed. With a region only that part is rescaled into the
        cache, so a blinking cursor costs a tile, not a full screen.
        """
        target = self.target_rect()
        if self._image is None or self._image.isNull() or target.isEmpty():
            self._scaled = None
            return None

        # Allocate in device pixels, not logical ones. At 150% or 200% display
        # scaling a logical-sized pixmap holds well under half the pixels the
        # screen can show, and Qt stretches it - the remote desktop arrives
        # sharp and gets blurred on the way to the glass.
        #
        # Setting devicePixelRatio on the pixmap keeps every coordinate below
        # logical: QPainter scales by the ratio itself, so the destination
        # maths and the returned repaint rect need no adjustment.
        ratio = self.devicePixelRatioF()
        device_size = QSize(round(target.width() * ratio),
                            round(target.height() * ratio))
        if (self._scaled is None
                or self._scaled.size() != device_size
                or self._scaled.devicePixelRatio() != ratio):
            self._scaled = QPixmap(device_size)
            self._scaled.setDevicePixelRatio(ratio)
            region = None  # nothing valid to keep, so rebuild it all

        if region is None:
            source = QRectF(self._image.rect())
        else:
            x, y, w, h = region
            # A pixel of margin so the smooth filter sees the same neighbours
            # it would during a full rescale, which keeps seams from showing.
            left, top = max(0, x - 1), max(0, y - 1)
            right = min(self._image.width(), x + w + 1)
            bottom = min(self._image.height(), y + h + 1)
            if right <= left or bottom <= top:
                return None
            source = QRectF(left, top, right - left, bottom - top)

        scale_x = target.width() / self._image.width()
        scale_y = target.height() / self._image.height()
        destination = QRectF(source.x() * scale_x, source.y() * scale_y,
                             source.width() * scale_x, source.height() * scale_y)

        painter = QPainter(self._scaled)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawImage(destination, self._image, source)
        painter.end()

        return (destination.toAlignedRect()
                .translated(target.topLeft())
                .adjusted(-1, -1, 1, 1))

    def on_damage(self, x, y, w, h):
        dirty = self._rescale((x, y, w, h))
        self.update() if dirty is None else self.update(dirty)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(24, 24, 24))
        if self._scaled is None:
            self._rescale()
        if self._scaled is not None:
            # Already at widget scale, so this is a blit rather than a resample.
            painter.drawPixmap(self.target_rect().topLeft(), self._scaled)

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
        # Send the first move straight away so tracking feels immediate, then
        # coalesce the rest: only the newest position matters, and the ones in
        # between would just be wire traffic the server has to work through.
        self._pending_motion = event.position()
        if not self._motion_timer.isActive():
            self._flush_motion()
            self._motion_timer.start()

    def _flush_motion(self):
        if self._pending_motion is None:
            self._motion_timer.stop()
            return
        position, self._pending_motion = self._pending_motion, None
        self._send_pointer(position)

    def _send_now(self, position):
        """Button changes must not be reordered behind a coalesced move."""
        self._pending_motion = None
        self._send_pointer(position)

    def mousePressEvent(self, event):
        self.setFocus()
        self._buttons |= BUTTONS.get(event.button(), 0)
        self._send_now(event.position())

    def mouseReleaseEvent(self, event):
        self._buttons &= ~BUTTONS.get(event.button(), 0)
        self._send_now(event.position())

    def wheel_clicks(self, delta):
        """How many scroll clicks one wheel event should send.

        RFB has no scroll magnitude - a wheel notch is a button press. Remote
        sessions therefore feel sluggish, so the clicks per notch are
        multiplied by the user's chosen speed. At the minimum this is the raw,
        unamplified notch count.
        """
        notches = max(1, round(abs(delta) / WHEEL_NOTCH))
        return min(notches * self.wheel_speed, WHEEL_CLICK_LIMIT)

    def wheelEvent(self, event):
        point = self._remote_point(event.position())
        if point is None or not self._client:
            return
        steps = event.angleDelta()
        for delta, down, up in ((steps.y(), 8, 16), (steps.x(), 32, 64)):
            if not delta:
                continue
            button = down if delta > 0 else up
            for _ in range(self.wheel_clicks(delta)):
                self._client.send_pointer(point.x(), point.y(),
                                          self._buttons | button)
                self._client.send_pointer(point.x(), point.y(), self._buttons)

    def event(self, event):
        # Claim the key before Qt decides it is a shortcut. Without this Alt
        # opens the menu bar and never reaches the remote at all, and Alt+F is
        # eaten as a menu mnemonic. F11 stays ours, or full screen would be a
        # one-way door with no way back.
        if (self._client and event.type() == QEvent.Type.ShortcutOverride
                and event.key() != Qt.Key_F11):
            event.accept()
            return True

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
            keysym = _keysym(event, self.alt_is_command)
            if keysym is None:
                return False
            self._pressed[event.key()] = keysym
        else:
            # Release the keysym we pressed: modifiers may have changed since.
            keysym = (self._pressed.pop(event.key(), None)
                      or _keysym(event, self.alt_is_command))
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
    def __init__(self, host="", port=5900, username="",
                 wheel_speed=WHEEL_SPEED_DEFAULT, share_clipboard=True,
                 alt_is_command=True, history=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to a VNC server")
        self._history = history if history is not None else ServerHistory(
            default_history_path())
        # Guards the two-way link between the host box and the fields it fills.
        self._updating = False

        self.host = QComboBox()
        self.host.setEditable(True)
        self.host.setInsertPolicy(QComboBox.NoInsert)
        self.host.lineEdit().setPlaceholderText("hostname or IP")
        self.server_name = QLineEdit()
        self.server_name.setPlaceholderText("optional label for this server")
        self.server_name.setToolTip(
            "A name of your choosing for this server. Shown in the recent list "
            "and in the window title once connected.")

        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(port)
        self.username = QLineEdit(username)
        self.username.setPlaceholderText("macOS account - blank for other servers")
        self.username.setToolTip(
            "A macOS account name, for Screen Sharing.\n"
            "Leave blank for a standard VNC server, which authenticates with a "
            "password alone.")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setToolTip(
            "The macOS account password, or the VNC password.\n"
            "Standard VNC passwords are limited to 8 characters by the "
            "protocol; anything longer is ignored.")

        self.alt_is_command = QCheckBox("Send Alt as Command (macOS)")
        self.alt_is_command.setChecked(alt_is_command)
        self.alt_is_command.setToolTip(
            "Alt sits where Command does on a Mac keyboard, so this makes\n"
            "Alt+C and Alt+V copy and paste on the remote Mac. It is also\n"
            "the only reachable choice: Windows keeps most Win+key\n"
            "combinations for itself.\n\n"
            "Untick for a non-Apple server, where Alt should stay Alt.")

        self.share_clipboard = QCheckBox("Share clipboard with this server")
        self.share_clipboard.setChecked(share_clipboard)
        self.share_clipboard.setToolTip(
            "Copy on either machine, paste on the other.\n"
            "Note that anything you copy locally is then sent to the server, "
            "and RFB\ncarries it in the clear. Untick this when that matters."
            "\n\nmacOS Screen Sharing does not carry the clipboard over RFB, "
            "so this\nhas no effect when connected to a Mac.")

        self.save_password = QCheckBox("Save password for this server")
        if secretstore.available():
            self.save_password.setToolTip(
                "Encrypts the password with your Windows account key, so the "
                "saved file is useless\non another machine or to another user.\n"
                "It does not protect against programs running as you.")
        else:
            self.save_password.setEnabled(False)
            self.save_password.setToolTip(
                "Unavailable: this platform has no facility to encrypt the "
                "password with your\nlogin. It will not be stored in the clear.")

        form = QFormLayout()
        form.addRow("Host", self.host)
        form.addRow("Server name", self.server_name)
        form.addRow("Port", self.port)
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        form.addRow("", self.save_password)

        # Laid out by hand rather than with QDialogButtonBox: the order below
        # is fixed, and a button box reorders by role per platform.
        self.connect_button = QPushButton("Connect")
        self.connect_button.setDefault(True)
        self.connect_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        self.remove_button = QPushButton("Remove Server")
        self.remove_button.setToolTip(
            "Forget the server shown above, along with any saved password.\n"
            "Removes only that one, so click again for the next.")
        self.remove_button.clicked.connect(self.remove_selected)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.connect_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.remove_button)

        # Wired after the fields exist: filling the box fires the handler.
        self.reload_history()
        self.host.setEditText(host)
        self.host.activated.connect(self._history_selected)
        self.host.editTextChanged.connect(self._host_changed)
        self._host_changed(host)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._build_options(wheel_speed))
        layout.addStretch()
        layout.addLayout(buttons)

    def _build_options(self, wheel_speed):
        """The Options group: everything that tunes the session, not the login."""
        self.wheel_speed = QSlider(Qt.Horizontal)
        self.wheel_speed.setRange(WHEEL_SPEED_MIN, WHEEL_SPEED_MAX)
        self.wheel_speed.setValue(wheel_speed)
        self.wheel_speed.setTickPosition(QSlider.TicksBelow)
        self.wheel_speed.setTickInterval((WHEEL_SPEED_MAX - WHEEL_SPEED_MIN) // 10)
        self.wheel_speed.setSingleStep(1)
        self.wheel_speed.setPageStep(10)
        self.wheel_speed.setToolTip(
            "How many scroll clicks to send per wheel notch.\n"
            "Leftmost sends the wheel untouched; move right if scrolling "
            "feels sluggish over the network.")
        # No explicit fonts anywhere here: inheriting the dialog's font is what
        # keeps this consistent with the rest of the interface.
        ends = QHBoxLayout()
        ends.addWidget(QLabel("Raw"))
        ends.addStretch()
        ends.addWidget(QLabel("Faster"))

        options = QGroupBox("Options")
        inner = QVBoxLayout(options)
        inner.addWidget(QLabel("Mouse wheel speed"))
        inner.addWidget(self.wheel_speed)
        inner.addLayout(ends)
        inner.addWidget(self.share_clipboard)
        inner.addWidget(self.alt_is_command)
        return options

    # ------------------------------------------------------------- history

    def reload_history(self):
        """Refill the dropdown. The host itself is the item data, not its text."""
        self._updating = True
        try:
            self.host.clear()
            for entry in self._history.entries():
                label = (f"{entry['name']}{HISTORY_SEPARATOR}{entry['host']}"
                         if entry["name"] else entry["host"])
                self.host.addItem(label, entry["host"])
        finally:
            self._updating = False

    def _history_selected(self, index):
        """Qt drops the decorated item text into the line edit; put the host back."""
        stored = self.host.itemData(index)
        if stored:
            self.host.setEditText(stored)

    def _host_changed(self, text):
        entry = self._history.find(text)
        # Nothing to remove unless this host is actually in the list.
        self.remove_button.setEnabled(entry is not None)
        if self._updating:
            return
        self._updating = True
        try:
            if entry is None:
                # A host never seen before carries no name; leave whatever port
                # and username were typed alone.
                self.server_name.clear()
                self.password.clear()
                self.save_password.setChecked(False)
                return
            self.server_name.setText(entry["name"])
            self.port.setValue(entry["port"])
            self.username.setText(entry["username"])

            saved = secretstore.decrypt(entry.get("password", ""))
            # A token that will not decrypt - written by another user, or on
            # another machine - is treated as no saved password at all.
            self.password.setText(saved or "")
            self.save_password.setChecked(bool(saved))
        finally:
            self._updating = False

    def remove_selected(self):
        """Forget just the server currently shown, so repeated clicks work."""
        if not self._history.remove(self.host.currentText()):
            return
        self.reload_history()

        # Land on the next server so a second click removes that one, rather
        # than leaving a stale host in the box that is no longer in the list.
        remaining = self._history.hosts()
        self._updating = True
        try:
            self.host.setEditText(remaining[0] if remaining else "")
        finally:
            self._updating = False
        self._host_changed(self.host.currentText())

    def values(self):
        return (self.host.currentText().strip(), self.port.value(),
                self.username.text(), self.password.text(),
                self.wheel_speed.value(), self.server_name.text().strip(),
                self.save_password.isChecked(),
                self.share_clipboard.isChecked(),
                self.alt_is_command.isChecked())


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
        self.history = ServerHistory(default_history_path())
        self._pending_history = None
        self.server_name = ""
        self.signals = ClientSignals()
        self.signals.resized.connect(self._on_resize)
        self.signals.damaged.connect(self.view.on_damage)
        self.signals.disconnected.connect(self._on_disconnect)
        self.signals.clipboard.connect(self._on_remote_clipboard)

        self.share_clipboard = True
        self._clipboard_primed = False
        # Text we put on the clipboard ourselves, or last sent. Without this
        # the server's text lands locally, fires dataChanged, and goes straight
        # back - an endless round trip on every copy.
        self._clipboard_echo = None
        QApplication.clipboard().dataChanged.connect(self._on_local_clipboard)

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction("&Connect...", self.prompt_connect)
        file_menu.addAction("&Disconnect", self.disconnect)
        file_menu.addSeparator()
        file_menu.addAction("E&xit", self.close)

        self._was_maximized = False
        self.fullscreen_action = QAction("&Full screen", self)
        self.fullscreen_action.setCheckable(True)
        self.fullscreen_action.setShortcut(QKeySequence(Qt.Key_F11))
        self.fullscreen_action.setShortcutContext(Qt.ApplicationShortcut)
        self.fullscreen_action.toggled.connect(self.set_fullscreen)
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.fullscreen_action)
        # Also owned by the window, so F11 still works once the menu bar is
        # hidden - otherwise full screen would be a one-way door.
        self.addAction(self.fullscreen_action)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("&About", self.show_about)

        self.last_connection = ("", 5900, "", WHEEL_SPEED_DEFAULT)

    def set_fullscreen(self, enabled):
        """Give the whole screen to the remote desktop, chrome included.

        The framebuffer is not renegotiated - this only changes how much room
        the scaler has, so the remote desktop simply gets drawn larger.
        """
        # Whatever is held down now would otherwise stick: the remote never
        # sees the release, because the window is busy changing state.
        self.view.release_all_keys()

        self.menuBar().setVisible(not enabled)
        self.statusBar().setVisible(not enabled)
        if enabled:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
        elif self._was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.view.setFocus()

    def show_about(self):
        # Built rather than using QMessageBox.about() so the Bitcoin address is
        # selectable for copying and the links are actually clickable.
        about = QMessageBox(self)
        about.setWindowTitle("About OpenVNCViewer")
        about.setTextFormat(Qt.RichText)
        about.setTextInteractionFlags(Qt.TextBrowserInteraction)
        about.setText(
            f"<b>OpenVNCViewer for Windows {__version__}</b>"
            "<p>A VNC viewer for 64-bit Windows, with support for macOS Screen "
            "Sharing and standard VNC servers. It scales the remote desktop to "
            "whatever size the client window is.</p>"
            # The GPL asks interactive programs to carry a warranty notice.
            "<p>Copyright &copy; 2026 The OpenVNCViewer contributors.<br>"
            "This program comes with ABSOLUTELY NO WARRANTY. It is free "
            "software, and you are welcome to redistribute it under the terms "
            "of the GNU General Public License, version 3 or later.</p>"
            f'<p><a href="{HOMEPAGE}">{HOMEPAGE}</a></p>'
            "<hr>"
            f"<p>{DONATION_MESSAGE}</p>"
            "<p>Send Bitcoin:<br>"
            f'<a href="bitcoin:{DONATION_ADDRESS}" '
            'style="font-family: monospace;">'
            f"{DONATION_ADDRESS}</a></p>")
        # QMessageBox does not always let its label follow links on its own.
        for label in about.findChildren(QLabel):
            label.setOpenExternalLinks(True)
        about.exec()

    def prompt_connect(self):
        dialog = ConnectDialog(*self.last_connection, history=self.history,
                               parent=self)
        if dialog.exec() == QDialog.Accepted:
            (host, port, username, password, wheel_speed, name,
             save_password, share_clipboard, alt_is_command) = dialog.values()
            if host:
                self.connect_to(host, port, username, password, wheel_speed,
                                name, save_password, share_clipboard,
                                alt_is_command)

    def connect_to(self, host, port, username, password,
                   wheel_speed=WHEEL_SPEED_DEFAULT, server_name="",
                   save_password=False, share_clipboard=True,
                   alt_is_command=True):
        self.disconnect()
        self.share_clipboard = share_clipboard
        self.view.alt_is_command = alt_is_command
        # A fresh session has seen none of our clipboard yet, so forget what we
        # told the last one and offer the current contents once it is up.
        self._clipboard_echo = None
        self._clipboard_primed = False
        self.view.wheel_speed = wheel_speed
        self.last_connection = (host, port, username, wheel_speed)
        # Encrypt now, while the password is still in hand. An empty token
        # means "forget whatever was saved for this server".
        token = secretstore.encrypt(password) if save_password else None
        # Held until the session actually comes up: the list is of servers
        # connected to, not of hosts typed.
        self._pending_history = (host, port, username, server_name, token or "")
        # Revoking a saved password is not a history update and must not wait
        # for the connection to succeed. Untick the box, fail to connect, and
        # deferring this would leave the old token on disk after the user asked
        # for it to be gone. Clears the token in place, so an unknown host still
        # records nothing and a known one keeps its place in the list.
        if not save_password:
            self.history.forget_password(host)
        self.server_name = server_name
        self.status.setText(f"Connecting to {host}:{port}...")
        self.client = RFBClient(
            host, port, username, password,
            on_resize=self.signals.resized.emit,
            on_damage=self.signals.damaged.emit,
            on_disconnect=self.signals.disconnected.emit,
            on_clipboard=self.signals.clipboard.emit,
        )
        self.view.attach(self.client)
        self.client.start()

    def disconnect(self):
        if self.client:
            self.client.stop()
            self.client = None
        self.view.detach()
        self.server_name = ""
        self.setWindowTitle("OpenVNCViewer")
        self.status.setText("Not connected")

    def _on_resize(self, width, height):
        self.view.on_resize(width, height)
        if self._pending_history:
            self.history.remember(*self._pending_history)
            self._pending_history = None

        # Anything copied before the session came up was never sent - there was
        # no connection to send it over - so offer it now. Otherwise you copy
        # on Windows, connect, and find the remote paste gives you nothing.
        if not self._clipboard_primed:
            self._clipboard_primed = True
            self._on_local_clipboard()

        label = self.server_name or (self.client.desktop_name if self.client
                                     else "")
        self.setWindowTitle(f"{label} - OpenVNCViewer" if label
                            else "OpenVNCViewer")
        where = f"Connected to {label}" if label else "Connected"
        self.status.setText(f"{where} - remote desktop {width}x{height}, "
                            "scaled to window")

    def _on_remote_clipboard(self, text):
        """The server copied something; mirror it locally."""
        if not self.share_clipboard or not text:
            return
        self._clipboard_echo = text
        QApplication.clipboard().setText(text)

    def _on_local_clipboard(self):
        """We copied something; offer it to the server."""
        if not (self.share_clipboard and self.client):
            return
        text = QApplication.clipboard().text()
        if not text or text == self._clipboard_echo:
            return  # came from the server, or we already sent it
        # Only remember it as sent if it actually went. Until the handshake
        # finishes the client refuses to send, and recording it here would mean
        # that text was never offered again.
        if self.client.send_clipboard(text):
            self._clipboard_echo = text

    def _on_disconnect(self, reason):
        self.view.detach()
        self.client = None
        self._pending_history = None  # never connected, so nothing to remember
        # A window still titled after the server it is no longer showing reads
        # as a live session.
        self.server_name = ""
        self.setWindowTitle("OpenVNCViewer")
        self.status.setText(f"Disconnected: {reason}" if reason else "Disconnected")
        if reason:
            QMessageBox.warning(self, "Disconnected", reason)

    def closeEvent(self, event):
        self.disconnect()
        super().closeEvent(event)
