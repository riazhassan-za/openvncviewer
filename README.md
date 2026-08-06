# OpenVNCViewer

[![CI](https://github.com/riazhassan-za/openvncviewer/actions/workflows/ci.yml/badge.svg)](https://github.com/riazhassan-za/openvncviewer/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

A VNC viewer for **macOS Screen Sharing** that logs in with a real macOS
account name and password, and scales the remote desktop to whatever size the
client window happens to be.

Most viewers make you choose between a 1:1 window the size of the Mac's display
and a scrollable viewport. This one always fits the desktop to the window,
rescaling live as you drag the window edge.

## Why this exists

Two things are awkward with the usual Windows VNC clients against a Mac:

1. **Logging in with a macOS account.** macOS offers RFB security type 30
   ("ARD"), which takes a normal account name and password. Many clients only
   implement the legacy 8-character VNC password (type 2), which requires
   turning on a separate, weaker option on the Mac.
2. **Scaling.** A 3420x2214 Retina desktop on a 1920x1080 monitor is
   unusable without client-side scaling.

## Requirements

- 64-bit Windows 10 (1809 or later) or Windows 11
- A Mac with **System Settings → General → Sharing → Screen Sharing** enabled,
  and your account allowed access

The legacy "VNC viewers may control screen with password" option is **not**
needed and is not used.

## Install

### Prebuilt executable

Download `OpenVNCViewer.exe` from the
[latest release](https://github.com/riazhassan-za/openvncviewer/releases).
It is a single file with Python, PySide6 and cryptography bundled — nothing to
install. It is unsigned, so SmartScreen will warn on first run.

### From source

```
git clone https://github.com/riazhassan-za/openvncviewer.git
cd openvncviewer
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

## Run

```
.venv\Scripts\python.exe -m openvncviewer
.venv\Scripts\python.exe -m openvncviewer --host mac.local --user myaccount
```

Or, once installed, just `openvncviewer`.

`--host`, `--port` and `--user` prefill the connect dialog. The password is
always typed into the dialog — never passed on the command line, where it would
land in shell history and the process list.

## Scaling

The remote framebuffer is drawn scaled to the window on every repaint, with a
smooth transform and the aspect ratio preserved (letterboxed or pillarboxed as
needed). Resizing the window rescales immediately — nothing is renegotiated
with the server and the Mac's own resolution is left alone.

Mouse positions are mapped back through the same scale, so clicks land where
you point at any window size. Clicks in the letterbox area clamp to the nearest
remote pixel.

## How the macOS login works

macOS announces itself as `RFB 003.889`; the client replies `RFB 003.008` and
speaks standard RFB 3.8 from there.

For authentication it selects security type 30. The client performs a
Diffie-Hellman exchange with the server, MD5s the shared secret into an
AES-128 key, and sends the username and password as two 64-byte
null-terminated fields encrypted with AES-128-ECB.

> **Security note.** That scheme is Apple's, not ours, and it is weak by modern
> standards. More importantly, **everything after authentication is plaintext
> RFB** — screen contents and keystrokes included — and the server's identity is
> not verified at all. On any network you do not fully trust, tunnel it:
> `ssh -L 5900:localhost:5900 you@mac` and connect to `localhost`.
> See [TODO.md](TODO.md) §1 for the full list of security caveats.

## Encodings

Raw, CopyRect and ZRLE (all five tile subencodings), plus the DesktopSize
pseudo-encoding so a resolution change on the Mac reallocates the framebuffer.

Pixels are negotiated as 32bpp little-endian BGRX, which is byte-identical to
`QImage.Format_RGB32`, so the framebuffer `bytearray` is wrapped by QImage
without copying.

## Layout

| Path | Purpose |
| --- | --- |
| `src/openvncviewer/__main__.py` | Entry point and argument parsing |
| `src/openvncviewer/rfb.py` | RFB protocol, ARD auth, decoders (no Qt) |
| `src/openvncviewer/ui.py` | Qt window, scaling view, input translation |
| `tests/test_protocol.py` | Fake macOS server: auth + every encoding path |
| `tests/test_scaling.py` | Scaling geometry, pointer mapping, painting |
| `packaging/openvncviewer.spec` | PyInstaller build definition |

The protocol layer is deliberately Qt-free, so it can be tested and reused
without a GUI.

## Tests

```
.venv\Scripts\python.exe -m pip install -e .
set QT_QPA_PLATFORM=offscreen
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

`tests/test_protocol.py` stands up a fake server that mimics macOS Screen
Sharing — real Diffie-Hellman handshake, real credential decryption — and
compares the decoded framebuffer against an independently computed reference
image. No Mac required.

## Building the executable

```
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean packaging/openvncviewer.spec
```

Produces `dist\OpenVNCViewer.exe` (~49 MB). The spec excludes the PySide6
modules the viewer never touches (WebEngine, Quick, 3D, Multimedia and
friends), which is most of the download size.

The build will not run on Windows 7/8/8.1 or 32-bit Windows — that is the floor
for the embedded CPython, not a limitation of the code. Rebuilding with an
older or 32-bit interpreter should work but has not been tested.

## Known limitations

See **[TODO.md](TODO.md)**. The short version: no clipboard sync, no
Tight/Hextile encodings, no legacy VNC password support, ZRLE decoding is pure
Python and is the performance bottleneck, HiDPI is untested, and the security
caveats above.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports and patches welcome —
[TODO.md](TODO.md) is a ready-made list of things worth doing.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).

The RFB implementation here was written from scratch against the protocol
specification. No code was copied from TigerVNC or any other VNC
implementation.
