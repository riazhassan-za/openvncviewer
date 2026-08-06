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

    def remember(self, host, port, username="", name="", password=""):
        """Move a server to the front of the list, or add it.

        `password` is an already-encrypted token or empty. Passing "" replaces
        any previously saved one, so unticking "Save password" forgets it.
        """
        host = (host or "").strip()
        if not host:
            return
        self._entries = [e for e in self._entries if e["host"] != host]
        self._entries.insert(0, {"host": host, "port": int(port),
                                 "username": username or "", "name": name or "",
                                 "password": password or ""})
        del self._entries[MAX_ENTRIES:]
        self.save()

    def remove(self, host):
        """Forget one server. Returns whether there was anything to forget."""
        host = (host or "").strip()
        remaining = [e for e in self._entries if e["host"] != host]
        if len(remaining) == len(self._entries):
            return False
        self._entries = remaining
        self.save()
        return True
