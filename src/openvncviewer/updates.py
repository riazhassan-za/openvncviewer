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
"""Read-only release checks. No credentials, downloads or installation."""

from dataclasses import dataclass
import json
import re
from urllib.parse import quote

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import __version__

API_URL = "https://api.github.com/repos/riazhassan-za/openvncviewer/releases/latest"
RELEASES_URL = "https://github.com/riazhassan-za/openvncviewer/releases"
CHECK_TIMEOUT_MS = 10_000
MAX_RESPONSE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    notes: str


def _version(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Unrecognized release version.")
    match = re.fullmatch(r"v?([0-9]+)\.([0-9]+)\.([0-9]+)", value)
    if match is None:
        raise ValueError("Unrecognized release version.")
    return tuple(int(part) for part in match.groups())


def newer_release(data, current):
    """Only published stable versions, linked to this project's release page."""
    if not isinstance(data, dict) or not isinstance(data.get("draft"), bool) \
            or not isinstance(data.get("prerelease"), bool):
        raise ValueError("GitHub returned invalid release information.")
    if data["draft"] or data["prerelease"]:
        return None
    tag = data.get("tag_name")
    if _version(tag) <= _version(current):
        return None
    notes = data.get("body")
    # Release notes are untrusted text, never HTML or an API-supplied URL.
    notes = notes[:8000] if isinstance(notes, str) else ""
    return Release(tag.removeprefix("v"),
                   RELEASES_URL + "/tag/" + quote(tag, safe=""), notes)


class UpdateChecker(QObject):
    finished = Signal(object, str)  # Release or None, error (empty on success)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._reply = None
        self._data = bytearray()
        self._failure = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timed_out)

    def check(self):
        if self._reply is not None:
            return False
        self._data.clear()
        self._failure = ""
        request = QNetworkRequest(QUrl(API_URL))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", f"OpenVNCViewer/{__version__}".encode("ascii"))
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                             QNetworkRequest.ManualRedirectPolicy)
        self._reply = self._manager.get(request)
        self._reply.setReadBufferSize(MAX_RESPONSE_BYTES + 1)
        self._reply.readyRead.connect(self._read_available)
        self._reply.finished.connect(self._finished)
        # A total deadline also bounds a peer that keeps trickling bytes.
        self._timer.start(CHECK_TIMEOUT_MS)
        return True

    def cancel(self):
        self._timer.stop()
        reply, self._reply = self._reply, None
        if reply is not None:
            reply.readyRead.disconnect(self._read_available)
            reply.finished.disconnect(self._finished)
            reply.abort()
            reply.deleteLater()
        self._data.clear()

    def _timed_out(self):
        if self._reply is not None:
            self._failure = "The update check timed out. Please try again later."
            self._reply.abort()

    def _read_available(self):
        if self._reply is None or self._failure:
            return
        chunk = bytes(self._reply.readAll())
        if len(self._data) + len(chunk) > MAX_RESPONSE_BYTES:
            self._failure = "GitHub's release response was too large."
            self._reply.abort()
            return
        self._data.extend(chunk)

    def _finished(self):
        reply = self._reply
        if reply is None:
            return
        self._read_available()
        self._reply = None
        self._timer.stop()
        release, error = None, self._failure
        try:
            status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            if error:
                pass
            elif status == 404:
                error = "No published release is available on GitHub."
            elif status in (403, 429):
                error = "GitHub refused the check or its rate limit was reached. Try again later."
            elif reply.error() != QNetworkReply.NoError or status != 200:
                error = "Could not check GitHub for updates. Please try again later."
            else:
                release = newer_release(json.loads(self._data), __version__)
        except (ValueError, UnicodeError, RecursionError):
            error = "GitHub returned invalid release information."
        finally:
            self._data.clear()
            reply.deleteLater()
            # A check is one request, so keep no connection open for reuse.
            # Qt would close it ~30s later itself, printing "QIODevice::read
            # (QSslSocket): device not open" to stderr.
            self._manager.clearConnectionCache()
        self.finished.emit(release, error)
