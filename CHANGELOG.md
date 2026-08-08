# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **The `main` ruleset requires an approving code-owner review again**,
  reversing the change in 0.9.1. That change rested on the claim that nobody
  but the owner has write access to a personal repository, so the rule gated
  only the person who cannot approve their own pull request. The claim was
  never checked and is false: a second collaborator has write access. With two
  write-capable accounts either could otherwise merge their own work to `main`
  unreviewed, so the requirement is back on.

## [0.9.1] - 2026-08-07

### Changed

- **Documented as what it is: a VNC viewer for 64-bit Windows**, with support
  for macOS Screen Sharing and standard VNC servers. Nothing about the software
  changed — the positioning did. It began as a viewer that could in principle
  run anywhere Python does, and in practice Qt 6, PySide6's wheel coverage and
  DPAPI password storage have settled it on Windows x64. Saying so up front
  beats letting somebody discover it after a download.

  A new **Platform support** section in the README separates the two questions
  that were being conflated: what the viewer *runs on* (Windows x64 only) and
  what it *connects to* (macOS Screen Sharing and standard VNC servers on any
  OS). TODO §2 now opens with that scope, and the note about macOS and Linux
  builds is recorded as a decision rather than an open gap — with the three
  things a porter would hit first.

  Also corrected: the package metadata carried
  `Environment :: X11 Applications :: Qt`, an X11 classifier on a Windows-only
  application, and a generic Windows classifier where 10 and 11 are meant.

- **The `main` ruleset no longer requires an approving review.** On a personal
  repository that requirement gated nobody but the maintainer — no one else has
  write access, so a fork PR can never be self-merged whatever the rule says,
  while GitHub forbids approving your own pull request. Every merge therefore
  needed the admin override, which makes the protection advisory in practice.
  Pull requests, passing CI, no force-push and no deletion all still hold.
  TODO §9 records the trigger for putting the requirement back.

## [0.9.0] - 2026-08-07

### Added

- **Clipboard sharing in both directions**, on by default, with a *Share
  clipboard with this server* tick in the connect dialog's Options group. Copy
  on either machine, paste on the other.

  Two caveats stated plainly rather than buried: anything copied locally is
  **sent to the server over plaintext RFB**, so untick it when that matters;
  and the base protocol carries **Latin-1 only**, so emoji and CJK become `?`.
  Windows CRLF is converted to the bare LF the protocol requires, and back.

  **This does not work against macOS**, which does not carry the clipboard over
  RFB in either direction — see below.

- **Alt is sent as Command by default**, so Alt+C and Alt+V copy and paste on
  a remote Mac. Alt sits where Command does on a Mac keyboard, and it is the
  only reachable choice — Command was previously on the Windows key, and
  Windows keeps most Win+key combinations for itself. A tick in the connect
  dialog reverts it for non-Apple servers.

### Fixed

- **Alt opened the local menu bar instead of reaching the remote.** Qt offers
  each keystroke to shortcuts before the focused widget, so Alt activated the
  menu bar and Alt+F was eaten as a mnemonic. The view now claims
  `ShortcutOverride` for every key except F11, which stays reserved so full
  screen does not become a one-way door.

- **`ServerCutText` read an unbounded length off the wire.** The length is a
  server-chosen `u32`, so a hostile server could declare 4GB and the client
  would attempt to read it — the same class of problem as the ZRLE bomb fixed
  in 0.6.0, sitting in the path this feature turns on. Oversized text is now
  dropped, and an absurd length ends the session before anything is read.

- **A NUL terminator left on the Windows clipboard by some applications was
  forwarded to the server.** Observed on a live session. It is not part of the
  text and is now stripped in both directions.

### Known limitation

- **macOS Screen Sharing does not carry the clipboard over RFB**, in either
  direction, so the new option has no effect when connected to a Mac. A traced
  session shows `ClientCutText` leaving correctly and being ignored, and no
  `ServerCutText` ever arriving, while the same build round-trips both
  directions against a standard VNC server. Apple's client uses a private
  channel. Documented in the README, the TODO and the option's tooltip so it is
  not mistaken for a bug in this viewer.

## [0.8.2] - 2026-08-07

### Changed

- Release assets are now named per architecture (`OpenVNCViewer-x64.exe`) and
  accompanied by a CI-generated `SHA256SUMS.txt`, so a download can be verified
  without trusting the release notes. The build verifies the PE header of what
  it produced rather than trusting the runner label, and publishing moved to
  its own job so a tag releases only once the build has succeeded.

- Documented, after checking PyPI rather than assuming, why three platforms are
  not offered. Previous notes claimed 32-bit and Windows 7/8 "only need a
  rebuild with an older/32-bit interpreter", which was wrong and would have
  sent someone down a dead end.

  - **32-bit** — PySide6 has never published a `win32` wheel, in any release.
  - **Windows 7/8** — Qt 6 requires Windows 10, so even x64 cannot run there.
  - **ARM64** — PySide6 does publish `win_arm64`, but `cryptography` shipped a
    Windows ARM64 wheel only in 46.0.0-46.0.3 and dropped it. Attempted on a
    `windows-11-arm` runner: the build fails installing `cryptography`, which
    falls back to compiling from source and wants Rust and OpenSSL. Shipping it
    would mean pinning a crypto library four major versions behind in an
    application that handles account passwords.

## [0.8.1] - 2026-08-07

### Fixed

- **The remote desktop was drawn blurry at Windows display scaling above
  100%.** The cached pixmap was allocated in logical pixels, so at 150% it held
  64% of the pixels the screen could show and at 200% just 48% — Qt stretched
  the rest. It is now allocated in device pixels with `devicePixelRatio` set,
  restoring 97% coverage at every scale, which is what it had at 100% all
  along.

  Clicks were never affected: `target_rect()` and the mouse position are both
  in logical pixels, so the scale factor cancels. Earlier notes in this project
  suspected the pointer mapping; that was wrong, and measuring it said so.

  The cost is a pixmap with `ratio**2` more pixels, so a full rescale at 200%
  does around four times the work. Damage-limited repainting keeps the common
  case cheap.

## [0.8.0] - 2026-08-07

### Added

- **Recently connected servers are remembered between runs.** The Host box is
  now a dropdown, most recent first, and a new **Server name** field gives each
  one a label of your choosing. Selecting a server fills in its name, port and
  username together — restoring the name alone would leave the previous
  server's username in the form, which breaks a Mac-then-standard-VNC switch.
  Typing a host never connected to clears the name.

  The name also appears in the window title and status bar once connected, so
  several open viewers can be told apart.

  A server is remembered only after it **actually connects**, so typos and
  unreachable hosts never accumulate. **Remove Server** forgets the one
  currently shown along with any saved password, then selects the next, so
  removing three takes three clicks.

  The dialog's accept button now reads **Connect** rather than OK, and the
  three buttons are ordered Connect, Cancel, Remove Server. They are laid out
  by hand rather than with `QDialogButtonBox`, which reorders by role per
  platform and would not honour a fixed order.

  Stored as `servers.json` in the user config directory (`%LOCALAPPDATA%` on
  Windows, where Qt's `AppConfigLocation` points — not the roaming `%APPDATA%`).
  Saves are atomic, so a crash mid-write leaves the previous list intact, and a
  corrupt or hand-edited file is discarded field by field rather than crashing
  the dialog.

- **Optional saved passwords, off by default.** Ticking *Save password for this
  server* encrypts the password with Windows DPAPI — keyed to your Windows
  login — before it is written. Selecting that server later fills the password
  in and re-ticks the box; unticking and reconnecting forgets it.

  What that protects: the stored blob is useless on another machine or to
  another user on this one. What it does not: code running as you can call
  `CryptUnprotectData` exactly as the viewer does, the same property every
  browser password store has.

  The plaintext never reaches `servers.json`, and the module that writes that
  file cannot decrypt — it stores an opaque token. Where DPAPI is unavailable
  the option is **disabled** rather than falling back to a key kept beside the
  ciphertext, which would be obfuscation rather than encryption.

### Changed

- The viewport is cleared on disconnect, along with the window title. A stale
  last frame under a title naming the server implies a session that has ended.

### Fixed

- DPAPI could block indefinitely instead of failing. `CryptProtectData` was
  called with `dwFlags=0`, which permits it to raise a UI prompt; in a session
  with no interactive desktop that waits forever. It now passes
  `CRYPTPROTECT_UI_FORBIDDEN`, so it fails rather than hangs — the only sane
  outcome when there is nobody to answer a dialog. Found because it hung CI on
  all five Python versions.

  `secretstore.available()` now probes with a real encrypt/decrypt round trip
  rather than reporting that the library loaded, since DPAPI can be present and
  still refuse to work; claiming availability there would offer a "Save
  password" box that silently saved nothing.

## [0.7.0] - 2026-08-06

### Added

- A donation notice with a Bitcoin address under **Help → About** and in the
  README. The address is rendered as a `bitcoin:` link and stays selectable so
  it can be copied. Its bech32 checksum was verified before shipping, since a
  mistyped address would send donations somewhere unrecoverable.

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

[Unreleased]: https://github.com/riazhassan-za/openvncviewer/compare/v0.9.1...HEAD
[0.9.1]: https://github.com/riazhassan-za/openvncviewer/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/riazhassan-za/openvncviewer/compare/v0.8.2...v0.9.0
[0.8.2]: https://github.com/riazhassan-za/openvncviewer/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/riazhassan-za/openvncviewer/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/riazhassan-za/openvncviewer/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/riazhassan-za/openvncviewer/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/riazhassan-za/openvncviewer/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/riazhassan-za/openvncviewer/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/riazhassan-za/openvncviewer/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/riazhassan-za/openvncviewer/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/riazhassan-za/openvncviewer/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/riazhassan-za/openvncviewer/releases/tag/v0.1.0
