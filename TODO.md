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

An independent adversarial review of commit `4dbba56` is published in
[docs/](docs/). Both High-severity findings and three others are fixed in
0.10.0; the rest are open and indexed with their severities in
[docs/README.md](docs/README.md), which is the authoritative status page for
that audit. The highest open items are now **AUTH-01/NET-4** (security
negotiation can fall back from ARD to legacy VNC after a username was given)
and **AUTH-02** (ARD accepts small-subgroup peer keys).

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
- [x] **"Save password" is available and off by default.** When ticked the
  password is encrypted with Windows DPAPI, keyed to the logged-in account, so
  `servers.json` is useless on another machine or to another user. The module
  that writes that file never sees plaintext and cannot decrypt. Where DPAPI is
  absent the option is disabled rather than downgraded to a key stored next to
  the ciphertext.

  Still true, and stated in SECURITY.md rather than glossed over: this does not
  defend against code running as the same user, which can call
  `CryptUnprotectData` exactly as the viewer does.

- [ ] No integration with Windows Credential Manager proper, which would give
  the saved password an entry users can see and revoke from Control Panel
  rather than an opaque blob in a JSON file.

## 2. Platform and packaging

**Scope: this is a Windows x64 application.** It runs on 64-bit Windows 10
(1809+) and Windows 11, and connects to macOS Screen Sharing and standard VNC
servers. Everything below is measured against that target — the items are what
would have to change to widen it, not a backlog anyone has committed to.

- [x] Releases carry a **`SHA256SUMS.txt`** generated by CI, so a download can
  be verified without trusting the release notes. CI also checks the PE header
  of each artifact rather than trusting the runner label — a build that
  silently produced the wrong architecture would look fine until someone ran it.

- [ ] **ARM64 is blocked by `cryptography`, not by Qt.** PySide6 does publish a
  `win_arm64` wheel, so the viewer itself would build; but `cryptography`
  shipped Windows ARM64 wheels only in 46.0.0-46.0.3 and dropped them from
  46.0.7 onward. Building ARM64 today means pinning `cryptography==46.0.3` —
  four major versions behind, with an older bundled OpenSSL, in the library
  doing AES and DES for an application that handles account passwords.
  Tried on a `windows-11-arm` runner: the build fails at
  `pip install cryptography`, which falls back to compiling from source and
  needs Rust plus OpenSSL. Revisit if cryptography restores the wheel.

- [ ] **32-bit Windows and Windows 7/8 cannot be supported with the current
  toolkit**, and this is a Qt constraint rather than a Python one:

  - **PySide6 has never shipped a 32-bit (`win32`) wheel** — not in any
    release. Its Windows wheels are `win_amd64` and `win_arm64` only.
  - **Qt 6 requires Windows 10**, so even a 64-bit build cannot run on 7 or 8.

  The note this replaces claimed those targets "only need a rebuild with an
  older/32-bit interpreter". That was wrong, and would have sent someone down a
  dead end: no interpreter choice can conjure a wheel that does not exist.

  Supporting them means a **parallel port to PySide2 / Qt 5**, which does ship
  `win32` wheels and runs on Windows 7. That implies Python <= 3.10 (3.8 for
  Windows 7), a second UI code path for the Qt 5 API differences, and a doubled
  CI matrix — against dependencies that have been end-of-life since 2020, for
  operating systems that lost security updates in 2023. Recorded as a decision
  taken, not an oversight.
- [ ] **The executable is unsigned.** SmartScreen warns on first run on any
  machine that has not seen it before, and some corporate policies will block
  it outright. Needs an Authenticode certificate to fix properly.
- [ ] **One-file builds unpack to `%TEMP%` on every launch**, costing a few
  seconds of startup. A one-folder build removes the delay at the cost of
  shipping a directory. Consider offering both on releases.
- [ ] No installer, no Start Menu entry, no file association, no auto-update.
- [ ] **No macOS or Linux build of the *viewer* itself, and this is now the
  stated scope rather than a gap.** The project is documented as a Windows x64
  application that connects to macOS Screen Sharing and standard VNC servers.

  The protocol layer is portable (PySide6 + stdlib sockets) and carries no
  Windows assumptions, so a port is not blocked — but only Windows has been
  exercised, the Qt key mapping assumes a PC keyboard, and saved-password
  encryption is Windows DPAPI with no equivalent wired up elsewhere. Anyone
  attempting a port should start with those three.
- [ ] No application icon; the exe and window use Qt defaults.

## 3. Protocol coverage

- [ ] **Encodings are limited to Raw, CopyRect and ZRLE**, plus the DesktopSize
  pseudo-encoding. Tight, Hextile, RRE, CoRRE and zlib are not implemented.
  Tight in particular would cut bandwidth substantially on photographic content.
- [x] **Legacy VNC password authentication (security type 2) works**, so
  non-Apple servers connect. The client picks the strongest scheme it can
  satisfy from what the server offers and which credentials were supplied: ARD
  when a username was given, otherwise the VNC password, otherwise none.
  Verified against a live non-Apple server offering types 2 and 16.
- [ ] **Tight security (16), TLS (18) and VeNCrypt (19) are unsupported.** A
  server that merely offers one of these is fine — we pick type 2 instead — but
  one that *requires* it cannot be connected to. VeNCrypt in particular would
  bring transport encryption to non-Apple servers.
- [ ] Only the security type of Tight is missing; the **Tight encoding** is a
  separate item above and would cut bandwidth on photographic content.
- [x] **Clipboard synchronisation works in both directions**, on by default,
  with a per-session tick in the connect dialog. Windows CRLF is converted to
  the bare LF the protocol requires and back.

  Fixed while doing it: `ServerCutText` read a server-chosen `u32` length with
  no bound at all, so a server could declare 4GB and the client would try to
  read it. Text is now refused above a hard limit and dropped (rather than
  ending the session) above a smaller practical one.

- [ ] **macOS Screen Sharing does not carry the clipboard over RFB at all**, in
  either direction, so the feature does nothing when connected to a Mac. This
  is settled, not suspected. A traced session against macOS 14 showed
  `ClientCutText` leaving correctly with the handshake complete and being
  ignored by the pasteboard, and *no* `ServerCutText` arriving across a whole
  session of copying on the Mac — while the same build round-trips both
  directions against a standard VNC server. Apple's own client uses a private
  channel. Nothing in the RFB specification reaches the macOS pasteboard, so
  there is no fix here short of reverse-engineering that channel, which is out
  of scope.

  Worth recording because it cost two rounds of debugging correct code: an
  early conclusion that "incoming works" came from a trace taken against the
  Windows server, not the Mac. Check which host is connected before drawing a
  conclusion about a host.

- [ ] **Clipboard text is limited to Latin-1**, so emoji and CJK are replaced
  with `?`. The Extended Clipboard pseudo-encoding (`0xc0a1e5ce`) would carry
  UTF-8, and is worth doing for standard VNC servers. It is moot for macOS,
  which does not do RFB clipboard at all.

- [ ] `ConnectDialog.values()` now returns a **ten**-item positional tuple, and
  every option added to it has broken the same two tests — three times now,
  most recently for auto-reconnect. A named tuple would cost little and stop
  that recurring.
- [ ] **No cursor pseudo-encodings.** The server renders the pointer into the
  framebuffer, which is what we want for a scaled view, but it means the local
  cursor and the remote cursor can visibly disagree during fast movement.
- [ ] No `ExtendedDesktopSize`, no multi-monitor enumeration or per-screen
  selection. A multi-display Mac appears as one wide framebuffer.
- [ ] No client-initiated remote resize. Scaling is purely client-side by
  design — but some users will expect the Mac's resolution to follow the window
  and should be told it does not.
- [x] Reconnect on a dropped link. A session that was up is redialled 12 times
  over about 1m 45s, backing off from half a second to fifteen, when *Reconnect
  automatically if the link drops* is ticked. Never for a connection that
  failed to come up in the first place, and never after the server refused the
  credentials.

  The reconnect logic was the easy half. The half that actually mattered was
  **noticing**: a dropped Wi-Fi link does not break a TCP connection, and with
  no read timeout and nothing being written between framebuffer updates, a
  peer that vanished was never detected at all. TCP keepalive now does that in
  ~15s. Worth remembering before adding any other "the link went" behaviour —
  the session ending is not the same event as the link going.
- [x] RFB 3.3 servers work and are tested. A 3.3 server dictates the security
  type rather than offering a list, and differs from 3.7/3.8 in when it sends
  `SecurityResult`; both are handled.

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
- [x] **Alt maps to Command by default**, via a tick in the connect dialog, so
  Alt+C and Alt+V work on a remote Mac. Alt sits where Command does on a Mac
  keyboard, and it is the only reachable choice: Command was previously on the
  Windows key, which Windows largely keeps for itself. Unticking reverts to
  Alt=Option, Win=Command for non-Apple servers.

- [x] **Modifiers reach the remote rather than the local menu bar.** The view
  claims `ShortcutOverride` for every key except F11; without that, Alt opened
  the menu bar and never reached the server at all, and Alt+F was eaten as a
  menu mnemonic.

- [ ] Beyond that single tick there is still no general key-remapping UI.
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
- [x] **HiDPI is handled and tested.** The cached pixmap is allocated in device
  pixels with `devicePixelRatio` set on it, so the remote desktop is drawn at
  the resolution the screen can actually show.

  The original note here guessed wrong about what was at risk. It suspected
  **clicks**; those were always exact, because `target_rect()` and the mouse
  position are both in logical pixels and the scale factor cancels. The real
  fault was **resolution**: the pixmap was sized in logical pixels, so at
  higher scalings it held a fraction of the pixels the display could show and
  Qt stretched the difference.

  | Display scaling | Pixmap coverage before | After |
  | --- | --- | --- |
  | 100% | 97% | 97% |
  | 150% | 64% | 97% |
  | 200% | 48% | 97% |

  Measured, not assumed: `tests/test_hidpi.py` drives a child process per
  scale factor, since Qt reads `QT_SCALE_FACTOR` once when the QApplication is
  built and it cannot be changed mid-run.

- [ ] The cost of the above is a pixmap with `ratio**2` more pixels, so a full
  rescale at 200% does roughly four times the work. Damage-limited repainting
  keeps the common case cheap, but there is no benchmark that runs at a scale
  factor, so that multiplier is reasoned rather than measured.
- [x] Full-screen mode via **View → Full screen** or **F11**, hiding the menu
  and status bars. Restores a maximized window as maximized, and releases held
  keys on toggle so a modifier cannot stick on the remote.
- [x] Recently connected servers are remembered between runs, in a dropdown on
  the connect dialog with a user-chosen **Server name** shown alongside, in the
  window title and in the status bar. Entries are added only after a successful
  connection, and **Clear history** empties the list. Stored as `servers.json`
  in the user config directory; passwords are never written to it.
- [ ] No import or export of the server list, and no ordering other than
  most-recently-connected — no pinning or manual sort.
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
- [x] **The decoder is hardened against a hostile stream.** Three real problems
  were found and measured before being fixed:

  | Input | Before | After |
  | --- | --- | --- |
  | RLE run longer than its tile | 39 KB produced 40 MB (1021x) | 1x, clamped |
  | zlib compression bomb | 194 KB expanded to 200 MB | refused |
  | Rectangle outside the framebuffer | **grew the `bytearray`** | refused |

  The last was the serious one. Slice assignment past the end of a `bytearray`
  extends it rather than failing, and QImage holds a raw pointer into that
  buffer — so a malformed rectangle reallocated the framebuffer underneath the
  view. `_blit` now bounds-checks once per call, which also covers CopyRect
  sources. Malformed tiles raise `RFBError` rather than escaping as `IndexError`
  or `ValueError`.

  Cost: palette-RLE decoding went from 21.7 to 19.7 Mpx/s, all of it the run
  clamp and the palette bounds check. Padding the palette to avoid the check
  recovers 0.5 Mpx/s and was rejected — it would silently paint a wrong colour
  instead of reporting malformed data.

- [ ] No property-based or generated fuzzing over the decoder. The cases above
  were found by reasoning about the code and then measured; a fuzzer would
  cover the combinations nobody thought of.
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

- [x] **`main` is protected by the "Protect main" ruleset**, live since the
  repository went public (rulesets are refused on a private repository under a
  GitHub Free plan, which is why this was deferred rather than worked around).
  `.github/branch-protection.json` is the source of truth and can be reapplied
  in one command:

  ```
  gh api --method PUT repos/riazhassan-za/openvncviewer/rulesets/20514270 \
         --input .github/branch-protection.json
  ```

  It forces a pull request, demands the five CI test jobs pass and be up to
  date with `main`, requires review threads to be resolved, dismisses stale
  approvals on new pushes, and blocks deletion and force-pushes of `main`.

- [x] **Release tags are protected.** `.github/tag-protection.json` restricts
  creating, moving and deleting `refs/tags/v*` to the admin role, and the
  release job independently refuses to publish a tag whose commit is not
  contained in `main`. Before this, a tag was sufficient to publish an official
  binary from any commit through a job holding `contents: write` — audit
  finding SC-02/CFG-02. The two controls are deliberately independent: the
  workflow check still holds if the ruleset is changed or removed.

  ```
  gh api --method PUT repos/riazhassan-za/openvncviewer/rulesets/20584903 \
         --input .github/tag-protection.json
  ```

  Not done: **signed tags**. The ruleset can require them, but every existing
  tag is unsigned and there is no signing key set up, so turning it on would
  block releases rather than secure them. Revisit alongside Authenticode
  signing for the executable, which is the same missing piece.

- [x] **One approving review from a code owner is required**, and
  `.github/CODEOWNERS` assigns every path to `@riazhassan-za`, so it binds to
  the maintainer rather than to any contributor with write access.

  This was briefly set to zero. The reasoning was that on a personal repository
  nobody else has write access, so the rule gated only the owner — who cannot
  approve their own pull request — and every merge therefore needed the admin
  override, which makes the protection advisory in practice.

  **That reasoning was wrong: a second collaborator with write access exists.**
  It was asserted without checking the collaborator list. With two write-capable
  accounts the rule protects against something real — either can otherwise merge
  their own work to `main` unreviewed — so it is back on. Check
  `gh api repos/riazhassan-za/openvncviewer/collaborators` before reasoning
  about who can merge what.

- [ ] **`require_last_push_approval` is off.** Turning it on stops the account
  that pushed last from also being the approver, which is the point of the rule
  once two maintainers can review each other. Left as it was rather than
  tightened silently; consider it alongside the bypass below.

- [ ] **Decide what happens to the admin bypass.** The ruleset grants
  always-bypass to the admin repository role (`actor_id: 5`), which is the "or
  admin" escape hatch — and also means the rules stay advisory for the owner.
  Now that a second reviewer exists, the approval requirement can be satisfied
  without it, so the `bypass_actors` entry can go.

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
