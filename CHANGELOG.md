# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-08-06

### Added

- **Support for standard VNC servers.** Legacy VNC password authentication
  (security type 2) is implemented, so non-Apple servers connect. Leave
  **Username** blank in the connect dialog and the viewer uses the VNC password;
  fill it in and it still prefers a macOS account over the legacy scheme where
  both are on offer. Servers requiring no authentication also work.

  The client now picks the strongest scheme it can satisfy from what the server
  offers and which credentials were supplied, rather than assuming a Mac.

  Two caveats, both inherent to the scheme rather than to this implementation:
  the password is capped at 8 characters, and single DES with a 56-bit key is
  recoverable offline by anyone who sees the challenge and response.

- RFB 3.3 servers work and are tested. A 3.3 server dictates the security type
  instead of offering a list, and differs on when `SecurityResult` is sent.
  This path previously existed but had never been exercised.

### Changed

- The connect dialog is retitled "Connect to a VNC server", with **Username**
  relabelled from "macOS user" and tooltips explaining that blank means a
  standard VNC server and that VNC passwords are truncated to 8 characters.

### Security

- The decoder is hardened against a hostile or broken server. Three problems,
  each measured before and after:

  - **A framebuffer rectangle outside the framebuffer resized it.** Slice
    assignment past the end of a `bytearray` extends it rather than failing,
    and QImage holds a raw pointer into that buffer — so a malformed rectangle
    reallocated the framebuffer underneath the view. `_blit` now bounds-checks
    once per call, covering CopyRect sources too.
  - **A ZRLE run longer than its tile allocated without limit.** 39 KB of input
    produced 40 MB of pixels, a 1021x amplification. Runs are clamped to the
    pixels remaining in the tile.
  - **ZRLE decompression was unbounded.** A 194 KB compression bomb expanded to
    200 MB. `decompress` is now given a ceiling derived from the rectangle
    size, and a stream that exceeds it is refused.

  Malformed tiles now raise a protocol error rather than escaping as
  `IndexError` or `ValueError`. Palette-RLE decoding costs 19.7 Mpx/s against
  21.7 before, all of it the run clamp and the palette bounds check.

## [0.5.0] - 2026-08-06

### Security

- The Diffie-Hellman group offered for ARD authentication is now validated
  before the password is encrypted under a key derived from it. The client
  refuses a prime below 1024 bits, a composite modulus, a generator outside
  `2 <= g < p`, a peer public key of 0, 1 or `p-1`, and a shared secret that
  collapses to one of those — hanging up without sending the credential block.

  This protects the exchange from a passive eavesdropper and catches parameters
  tampered with in transit or a broken server. It does *not* protect against a
  hostile server, which holds the other private key and can decrypt the
  credentials whatever group it chose; that needs server identity verification,
  which is still open.

  Groups whose primality has already been verified are matched by SHA-256 and
  skip the Miller-Rabin test, because proving macOS's 4096-bit modulus prime
  costs about 2.5 seconds and would otherwise be paid on every connect.
  Authentication against a real Mac remains at 0.68s.

## [0.4.0] - 2026-08-06

### Added

- Full-screen mode, from **View → Full screen** or **F11**, which hides the
  menu and status bars and gives the whole screen to the remote desktop.
  Nothing is renegotiated with the server; the scaler simply has more room.
  Leaving full screen restores a maximized window as maximized rather than
  dropping it to a normal one, and any keys held while toggling are released so
  a modifier cannot stick on the Mac. F11 is reserved by the viewer and is not
  forwarded to the remote.

## [0.3.0] - 2026-08-06

### Changed

- Only the damaged region of the screen is repainted. Each framebuffer update
  now reports the bounding box of every rectangle it contained, and the view
  rescales just that part into a cached `QPixmap` instead of smooth-scaling the
  whole desktop on every frame. At 3420x2214 a 64x64 change costs 0.02 ms
  against 1.86 ms for the previous full rescale.
- Packed-palette ZRLE tiles memoise the expansion of each packed byte rather
  than looping per pixel, and decoded tiles are written straight into the
  framebuffer instead of being staged in a scratch buffer and copied twice.
  Packed-palette decoding went from 7.8 to 27.0 Mpx/s; every other tile
  subencoding improved between 15% and 25%.
- Pointer motion is coalesced to roughly one message per 16 ms. The first move
  of a burst is still sent immediately, and button presses bypass the queue so
  they cannot be reordered behind a pending move.

### Added

- `benchmarks/decode.py` and `benchmarks/paint.py`, so the numbers above can be
  reproduced rather than taken on trust.

### Fixed

- Packed-palette ZRLE tiles could raise `IndexError` on a stream whose row
  padding bits were not zero. The palette is now padded to cover every index
  the bit mask can produce. The previous per-pixel loop stopped at the tile
  width and never read those bits, so this was latent rather than observed.

## [0.2.0] - 2026-08-06

### Added

- Mouse wheel speed control in the connect dialog, under a new **Options**
  group. RFB carries no scroll magnitude — a wheel notch is just a button
  press — so scrolling over a remote session feels sluggish. The slider
  multiplies the clicks sent per notch, from 1 (the wheel passed through
  untouched) up to 100, defaulting to 50. A ceiling of 500 clicks per wheel
  event keeps a fast flick from flooding the server, and is deliberately held
  above the slider maximum so it cannot quietly cap the top of the range.

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

[Unreleased]: https://github.com/riazhassan-za/openvncviewer/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/riazhassan-za/openvncviewer/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/riazhassan-za/openvncviewer/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/riazhassan-za/openvncviewer/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/riazhassan-za/openvncviewer/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/riazhassan-za/openvncviewer/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/riazhassan-za/openvncviewer/releases/tag/v0.1.0
