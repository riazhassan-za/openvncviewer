# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-08-06

First public release.

### Added

- RFB 3.8 client with macOS Screen Sharing (`RFB 003.889`) version negotiation.
- Apple/ARD authentication (security type 30): Diffie-Hellman key exchange,
  MD5-derived AES-128 key, AES-128-ECB credential block. Logs in with an
  ordinary macOS account name and password.
- Raw, CopyRect and ZRLE encodings, including all five ZRLE tile subencodings,
  plus the DesktopSize pseudo-encoding.
- Qt viewer that scales the remote desktop to the client window on every
  repaint, preserving aspect ratio, with pointer positions mapped back through
  the same scale.
- Keyboard translation to X11 keysyms, with Meta mapped to `Super_L` so macOS
  reads it as Command.
- Single-file Windows executable built with PyInstaller.
- Test suite: fake macOS server covering authentication and every encoding
  path against an independently computed reference image, plus offscreen Qt
  tests for scaling geometry, pointer mapping and painting.

### Fixed

- Input sent during the handshake corrupted the protocol stream. The connect
  dialog closes on Enter and the key *release* was delivered to the remote view
  while the network thread was still negotiating, splicing an 8-byte KeyEvent
  into the RFB handshake; macOS responded by closing the connection. Client
  input is now suppressed until the session is initialised.
- A stopped client still reported its disconnect, tearing down the session that
  had just replaced it — every reconnect attempt within a session failed.
  `stop()` now silences the callbacks before closing the socket.
- ZRLE plain-RLE tiles counted runs instead of pixels when checking for tile
  completion, reading past the end of the tile buffer.
- The remote view read its client attribute during `__init__`, before it was
  assigned, because Qt dispatches an event from `setMouseTracking`.

[Unreleased]: https://github.com/riazhassan-za/openvncviewer/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/riazhassan-za/openvncviewer/releases/tag/v0.1.0
