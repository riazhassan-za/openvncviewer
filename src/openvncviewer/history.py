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
"""The list of recently connected servers, persisted between runs.

A password is only ever stored when the user ticks "Save password", and only
as an opaque token that someone else encrypted. This module never sees a
plaintext password and cannot decrypt one - see `secretstore`. That split is
deliberate: it keeps the store dumb and makes "no plaintext on disk" something
a test can pin rather than something to take on trust.

Standard library only, and the path is injected rather than discovered, so this
is testable without Qt or a real home directory.
"""

import json
import os
import tempfile
from pathlib import Path

MAX_ENTRIES = 20
FORMAT_VERSION = 1

# Per-server session settings, with the value used when a file predates them or
# a host has never been connected to. `wheel_speed` of None means "decide from
# the connection type" - a Retina Mac wants amplification, a standard VNC server
# wants the raw notch count - which is a UI judgement, not this module's.
SESSION_DEFAULTS = {
    "wheel_speed": None,
    "share_clipboard": True,
    "alt_is_command": True,
    "auto_reconnect": True,
}
# Kept in step with ui.WHEEL_SPEED_MIN/MAX. Duplicated rather than imported
# because this module is deliberately Qt-free and stdlib-only; the test suite
# pins the two together.
WHEEL_SPEED_RANGE = (1, 100)


def _clean_session(entry):
    """Session settings from a file anyone can edit, or their defaults.

    A file written before these existed simply has none of them, which is the
    same case as a value of the wrong type: fall back rather than refuse the
    entry, because losing a saved server over a bad boolean would be worse
    than ignoring the bad boolean.
    """
    speed = entry.get("wheel_speed")
    low, high = WHEEL_SPEED_RANGE
    return {
        "wheel_speed": (speed if isinstance(speed, int)
                        and low <= speed <= high else None),
        "share_clipboard": entry.get("share_clipboard") if isinstance(
            entry.get("share_clipboard"), bool) else True,
        "alt_is_command": entry.get("alt_is_command") if isinstance(
            entry.get("alt_is_command"), bool) else True,
        "auto_reconnect": entry.get("auto_reconnect") if isinstance(
            entry.get("auto_reconnect"), bool) else True,
    }


class ServerHistory:
    """Most-recently-connected first, capped, and safe to load from junk."""

    def __init__(self, path):
        self.path = Path(path)
        self._entries = []
        self.load()

    # ------------------------------------------------------------------ disk

    def load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._entries = []
            return
        except (OSError, ValueError, UnicodeDecodeError):
            # Unreadable or corrupt. Losing the list is a nuisance; refusing to
            # start over it would be worse.
            self._entries = []
            return
        self._entries = self._clean(raw)

    @staticmethod
    def _clean(raw):
        """Keep only what this version understands, from a file anyone can edit."""
        servers = raw.get("servers") if isinstance(raw, dict) else None
        if not isinstance(servers, list):
            return []
        cleaned = []
        for entry in servers:
            if not isinstance(entry, dict):
                continue
            host = entry.get("host")
            if not isinstance(host, str) or not host.strip():
                continue
            port = entry.get("port")
            cleaned.append({
                "host": host.strip(),
                "port": port if isinstance(port, int) and 1 <= port <= 65535 else 5900,
                "username": entry.get("username") if isinstance(
                    entry.get("username"), str) else "",
                "name": entry.get("name") if isinstance(
                    entry.get("name"), str) else "",
                # An encrypted token, meaningless to this module.
                "password": entry.get("password") if isinstance(
                    entry.get("password"), str) else "",
                **_clean_session(entry),
            })
        return cleaned[:MAX_ENTRIES]

    def save(self):
        payload = json.dumps({"version": FORMAT_VERSION, "servers": self._entries},
                             indent=2)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename: a crash midway leaves the old list intact
            # rather than a half-written file that will not parse.
            handle, temporary = tempfile.mkstemp(dir=str(self.path.parent),
                                                 suffix=".tmp")
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    stream.write(payload)
                os.replace(temporary, self.path)
            except (OSError, ValueError):
                Path(temporary).unlink(missing_ok=True)
                raise
        except (OSError, ValueError):
            # A read-only or unwritable config directory must not stop anyone
            # connecting; the list simply will not survive this run. ValueError
            # matters too: an unusable path raises that rather than OSError.
            pass

    # ----------------------------------------------------------------- query

    def entries(self):
        return [dict(entry) for entry in self._entries]

    def hosts(self):
        return [entry["host"] for entry in self._entries]

    def find(self, host):
        host = (host or "").strip()
        for entry in self._entries:
            if entry["host"] == host:
                return dict(entry)
        return None

    # ---------------------------------------------------------------- update

    def remember(self, host, port, username="", name="", password="",
                 **session):
        """Move a server to the front of the list, or add it.

        `password` is an already-encrypted token or empty. Passing "" replaces
        any previously saved one, so unticking "Save password" forgets it.

        `session` carries the per-server settings in SESSION_DEFAULTS - wheel
        speed, clipboard sharing, Alt-as-Command, auto-reconnect. Anything not
        passed keeps its default, so an older caller still works.
        """
        host = (host or "").strip()
        if not host:
            return
        unknown = set(session) - set(SESSION_DEFAULTS)
        if unknown:
            raise TypeError(f"unknown session settings: {sorted(unknown)}")
        self._entries = [e for e in self._entries if e["host"] != host]
        self._entries.insert(0, {"host": host, "port": int(port),
                                 "username": username or "", "name": name or "",
                                 "password": password or "",
                                 **SESSION_DEFAULTS, **session})
        del self._entries[MAX_ENTRIES:]
        self.save()

    def forget_password(self, host):
        """Drop a saved password, leaving the entry and its order alone.

        Separate from `remember` because revoking a credential is not a
        history update: it must not wait for a connection to succeed, and must
        not promote the server up the list on the way. Returns whether there
        was a token to forget.
        """
        host = (host or "").strip()
        for entry in self._entries:
            if entry["host"] == host:
                if not entry["password"]:
                    return False
                entry["password"] = ""
                self.save()
                return True
        return False

    def remove(self, host):
        """Forget one server. Returns whether there was anything to forget."""
        host = (host or "").strip()
        remaining = [e for e in self._entries if e["host"] != host]
        if len(remaining) == len(self._entries):
            return False
        self._entries = remaining
        self.save()
        return True
