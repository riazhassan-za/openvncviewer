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

- [x] **Diffie-Hellman parameters are validated** before the password is
  encrypted under a key derived from them. `rfb.py::_validate_dh_group` rejects
  a prime under 1024 bits, a composite modulus, a generator outside
  `2 <= g < p`, and a peer public key of 0, 1 or `p-1`; `_auth_ard` also
  rejects a shared secret that collapses to one of those, and bounds the
  advertised key length. On any failure it hangs up without sending the
  credential block.

  Two things learned doing it, both recorded so nobody re-derives them:

  - **macOS offers a 4096-bit group with generator 5**, not the 1024-bit group
    the original note assumed. An upper bound set from that assumption would
    have refused every real connection.
  - **Proving a 4096-bit modulus prime costs ~2.5s**, which would have been
    paid on every connect against an authentication step that otherwise takes
    0.68s. Groups whose primality has already been verified are matched by
    SHA-256 and skip the test. That is a fast path, not an allowlist —
    an unrecognised prime is still checked in full.

  The original note claimed this stopped a hostile server recovering the
  password. That was wrong: a hostile server holds the other private key and
  can decrypt the credentials regardless. What validation actually protects
  against is a **passive eavesdropper** on an exchange whose parameters were
  weak, tampered with, or broken. Only server identity verification, below,
  addresses a hostile server.
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

Numbers below come from `benchmarks/decode.py` and `benchmarks/paint.py` at
3420x2214, the resolution the project is tested against. Re-run them before
claiming any change here is an improvement.

- [~] **ZRLE tile decoding is pure Python.** The cheapest option in the
  original list is done: packed-palette tiles memoise the expansion of each
  packed byte instead of looping per pixel, and decoded tiles are written
  straight into the framebuffer rather than staged in a scratch buffer and
  copied twice.

  | Tile subencoding | Before | After |
  | --- | --- | --- |
  | packed palette | 7.8 Mpx/s | 27.0 Mpx/s |
  | raw | 75.3 Mpx/s | 90.4 Mpx/s |
  | solid | 173.5 Mpx/s | 218.3 Mpx/s |
  | plain RLE | 104.6 Mpx/s | 124.1 Mpx/s |
  | palette RLE | 21.5 Mpx/s | 21.7 Mpx/s |

  Still open: **palette RLE is now the slowest path** at 21.7 Mpx/s, and did
  not benefit — its cost is the per-run Python loop, not per-pixel work, so
  memoisation has nothing to save. Moving the inner loops to `numpy` or a small
  C/Cython extension remains the way to go further, at the cost of a build
  dependency.

- [ ] **The framebuffer is written by the network thread while the UI thread
  paints from it, with no lock.** Reviewed again while doing the work above and
  deliberately left alone: a lock or a copy of a 30 MB buffer costs more than
  it saves, and the worst case is brief tearing during a repaint, not a crash —
  the `bytearray` is replaced, never resized in place, so QImage never sees
  freed memory. Revisit if tearing is actually reported.

- [x] **Damage rectangles now limit the repaint.** `_handle_framebuffer_update`
  reports the bounding box of every rectangle in an update, and the view
  repaints only the corresponding widget rectangle.

- [x] **The scaled image is cached in a `QPixmap`** and only the damaged region
  is rescaled into it, so painting is a blit rather than a resample. Measured
  against the old full-rescale-every-frame path:

  | Damaged area | Cost |
  | --- | --- |
  | full rescale (old behaviour) | 1.86 ms |
  | 1710x1107 (a quarter of the screen) | 0.35 ms |
  | 400x300 (a window) | 0.06 ms |
  | 64x64 (a tile) | 0.02 ms |

- [x] **Pointer motion is coalesced** to one message per ~16 ms. The first move
  in a burst is sent immediately so tracking stays responsive, and button
  presses bypass the queue so they can never be reordered behind a pending
  move.

## 5. Input handling

- [ ] **The keysym table is partial.** `ui.py::KEYSYMS` covers ASCII, the
  common navigation and modifier keys, and F1-F12. International layouts, dead
  keys, IME composition and the numeric keypad have not been tested.
- [ ] The Meta/Windows key is mapped to `Super_L` so macOS reads it as Command,
  and Alt maps to `Alt_L` for Option. This is a reasonable default but is not
  configurable, and no key-remapping UI exists.
- [ ] There is no way to send Ctrl-Alt-Del, Command-Tab, or other combinations
  that the local window manager swallows before Qt sees them.
- [ ] **No keyboard grab.** Full-screen mode exists (section 6), but Windows
  still intercepts Alt+Tab, the Windows key and Ctrl+Alt+Del before Qt sees
  them, so `Cmd+Tab` on the Mac stays unreachable. Fixing it needs a low-level
  `WH_KEYBOARD_LL` hook: Windows-specific, easy to strand the user with no way
  out of full screen, and liable to trip antivirus heuristics on an unsigned
  binary. Deliberately deferred rather than forgotten.
- [ ] F11 is reserved for the full-screen toggle and is the one key never
  forwarded to the remote. Making the shortcut configurable would remove even
  that exception.
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
- [x] Full-screen mode via **View → Full screen** or **F11**, hiding the menu
  and status bars. Restores a maximized window as maximized, and releases held
  keys on toggle so a modifier cannot stick on the remote.
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
- [x] CI matrix verified green on Python 3.10, 3.11, 3.12, 3.13 and 3.14
  (Windows), so the `requires-python = ">=3.10"` bound in `pyproject.toml` is
  backed by a run rather than assumed.
- [x] Regression test that the PyInstaller entry point works without a package
  context — pointing PyInstaller at `openvncviewer/__main__.py` silently builds
  an executable that dies on its first relative import.
- [ ] No test against a real macOS host in CI (would need a self-hosted or
  macOS runner plus a Mac with Screen Sharing enabled).
- [ ] No coverage measurement, no linter (`ruff`/`flake8`) and no formatter in
  CI.
- [ ] The ZRLE decoder has no fuzzing. A malformed or hostile stream can raise
  `IndexError` out of `_read_tile`, which currently just ends the session —
  acceptable, but it should be a clean protocol error, and the decoder should
  be checked against out-of-bounds tile writes.
- [x] Benchmarks under `benchmarks/` for the decoder and the paint path, so
  section 4's numbers can be reproduced rather than taken on trust.
- [ ] The benchmarks are not run in CI, so a performance regression would go
  unnoticed until someone ran them by hand.

## 8. Documentation

- [x] README covering setup, running, scaling behaviour, the ARD login, and
  building the executable.
- [x] Screenshots in the README: a scaled session, the connect dialog, and the
  other clients that were tried first.
- [ ] No animated demo showing the window actually being resized, which is the
  one thing a still cannot convey.
- [ ] No troubleshooting guide (what "connection closed by server" means, what
  to enable in macOS Sharing settings, firewall notes).
- [ ] The README compares against other VNC clients using a screenshot of their
  installers. It is captioned to make clear they are good software that simply
  did not do these two things together, but before the repository goes public
  it is worth deciding whether naming and crossing out other projects is the
  tone wanted, since it uses their logos and reads as disparagement at a
  glance.
- [ ] No architecture document; the protocol/UI split is only explained in
  docstrings and the README table.

## 9. Repository and release process

- [ ] **`main` is unprotected.** Anyone with write access can push straight to
  it, and nothing forces a pull request or a passing build. This is *not* an
  oversight: both classic branch protection and the newer rulesets are refused
  on a private repository under a GitHub Free plan —

  ```
  403 Upgrade to GitHub Pro or make this repository public to enable this feature.
  ```

  It becomes available at no cost the moment the repository goes public, so it
  is deferred until then rather than worked around.

- [x] The ruleset is written and committed, ready to apply in one command once
  the repository is public (or the plan is upgraded):

  ```
  gh api --method POST repos/riazhassan-za/openvncviewer/rulesets \
         --input .github/branch-protection.json
  ```

  It requires a pull request with one approving review from a code owner,
  demands the five CI test jobs pass and be up to date with `main`, requires
  review threads to be resolved, dismisses stale approvals on new pushes, and
  blocks deletion and force-pushes of `main`.

- [x] `.github/CODEOWNERS` assigns every path to `@riazhassan-za`, so the
  required approval must come from the maintainer rather than any contributor
  with write access.

- [ ] **Decide what happens to the admin bypass before the second maintainer
  joins.** GitHub does not let anyone approve their own pull request, so a
  sole maintainer with a mandatory approval would be unable to merge their own
  work at all. `branch-protection.json` therefore grants always-bypass to the
  admin repository role (`actor_id: 5`). That is the "or admin" escape hatch,
  but it also means the rules are advisory for the owner. Remove the
  `bypass_actors` entry once a second reviewer exists.

- [x] `SECURITY.md` documents the disclosure process, the supported versions,
  and the known weaknesses from section 1, so a reporter can tell at a glance
  whether they have found something new.

- [ ] **Turn on GitHub private vulnerability reporting when the repository goes
  public.** `SECURITY.md` directs reporters to the Security tab's "Report a
  vulnerability" button, but the API returns 404 while the repository is
  private — it is a public-repository feature. Until it is enabled the policy
  falls back to asking for a private channel via a detail-free issue, which is
  clumsy. Enable with:

  ```
  gh api --method PUT repos/riazhassan-za/openvncviewer/private-vulnerability-reporting
  ```

- [ ] No issue or pull request templates, and no Dependabot configuration for
  the Actions and Python dependencies.

- [ ] Release assets carry no published checksum file. The SHA-256 is written
  into the v0.1.0 release notes by hand; a `.sha256` asset generated by CI
  would be verifiable without trusting the notes.

- [x] Actions token permissions fixed. The first tag build produced the
  executable but could not create the release (`403 Resource not accessible by
  integration`) because `GITHUB_TOKEN` defaults to read-only `contents`. The
  workflow now pins read-only at the top level and elevates `contents: write`
  only on the build job.
