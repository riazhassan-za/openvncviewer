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
"""Left sidebar panel with saved VNC server bookmarks."""

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QMessageBox, QMenu, QCheckBox
)

# Qt's QWIDGETSIZE_MAX, which PySide6 does not export.
QWIDGETSIZE_MAX = (1 << 24) - 1


class HostListPanel(QWidget):
    """Left sidebar panel showing saved VNC servers for quick access."""

    # Signal emitted when user wants to connect to a server
    connect_requested = Signal(str, int, str)  # host, port, server_name
    add_requested = Signal()
    edit_requested = Signal(str, int)  # host, port
    disconnect_requested = Signal()
    refresh_requested = Signal()
    collapsed_changed = Signal(bool)

    def __init__(self, history, parent=None):
        """Initialize the host list panel.

        Args:
            history: ServerHistory instance with saved servers
            parent: Parent widget
        """
        super().__init__(parent)
        self.history = history

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Collapsed, the panel is just this button against the left edge.
        self.expand_btn = QPushButton("»")
        self.expand_btn.setFixedWidth(24)
        self.expand_btn.setToolTip("Show saved servers")
        self.expand_btn.clicked.connect(lambda: self.set_collapsed(False))
        self.expand_btn.hide()
        outer.addWidget(self.expand_btn, alignment=Qt.AlignTop)

        # No maximum: the main window gives the panel a share of its width.
        self.body = QWidget()
        self.body.setMinimumWidth(200)
        outer.addWidget(self.body)

        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Header
        header_layout = QHBoxLayout()
        header_label = QLabel("Saved Servers")
        header_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        header_layout.addWidget(header_label)

        # Refresh button
        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setMaximumWidth(30)
        self.refresh_btn.setToolTip("Refresh list")
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)
        header_layout.addStretch()
        header_layout.addWidget(self.refresh_btn)

        self.collapse_btn = QPushButton("«")
        self.collapse_btn.setMaximumWidth(30)
        self.collapse_btn.setToolTip("Hide saved servers")
        self.collapse_btn.clicked.connect(lambda: self.set_collapsed(True))
        header_layout.addWidget(self.collapse_btn)
        layout.addLayout(header_layout)

        # Alphabetical unless ticked; history itself is kept most recent first.
        self.sort_by_last_used = QCheckBox("Sort by last used")
        self.sort_by_last_used.toggled.connect(self.refresh_list)
        layout.addWidget(self.sort_by_last_used)

        # List widget for servers
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget {
                background-color: #f5f5f5;
                border: 1px solid #ddd;
                border-radius: 4px;
            }
            QListWidget::item {
                padding: 8px;
                border-radius: 3px;
            }
            QListWidget::item:hover {
                background-color: #e8e8e8;
            }
            QListWidget::item:selected {
                background-color: #0078d4;
                color: white;
            }
        """)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        layout.addWidget(self.list_widget)

        # Quick actions
        actions_layout = QHBoxLayout()

        self.add_btn = QPushButton("+ New")
        self.add_btn.setToolTip("Add a new server")
        self.add_btn.clicked.connect(self._on_add_clicked)
        actions_layout.addWidget(self.add_btn)

        self.edit_btn = QPushButton("✎ Edit")
        self.edit_btn.setToolTip("Edit selected server")
        self.edit_btn.clicked.connect(self._on_edit_clicked)
        actions_layout.addWidget(self.edit_btn)

        self.delete_btn = QPushButton("✕ Delete")
        self.delete_btn.setToolTip("Delete selected server")
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        actions_layout.addWidget(self.delete_btn)

        layout.addLayout(actions_layout)

        # Status indicator
        self.status_label = QLabel("No servers saved")
        self.status_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.status_label)

        layout.addStretch()

        self.refresh_list()

    def refresh_list(self):
        """Refresh the server list from history."""
        self.list_widget.clear()
        entries = self.history.entries()

        if not entries:
            self.status_label.setText("No servers saved")
            self.edit_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
            return

        if not self.sort_by_last_used.isChecked():
            entries.sort(key=lambda entry: self._format_entry(entry).casefold())

        for entry in entries:
            item_text = self._format_entry(entry)
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, entry)  # Store full entry data

            # Add icon based on connection type
            if entry.get("username"):
                item.setData(Qt.UserRole + 1, "macOS")  # ARD connection
            else:
                item.setData(Qt.UserRole + 1, "VNC")    # Standard VNC

            self.list_widget.addItem(item)

        count = len(entries)
        self.status_label.setText(f"{count} server{'s' if count != 1 else ''} saved")
        self.edit_btn.setEnabled(True)
        self.delete_btn.setEnabled(True)

    def set_collapsed(self, collapsed):
        """Fold the panel down to the expand button, or open it again."""
        self.body.setVisible(not collapsed)
        self.expand_btn.setVisible(collapsed)
        # Folded, it must not keep its share of the window's width.
        self.setMaximumWidth(self.expand_btn.width() if collapsed
                             else QWIDGETSIZE_MAX)
        self.collapsed_changed.emit(collapsed)

    def _format_entry(self, entry):
        """Format a server entry for display."""
        name = entry.get("name", "").strip()
        host = entry.get("host", "")
        port = entry.get("port", 5900)
        username = entry.get("username", "").strip()

        # Display format: "Server Name — host:port [type]"
        if name:
            display = f"{name}"
        else:
            display = host

        # Add port if not default
        if port != 5900:
            display += f":{port}"

        # Add type indicator
        if username:
            display += " [macOS]"

        return display

    def _on_item_double_clicked(self, item):
        """Connect to server when item is double-clicked."""
        entry = item.data(Qt.UserRole)
        if entry:
            self.connect_requested.emit(
                entry["host"],
                entry["port"],
                entry.get("name", "").strip()
            )

    def _show_context_menu(self, position):
        """Show context menu for server item."""
        item = self.list_widget.itemAt(position)
        if not item:
            return

        menu = QMenu(self)

        connect_action = menu.addAction("Connect")
        connect_action.triggered.connect(
            lambda: self._on_item_double_clicked(item)
        )

        disconnect_action = menu.addAction("Disconnect")
        disconnect_action.triggered.connect(self.disconnect_requested.emit)

        menu.addSeparator()

        edit_action = menu.addAction("Edit")
        edit_action.triggered.connect(
            lambda: self._edit_server(item)
        )

        delete_action = menu.addAction("Delete")
        delete_action.triggered.connect(
            lambda: self._delete_server(item)
        )

        menu.addSeparator()

        info_action = menu.addAction("Properties")
        info_action.triggered.connect(
            lambda: self._show_properties(item)
        )

        menu.exec(self.list_widget.mapToGlobal(position))

    def _on_add_clicked(self):
        """Add a new server (opens main connect dialog)."""
        self.add_requested.emit()

    def _on_edit_clicked(self):
        """Edit selected server."""
        item = self.list_widget.currentItem()
        if item:
            self._edit_server(item)

    def _on_delete_clicked(self):
        """Delete selected server."""
        item = self.list_widget.currentItem()
        if item:
            self._delete_server(item)

    def _edit_server(self, item):
        """Open the full connection settings for a saved server."""
        entry = item.data(Qt.UserRole)
        if not entry:
            return

        self.edit_requested.emit(entry["host"], entry["port"])

    def _delete_server(self, item):
        """Delete server from history."""
        entry = item.data(Qt.UserRole)
        if not entry:
            return

        host = entry["host"]
        name = entry.get("name", "").strip() or host

        reply = QMessageBox.question(
            self,
            "Delete Server",
            f"Delete '{name}'?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.history.remove(host)
            self.refresh_list()

    def _show_properties(self, item):
        """Show detailed server properties."""
        entry = item.data(Qt.UserRole)
        if not entry:
            return

        details = f"""Server Properties

Name: {entry.get('name', '(unnamed)')}
Host: {entry['host']}
Port: {entry['port']}
Username: {entry.get('username', '(none)')}
Type: {'macOS Screen Sharing' if entry.get('username') else 'Standard VNC'}
Wheel Speed: {entry.get('wheel_speed', 'default')}
Share Clipboard: {entry.get('share_clipboard', True)}
Auto-reconnect: {entry.get('auto_reconnect', False)}
"""

        QMessageBox.information(self, "Server Properties", details)

    def get_selected_server(self):
        """Get currently selected server entry."""
        item = self.list_widget.currentItem()
        if item:
            return item.data(Qt.UserRole)
        return None

    def set_connected_state(self, host):
        """Highlight connected server."""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            entry = item.data(Qt.UserRole)
            if entry and entry["host"] == host:
                # Highlight connected item
                item.setBackground(QColor("#d0e8ff"))
                self.list_widget.scrollToItem(item)
            else:
                # Reset other items
                item.setBackground(QColor("white"))

    def set_disconnected_state(self):
        """Clear connection highlighting."""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setBackground(QColor("white"))
