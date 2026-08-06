# Security Policy

OpenVNCViewer handles a macOS account password and carries the contents of a
remote screen, so security reports are taken seriously. Please read the
**Known and accepted weaknesses** section first — several of the obvious
problems are already documented, and a report restating one of them tells us
nothing new.

## Supported versions

| Version | Supported |
| --- | --- |
| 0.2.x | Yes |
| 0.1.x | No — upgrade to 0.2.x |

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

1. **Diffie-Hellman parameters from the server are trusted blindly.** The
   generator, prime and peer public key used by ARD authentication are read
   straight off the wire and used without validation. A malicious or spoofed
   server can supply degenerate parameters and recover the macOS password from
   the exchange. This is the most serious open issue.
2. **No server identity verification.** There is no host-key pinning,
   certificate check, or trust-on-first-use record. Anything that can
   intercept TCP/5900 can impersonate the Mac.
3. **All traffic after authentication is plaintext RFB.** Screen contents and
   keystrokes are unencrypted. Tunnel over SSH
   (`ssh -L 5900:localhost:5900 you@mac`) or a VPN on untrusted networks.
4. **The ARD scheme is weak by Apple's design.** It derives an AES-128 key with
   MD5 and encrypts the credential block in ECB mode. This cannot be changed
   without the server's cooperation; implementing one of Apple's RSA-AES
   security types (31/32/33/35) is the real fix.
5. **The password is held in memory as a Python `str`**, which cannot be zeroed
   after use and may persist until garbage collection.
6. **Released executables are unsigned.** Verify the SHA-256 published in the
   release notes before running one.

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
