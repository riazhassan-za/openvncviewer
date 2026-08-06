# Security Policy

OpenVNCViewer handles a macOS account password or a VNC password, and carries
the contents of a remote screen, so security reports are taken seriously.
Please read the
**Known and accepted weaknesses** section first — several of the obvious
problems are already documented, and a report restating one of them tells us
nothing new.

## Supported versions

| Version | Supported |
| --- | --- |
| latest release | Yes |
| anything older | No — upgrade |

This is a young project with a single maintainer. Only the latest release gets
fixes; there are no backport branches.

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Use GitHub's private vulnerability reporting: go to the **Security** tab and
choose **Report a vulnerability**. That opens a private channel visible only to
the maintainers.

If that option is not visible, open a normal issue saying only that you have a
security report and would like a private channel — **without any details** —
and you will be given somewhere to send them.

A useful report includes:

- What an attacker can achieve, not just what looks wrong
- The versions affected, and whether it applies to the prebuilt executable, a
  source install, or both
- Steps to reproduce, ideally as a failing test against the fake server in
  `tests/test_protocol.py`
- Your assessment of severity and any preconditions (network position,
  a malicious server, local access)

## What to expect

This is a spare-time project, so no response-time guarantee is offered in good
faith. What you can rely on:

- An acknowledgement that the report was received and read
- An honest assessment of whether it is already known, in scope, and fixable
- Credit in the release notes when a fix ships, unless you prefer otherwise
- Coordination on timing before anything is made public

If a report is judged out of scope or already documented, you will be told why
rather than ignored.

## Known and accepted weaknesses

These are documented, not undiscovered. Reporting them again will be closed as
known — but a **pull request fixing one is very welcome**, and they are roughly
in priority order. The full list lives in [TODO.md](TODO.md) §1.

1. **No server identity verification.** There is no host-key pinning,
   certificate check, or trust-on-first-use record. Anything that can
   intercept TCP/5900 can impersonate the Mac and be handed the account
   password. This is the most serious open issue, and no amount of care on our
   side of the exchange substitutes for it.
2. **All traffic after authentication is plaintext RFB.** Screen contents and
   keystrokes are unencrypted. Tunnel over SSH
   (`ssh -L 5900:localhost:5900 you@mac`) or a VPN on untrusted networks.
3. **The ARD scheme is weak by Apple's design.** It derives an AES-128 key with
   MD5 and encrypts the credential block in ECB mode. This cannot be changed
   without the server's cooperation; implementing one of Apple's RSA-AES
   security types (31/32/33/35) is the real fix.
4. **Legacy VNC authentication (type 2) is weak by design.** It is single DES
   with a 56-bit key, and the challenge and response are both visible on the
   wire, so an eavesdropper can recover the password offline. The scheme also
   caps the password at 8 characters. This is the protocol, not our
   implementation of it — but connecting to such a server means accepting it.
5. **The password is held in memory as a Python `str`**, which cannot be zeroed
   after use and may persist until garbage collection.
6. **A saved password is only as strong as the Windows account it is tied to.**
   Ticking "Save password" encrypts it with DPAPI, so the file is useless on
   another machine or to another user. It is *not* proof against code running
   as you, which can call `CryptUnprotectData` just as the viewer does — the
   same property every browser password store has. The option is off by
   default, and unavailable rather than downgraded where DPAPI is absent.
7. **Released executables are unsigned.** Verify the SHA-256 published in the
   release notes before running one.

## What is checked

Framebuffer updates are validated before they touch memory. A rectangle that
falls outside the framebuffer is refused rather than written — slice assignment
past the end of a `bytearray` extends it, and QImage holds a raw pointer into
that buffer, so an unchecked rectangle could reallocate it underneath the view.
ZRLE run lengths are clamped to the tile they belong to, and decompression is
capped at a size derived from the rectangle, so neither a runaway run nor a
compression bomb can spend arbitrary memory. Malformed tiles end the session
with a protocol error.

The Diffie-Hellman group offered for ARD authentication is validated before the
password is encrypted under a key derived from it. The client refuses a prime
below 1024 bits, a composite modulus (Miller-Rabin), a generator outside
`2 <= g < p`, a peer public key of 0, 1 or `p-1`, and a shared secret that
collapses to one of those. On any of these it hangs up without sending the
credential block at all.

Be clear about what that buys, because it is easy to overstate: it protects the
exchange from a **passive eavesdropper**, and catches parameters tampered with
in transit or a server that is simply broken. It does **not** protect against a
hostile server — that server holds the other private key and can decrypt the
credentials whatever group it chose. Only item 1 above would address that.

## Scope

In scope: everything under `src/openvncviewer/`, the packaging in
`packaging/`, and the release workflow in `.github/workflows/`.

Out of scope:

- Apple's Screen Sharing server and the ARD protocol design itself — report
  those to Apple
- Vulnerabilities in PySide6, Qt or `cryptography` — report those upstream,
  though do tell us if a pinned version needs bumping
- Anything requiring an attacker who already has code execution on the machine
  running the viewer
