# TODO and Known Limitations

This file is the honest inventory of what OpenVNCViewer does **not** do yet,
and where the sharp edges are. Read the Security section before using this on a
network you do not control.

Status legend: `[ ]` open · `[~]` partially done · `[x]` done

---

## 1. Security

These are the items that matter most for a publicly consumed tool. None of them
are hypothetical — they follow directly from how the protocol is implemented
today.

- [ ] **Diffie-Hellman parameters from the server are trusted blindly.**
  In `rfb.py::_auth_ard` the generator, prime and peer public key are read
  straight off the wire and used. A hostile or spoofed server can supply a weak
  or degenerate prime (or a peer key of 0/1) and recover the password from the
  exchange. Add sanity checks: reject small primes, reject `peer_key <= 1` and
  `peer_key >= prime - 1`, and require a minimum key length (Apple uses 128
  bytes / 1024 bits).
- [ ] **No server identity verification of any kind.** There is no host-key
  pinning, certificate check, or trust-on-first-use record. Anything that can
  intercept TCP/5900 can impersonate the Mac and harvest the macOS account
  password. Consider storing and checking a fingerprint of the server's DH
  public key or desktop name on first connect.
- [ ] **All traffic after authentication is plaintext RFB.** Screen contents,
  keystrokes (including anything typed into a password field on the Mac) and
  mouse movement are unencrypted. Until this is addressed, tunnel over SSH
  (`ssh -L 5900:localhost:5900 user@mac`) or a VPN on untrusted networks.
  Document this prominently rather than quietly relying on users knowing.
- [ ] **No support for the encrypted security types macOS also offers.** The
  test Mac advertised types `[30, 33, 31, 32, 2, 35]`; only 30 (ARD) is
  implemented. Types 31/32/33/35 are Apple's RSA-AES variants and would provide
  transport confidentiality. Implementing one of these is the proper fix for
  the plaintext problem above.
- [ ] **The ARD scheme itself is weak, by Apple's design, not ours.** It uses
  MD5 to derive an AES-128 key and AES-**ECB** to encrypt the credential block.
  We cannot change this without the server's cooperation; it is recorded here
  so nobody mistakes it for our choice.
- [ ] **The password lives in memory as a Python `str`.** Strings are immutable
  and cannot be zeroed after use; it may persist until garbage collection and
  can land in a crash dump. Consider holding it in a `bytearray` and wiping it
  after the credential block is built.
- [ ] No integration with Windows Credential Manager or any keychain; there is
  no "save password" feature (deliberate for now, but users will ask).

## 2. Platform and packaging

- [ ] **The prebuilt `OpenVNCViewer.exe` runs only on 64-bit Windows 10 (1809+)
  and Windows 11.** That is the floor for the embedded CPython. It will not run
  on Windows 7/8/8.1 or 32-bit Windows. Nothing in the source depends on a
  recent Python, so those targets only need a rebuild with an older/32-bit
  interpreter — but nobody has done or tested that.
- [ ] **The executable is unsigned.** SmartScreen warns on first run on any
  machine that has not seen it before, and some corporate policies will block
  it outright. Needs an Authenticode certificate to fix properly.
- [ ] **One-file builds unpack to `%TEMP%` on every launch**, costing a few
  seconds of startup. A one-folder build removes the delay at the cost of
  shipping a directory. Consider offering both on releases.
- [ ] No installer, no Start Menu entry, no file association, no auto-update.
- [ ] No macOS or Linux build of the *viewer* itself. The code is portable
  (PySide6 + stdlib sockets) but only Windows has been exercised. The Qt key
  mapping in particular assumes a PC keyboard.
- [ ] No application icon; the exe and window use Qt defaults.

## 3. Protocol coverage

- [ ] **Encodings are limited to Raw, CopyRect and ZRLE**, plus the DesktopSize
  pseudo-encoding. Tight, Hextile, RRE, CoRRE and zlib are not implemented.
  Tight in particular would cut bandwidth substantially on photographic content.
- [ ] **Legacy VNC password authentication (security type 2) is unsupported.**
  If a server offers only type 2, the viewer reports a clear error naming the
  macOS setting to change, but cannot connect. Non-Apple servers (RealVNC,
  TightVNC, x11vnc, TigerVNC server) have not been tested at all and most will
  not offer type 30.
- [ ] **No clipboard synchronisation** in either direction. `ServerCutText` is
  read off the wire and discarded.
- [ ] **No cursor pseudo-encodings.** The server renders the pointer into the
  framebuffer, which is what we want for a scaled view, but it means the local
  cursor and the remote cursor can visibly disagree during fast movement.
- [ ] No `ExtendedDesktopSize`, no multi-monitor enumeration or per-screen
  selection. A multi-display Mac appears as one wide framebuffer.
- [ ] No client-initiated remote resize. Scaling is purely client-side by
  design — but some users will expect the Mac's resolution to follow the window
  and should be told it does not.
- [ ] No reconnect or retry on a dropped link; the session simply ends.
- [ ] RFB 3.3 servers take an untested code path in `_authenticate`.

## 4. Performance

- [ ] **ZRLE tile decoding is pure Python and is the bottleneck.** Packed
  palette and RLE tiles run a per-pixel Python loop. On the 3420x2214 display
  used for testing this is noticeable; on a busy screen it will cap the frame
  rate well below what the network can carry. Options, cheapest first:
  decode into a preallocated `memoryview` instead of building and joining
  lists; move the inner loops to `numpy`; or add a small C/Cython extension.
- [ ] **The framebuffer is written by the network thread while the UI thread
  paints from it, with no lock.** This is deliberate (a lock or a copy of a
  30 MB buffer costs more than it saves) and the worst case is brief tearing
  during a repaint, not a crash — the `bytearray` is replaced, never resized in
  place, so QImage never sees freed memory. Revisit if tearing is reported.
- [ ] Every `FramebufferUpdate` triggers a full-widget repaint and a full
  rescale of the entire image. Damage rectangles are known but not used to
  limit the repaint region.
- [ ] The smooth-scaled blit happens on the CPU on every paint. Caching a
  pre-scaled `QPixmap` and only regenerating it when the window size or the
  framebuffer changes would help a lot when the remote screen is static.
- [ ] Pointer motion is sent on every mouse-move event with no coalescing.

## 5. Input handling

- [ ] **The keysym table is partial.** `ui.py::KEYSYMS` covers ASCII, the
  common navigation and modifier keys, and F1-F12. International layouts, dead
  keys, IME composition and the numeric keypad have not been tested.
- [ ] The Meta/Windows key is mapped to `Super_L` so macOS reads it as Command,
  and Alt maps to `Alt_L` for Option. This is a reasonable default but is not
  configurable, and no key-remapping UI exists.
- [ ] There is no way to send Ctrl-Alt-Del, Command-Tab, or other combinations
  that the local window manager swallows before Qt sees them.
- [ ] No keyboard grab / full-screen mode, so some shortcuts always go to
  Windows rather than the Mac.
- [ ] Horizontal scroll is mapped to buttons 6/7 but has not been verified
  against a real trackpad.

## 6. User interface

- [ ] **Aspect ratio is always preserved** (letterboxed or pillarboxed). There
  is no stretch-to-fill option and no 1:1 / no-scaling mode with scrollbars,
  both of which some users prefer.
- [ ] **HiDPI behaviour is untested.** With Windows display scaling at anything
  other than 100%, the mapping in `RemoteView._remote_point` uses logical
  pixels; whether clicks land correctly at 150%/200% has not been checked.
  This matters more than usual given scaling is the headline feature.
- [ ] No full-screen mode.
- [ ] Connection details are not remembered between runs — no recent-hosts
  list, no bookmarks, no config file.
- [ ] Errors surface as a modal `QMessageBox` with the raw exception text.
  Messages like "connection closed by server" are accurate but do not tell a
  non-expert what to do next.
- [ ] No logging of any kind, which made the handshake race in v0.1.0 harder to
  diagnose than it needed to be. A `--verbose` flag writing protocol stages to
  stderr would pay for itself.
- [ ] No translations; all strings are hard-coded English.

## 7. Testing and CI

- [x] Fake-macOS-server integration test covering ARD auth and every encoding
  path, checked against an independently computed reference image.
- [x] Regression test for the mid-handshake input race (verified to fail
  without the fix).
- [x] Regression test that a stopped client cannot report back and tear down
  the session that replaced it.
- [x] Offscreen Qt tests for scale geometry, pointer mapping and painting.
- [ ] **Only Python 3.14 on Windows has actually been run locally.** The CI
  matrix claims 3.10-3.14; those need to go green before the claim is trusted.
- [ ] No test against a real macOS host in CI (would need a self-hosted or
  macOS runner plus a Mac with Screen Sharing enabled).
- [ ] No coverage measurement, no linter (`ruff`/`flake8`) and no formatter in
  CI.
- [ ] The ZRLE decoder has no fuzzing. A malformed or hostile stream can raise
  `IndexError` out of `_read_tile`, which currently just ends the session —
  acceptable, but it should be a clean protocol error, and the decoder should
  be checked against out-of-bounds tile writes.
- [ ] No performance benchmark, so the optimisations in section 4 cannot be
  measured objectively.

## 8. Documentation

- [x] README covering setup, running, scaling behaviour, the ARD login, and
  building the executable.
- [ ] No screenshots or animated demo in the README.
- [ ] No troubleshooting guide (what "connection closed by server" means, what
  to enable in macOS Sharing settings, firewall notes).
- [ ] No architecture document; the protocol/UI split is only explained in
  docstrings and the README table.
