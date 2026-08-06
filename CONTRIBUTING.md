# Contributing to OpenVNCViewer

Thanks for taking a look. [TODO.md](TODO.md) is the maintained list of open
work — the security items in §1 and the ZRLE performance work in §4 are the
highest value.

## Setup

```
git clone https://github.com/riazhassan-za/openvncviewer.git
cd openvncviewer
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

## Running the tests

```
set QT_QPA_PLATFORM=offscreen
.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

Everything runs offline. `tests/test_protocol.py` stands up a fake server that
speaks real macOS Screen Sharing — it announces `RFB 003.889`, does a genuine
Diffie-Hellman exchange, and decrypts the credential block to check the client
sent the right thing. You do **not** need a Mac to work on this project.

## How to test protocol changes

Please do not hand-verify decoder changes by looking at a screenshot. The
pattern used throughout `tests/test_protocol.py` is:

1. Encode the data in the test, independently of the client code.
2. Compute the expected framebuffer with a plain, obvious reference model
   (`Reference`), not by reusing the client's helpers.
3. Assert the client's framebuffer matches byte for byte.

If you fix a bug, **prove the test catches it**: disable your fix, watch the
new test fail, then re-enable it. The handshake-race regression test was
validated exactly that way.

## Performance changes

Measure, do not assume. There are benchmarks for the two hot paths:

```
.venv\Scripts\python.exe benchmarks\decode.py
.venv\Scripts\python.exe benchmarks\paint.py
```

Run them before and after your change and put both numbers in the pull
request. This is not ceremony — during the work that produced these
benchmarks, an "obviously faster" single-copy blit for full-width rectangles
turned out to be **2.3x slower** than copying row by row, because row-sized
pieces stay in cache and a 30 MB memcpy does not. It would have shipped as an
improvement without a measurement.

If you make something faster, add the numbers to [TODO.md](TODO.md) §4 so the
next person inherits a baseline rather than a claim.

## Code style

- Match the surrounding code. Standard library only in `rfb.py`, plus
  `cryptography` — keep Qt out of the protocol layer so it stays testable
  headless.
- Comments explain *why*, not *what*. If a line needs a comment to say what it
  does, rewrite the line.
- Keep it small. This is a viewer, not a framework; prefer the least code that
  solves the problem over a configurable abstraction.
- No new runtime dependencies without discussion. Every one of them lands in
  the 49 MB executable.

## Reporting bugs

Please include:

- macOS version and how Screen Sharing is configured
- Windows version, and whether you used the exe or ran from source
- The exact error text from the dialog
- The remote display resolution (the status bar shows it once connected)

Protocol bugs are much easier to fix with a description of the encoding in
play. If you can, note whether the Mac is on a Retina display and whether the
failure happens at connect time or during a session.

## Licensing of contributions

This project is GPL-3.0-or-later. By submitting a pull request you agree your
contribution is licensed under the same terms. Do not paste code from other VNC
implementations — the protocol layer here was written from the specification
and should stay that way, so the licensing stays unambiguous.
