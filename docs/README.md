# Security audit — reports and status

An independent adversarial review of OpenVNCViewer at commit `4dbba56`, plus a
requirements-to-test traceability matrix. Published because a viewer that asks
people to type their macOS account password deserves to have its weak points
written down in public rather than discovered privately.

**Read this page before the reports.** Both High-severity findings and three
others are fixed in 0.10.0; the rest are still open, and the reports describe
them in enough detail to act on. That is a deliberate trade-off: everything
remaining is reachable only by a **hostile or impersonated server**, all of it
is already visible to anyone reading the source, and the architectural
exposures were documented in [SECURITY.md](../SECURITY.md) and
[TODO.md](../TODO.md) long before this audit.

**If you run an older build, update.** The fixed findings apply to 0.9.1 and
earlier.

If you find something *not* listed here, please use the private channel in
[SECURITY.md](../SECURITY.md) rather than opening a public issue.

## Status

Fixed in **0.10.0**:

| Finding | Severity | What it was |
| --- | --- | --- |
| NET-1 / F2 | **High** | Peer-controlled lengths and dimensions were read before any global bound: a 65535x65535 rectangle issued a 17 GB read request, and a `u16` desktop pair was a 17 GB allocation |
| SC-02 / CFG-02 | **High** | Any `v*` tag published an official release, from any commit, with `contents: write` |
| IF-1 | Medium | A truncated ZRLE tile returned fewer pixels than it claimed, shrinking the framebuffer under a live `QImage` pointer |
| NET-2 / F1 / BL-2 | Medium | CopyRect validated its source but not its destination, growing the framebuffer the same way |
| AUTH-03 | Low | Unticking **Save password** did not revoke the stored token unless the next connection happened to succeed |

IF-1 and NET-2 are the same sink — the framebuffer is a `bytearray` whose
length is an invariant because Qt wraps it zero-copy. `_blit` has guarded the
geometry since 0.6.0; these were two ways of reaching it around that guard.
Both now go through one shared rectangle check, and `_blit` additionally
requires the pixel payload to be exactly `w * h * 4` bytes, which closes the
whole class rather than the two instances found.

NET-1 is now refused *before* the read or allocation rather than after: the
rectangle's geometry is checked before its body is read at all, and dimensions,
compressed bodies, the desktop name and the authentication failure reason each
have a ceiling. The 17 GB read request is gone — the same hostile input now
produces reads of 3 and 12 bytes, then an error.

SC-02 has two independent controls, because one of them is configuration that
could drift: a repository ruleset restricts creating, moving and deleting
`v*` tags ([tag-protection.json](../.github/tag-protection.json)), and the
release job refuses to publish a tag whose commit is not contained in `main`.

Still open, and tracked in [TODO.md](../TODO.md):

| Finding | Severity | Summary |
| --- | --- | --- |
| IF-2 / BL-3 | Medium | One update can carry 65,535 DesktopSize rectangles, each causing a reallocation and a UI callback |
| AUTH-01 / NET-4 | Medium | Security negotiation can fall back from ARD to legacy VNC after a username was supplied |
| AUTH-02 | Medium | ARD accepts small-subgroup peer keys |
| NET-3 | Medium | No post-connect deadlines; `stop()` does not reliably interrupt a buffered read |
| BL-1 | Medium | Queued callbacks from a stopped session can reach its replacement |
| BL-5 | Medium | Concurrent instances can undo history deletions through whole-file writes |
| SC-01 / CFG-01 | Medium | Actions are referenced by mutable tags in a job holding `contents: write` |
| SC-03 / CFG-03 | Medium | Dependencies are not hash-pinned at build time |
| SC-04 | Medium | Releases are unsigned; `SHA256SUMS.txt` proves integrity, not authenticity |
| IF-3 | Low | Trailing decompressed ZRLE bytes are accepted and discarded |
| F3 | Low | Server-supplied strings reach Qt widgets that can interpret rich text |

`SC-05` / `CFG-04` — no mandatory reviewer on `main` — was fixed separately by
restoring the code-owner approval requirement.

## The reports

| File | Area |
| --- | --- |
| [01-input-fuzzing.md](01-input-fuzzing.md) | Adversarial input and parser probes |
| [02-injection-review.md](02-injection-review.md) | Injection surfaces and workflow expressions |
| [03-auth-secrets.md](03-auth-secrets.md) | Authentication and credential storage |
| [04-business-logic.md](04-business-logic.md) | Session and state-machine logic |
| [05-supply-chain.md](05-supply-chain.md) | Dependencies, build and release trust |
| [06-network-protocol.md](06-network-protocol.md) | RFB network trust boundary |
| [07-config-infrastructure.md](07-config-infrastructure.md) | Repository and CI configuration |
| [test-matrix.md](test-matrix.md) | Requirements-to-test traceability and coverage gaps |

## A note on the reports' own claims

Every reproduction quoted for the three fixed findings was re-run independently
against the real code before the fix was written, and again afterwards. They
were accurate — the byte counts in the reports match what the code actually did,
including `16384 -> 10240` for a truncated solid tile and `64 -> 68` for an
out-of-range CopyRect destination. The reports are worth trusting on detail.

Two limitations the reports state about themselves are worth repeating: the
environment had no PySide6, so the `QImage` consequences of a resized
framebuffer were reasoned about from the zero-copy boundary rather than
reproduced in a GUI process, and no claim of code execution is made.
