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
"""Encrypting a saved password with the Windows user's own key.

Uses DPAPI (`CryptProtectData`), whose key is derived from the logged-in
Windows account. Be precise about what that buys:

* The stored blob is useless on another machine, and useless to another user
  on this one. Copying `servers.json` somewhere else gains an attacker
  nothing.
* It does **not** defend against code running as you. Anything with your
  session can call `CryptUnprotectData` just as this does. Every browser
  password store has the same property. If that matters, leave "Save password"
  unticked and type it each time.

There is deliberately no fallback. On a platform without DPAPI, saving is
unavailable rather than quietly downgraded to a key sitting next to the
ciphertext, which would be obfuscation dressed up as encryption.
"""

import base64
import ctypes
import sys
from ctypes import wintypes

# Mixed into the key. Not a secret - it is in this file - so it adds no real
# strength; it only means another program on the same account has to have
# looked here first.
_ENTROPY = b"OpenVNCViewer/servers.json/v1"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _load():
    if not sys.platform.startswith("win"):
        return None
    try:
        crypt32 = ctypes.WinDLL("crypt32.dll")
        kernel32 = ctypes.WinDLL("kernel32.dll")
    except (OSError, AttributeError):
        return None
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    return crypt32, kernel32


_LIBS = _load()


def available():
    """Whether passwords can be encrypted at all on this machine."""
    return _LIBS is not None


def _to_blob(data):
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _from_blob(blob, kernel32):
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        kernel32.LocalFree(blob.pbData)


def encrypt(plaintext):
    """Plaintext to a base64 token, or None if it cannot be protected."""
    if not plaintext or _LIBS is None:
        return None
    crypt32, kernel32 = _LIBS
    source, _keep = _to_blob(plaintext.encode("utf-8"))
    entropy, _keep_entropy = _to_blob(_ENTROPY)
    result = _Blob()
    ok = crypt32.CryptProtectData(ctypes.byref(source), None,
                                  ctypes.byref(entropy), None, None, 0,
                                  ctypes.byref(result))
    if not ok:
        return None
    return base64.b64encode(_from_blob(result, kernel32)).decode("ascii")


def decrypt(token):
    """Base64 token back to plaintext, or None if it cannot be read.

    Returns None rather than raising when the blob was written by another user
    or on another machine - that is an expected outcome, not an error.
    """
    if not token or _LIBS is None:
        return None
    crypt32, kernel32 = _LIBS
    try:
        raw = base64.b64decode(token.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError):
        return None
    source, _keep = _to_blob(raw)
    entropy, _keep_entropy = _to_blob(_ENTROPY)
    result = _Blob()
    ok = crypt32.CryptUnprotectData(ctypes.byref(source), None,
                                    ctypes.byref(entropy), None, None, 0,
                                    ctypes.byref(result))
    if not ok:
        return None
    try:
        return _from_blob(result, kernel32).decode("utf-8")
    except UnicodeDecodeError:
        return None
