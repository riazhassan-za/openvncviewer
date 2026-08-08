# Fallback adversarial-input / parser review

**Target:** `4dbba5643733d31b51f76aef9a6fe67e53af09b8`  
**Worktree:** the repository at the commit above  
**Nature of this report:** This report covers adversarial input handling. It reuses the safe conclusions of `06-network-protocol.md`, but adds bounded parser probes and a newly confirmed ZRLE truncation defect. No tracked source was changed and no non-loopback network target was contacted.

The checked-out `HEAD` was the requested commit. `src/openvncviewer/rfb.py` had SHA-256 `7ebe6e680f1994059b5cf936001f30d517de90e49e891722583fd0c28fcab274`, identical to `git show <commit>:src/openvncviewer/rfb.py`.

## Methodology

1. Mapped all peer-controlled parser fields in `src/openvncviewer/rfb.py`: banner and security negotiation; 8/16/32-bit lengths; ServerInit dimensions/name; server-message discriminator; framebuffer-update count and rectangle headers; Raw, CopyRect, ZRLE and DesktopSize bodies; colour-map count; and clipboard length.
2. Reviewed bounds and state changes before and after reads, multiplication, allocation, decompression, slice assignment, callbacks, and persistent-zlib use.
3. Ran the existing focused protocol suite offline:
   `PYTHONPATH=src python -m unittest discover -s tests -t . -p 'test_protocol.py'` — **26 tests passed** in 15.45 seconds.
4. Ran an exhaustive bounded corpus consisting of every one-byte ZRLE subencoding value (0-255), each wrapped in a valid small zlib stream and decoded into an 8x8/256-byte framebuffer.
5. Ran a deterministic mutation corpus of 5,000 valid zlib streams whose decompressed bodies were 0-64 pseudo-random bytes (`seed=0x4DBBA564`), each against a fresh 8x8 client. No case could allocate more than the small rectangle-derived cap.
6. Used `io.BytesIO` and non-allocating fake readers to exercise hostile `u16` dimensions, `u32`-style read requests, out-of-range CopyRect destinations, truncation, trailing ZRLE bytes, and repeated DesktopSize rectangles. The 17 GB request was recorded and deliberately aborted before allocation.
7. Reused the prior review's safe loopback evidence for the blocking-reader lifecycle; this report does not repeat external/network testing.

## Confirmed findings

### IF-1 — Medium — Truncated solid/palette ZRLE tiles are accepted and shrink the framebuffer under the view

**CWE:** CWE-20 (Improper Input Validation), with a potential CWE-416 (Use After Free) native consumer consequence.

**Exact locations:**

- `src/openvncviewer/rfb.py:471-499` decompresses and dispatches tiles, but only converts raised `IndexError`/`ValueError`; successful short slices are not rejected.
- `src/openvncviewer/rfb.py:513-518` accepts solid and packed-palette colours through `_cpixel()` without checking that each three-byte CPIXEL exists.
- `src/openvncviewer/rfb.py:522-547` allows packed-palette rows to be built from short/empty slices without requiring the encoded row bytes to exist.
- `src/openvncviewer/rfb.py:427-449` validates rectangle geometry but does not validate `len(pixels) == w * h * 4`; assigning a short row to a larger `bytearray` slice shrinks the framebuffer.
- `src/openvncviewer/rfb.py:694-696` implements `_cpixel()` as a non-failing slice plus `0xff`; a missing three-byte colour therefore becomes a one-byte “pixel.”
- `src/openvncviewer/ui.py:166-169` wraps this mutable framebuffer in a zero-copy `QImage`.

**Bounded reproduction/evidence:**

- `zlib.compress(b"\x01")` declares a solid tile but omits its three-byte colour. `_decode_zrle(0, 0, 64, 64, payload)` returned normally and changed framebuffer length from **16,384 to 10,240 bytes**.
- Exhausting all one-byte tile bodies showed that subencodings **1 through 16 all returned successfully despite missing colour/palette/row data**. On an 8x8 framebuffer, subencoding 1 changed 256 bytes to 160; subencodings 2-16 changed 256 bytes to 128. The other 240 values raised `RFBError`.
- In the 5,000-case deterministic corpus, 317 bodies returned successfully and **227 successful cases resized the fixed-size framebuffer**. Exceptions in the remaining cases were normalized to `RFBError`; there was no hang.
- The existing truncation regression at `tests/test_protocol.py:734-740` covers only raw ZRLE subencoding 0, whose explicit length check is at `rfb.py:505-511`; it does not cover solid or packed palettes.

**Prerequisite and impact:** A user must connect to a hostile server, or a MITM must modify plaintext RFB traffic. The peer can resize a `bytearray` that the code intentionally treats as fixed and that QImage may be reading through a native pointer. Confirmed effects are parser acceptance and buffer corruption/shrinkage; likely effects are a bad frame, disconnect, or process crash. PySide6 was unavailable, so native stale-pointer behavior and code-execution potential were not tested; severity is therefore Medium rather than High.

**Remediation:** Make `_cpixel()` an exact checked read (raise if `pos + 3 > len(raw)`). Before each palette and packed row, check the complete required byte range. Require every decoded tile to contain exactly `tw * th * 4` bytes and make `_blit()` reject any payload whose total length differs from `w * h * 4` before the first slice assignment. Add truncation tests for solid, every packed palette width, plain RLE, palette RLE, and each byte boundary.

### IF-2 — Medium — One compact update can trigger up to 65,535 framebuffer reallocations and callbacks

**CWE:** CWE-400 (Uncontrolled Resource Consumption).

**Exact locations:**

- `src/openvncviewer/rfb.py:397-402` trusts the peer's 16-bit rectangle count and has no per-update work/allocation budget.
- `src/openvncviewer/rfb.py:410-413` calls `_resize()` for every DesktopSize rectangle rather than accepting at most one terminal resize.
- `src/openvncviewer/rfb.py:364-368` allocates a fresh framebuffer and invokes `_on_resize()` each time.

**Bounded reproduction/evidence:** A 1,538-byte synthetic update containing 128 DesktopSize rectangle headers caused **128 allocations and 128 resize callbacks**, then completed with a 16,384-byte 64x64 framebuffer. Extrapolation is direct from the `u16` loop: one update can contain 65,535 headers. Even 64x64 rectangles represent roughly 1 GiB of cumulative allocation/copy-zeroing work at the protocol maximum, plus 65,535 queued cross-thread UI callbacks; larger accepted dimensions amplify this drastically.

**Prerequisite and impact:** A connected hostile server/MITM can cause CPU and allocator churn and flood the Qt event queue with minimal wire data relative to the work induced. This can freeze or terminate the viewer even if a global framebuffer-size ceiling is added. This shares the resource-budget family with duplicate finding NET-1 below, but the amplification through rectangle count/callbacks is a distinct parser behavior.

**Remediation:** Set a conservative maximum rectangles per update and a cumulative decoded-byte/work budget. Permit at most one DesktopSize pseudo-rectangle, require it to be the last rectangle (or otherwise follow the negotiated RFB semantics), coalesce resize notification, and reject repeated/zero-value resizes that provide no useful state transition.

### IF-3 — Low — ZRLE accepts and silently discards trailing decompressed bytes

**CWE:** CWE-20 (Improper Input Validation).

**Exact locations:** `src/openvncviewer/rfb.py:483-495` parses the expected tiles but never requires final `pos == len(raw)`; only expansion past the ceiling and tile underflow are rejected.

**Bounded reproduction/evidence:** A valid 8x8 solid tile followed by `TRAILING` (`zlib.compress(b"\x01\x01\x02\x03TRAILING")`) was accepted with no error. The trailing decompressed bytes were consumed from the persistent zlib stream and discarded.

**Impact:** No direct confidentiality or memory-safety impact was demonstrated. Acceptance weakens parser invariants, can hide malformed framing, and makes repeated-stream failures harder to diagnose. A hostile server can already terminate a session, so this is Low severity hardening.

**Remediation:** After decoding all expected tiles, require `pos == len(raw)` for the rectangle's decompressed contribution, accounting carefully for RFB's persistent ZRLE stream semantics. Add valid multi-rectangle persistent-stream tests before enforcing this invariant.

## Confirmed duplicate root causes from the network review

These were independently reproduced where noted but are duplicates of `06-network-protocol.md`, not additional unique issue counts.

### DUP-NET-1 — High — Unbounded peer lengths/dimensions are consumed before validation

**CWE:** CWE-770 (Allocation of Resources Without Limits).

**Exact locations:**

- `src/openvncviewer/rfb.py:234-237` — unbounded `u32` desktop-name length.
- `src/openvncviewer/rfb.py:313-315` — unbounded `u32` failure-reason length.
- `src/openvncviewer/rfb.py:364-368` — unchecked `u16 * u16 * 4` framebuffer allocation.
- `src/openvncviewer/rfb.py:403-409` — Raw data and `u32` compressed ZRLE body are read before destination validation/global bounds.
- `src/openvncviewer/rfb.py:471-480` — decompression limit is rectangle-derived without a global cap.

**Reproduction:** A fake reader presented a Raw 65535x65535 rectangle to a 1x1 framebuffer. The requested read sizes were `[3, 12, 17179344900]`: **17,179,344,900 bytes** was requested before `_blit()` could reject the rectangle. The fake reader aborted, so no large memory use occurred. Metadata and compressed-length fields independently permit up to `0xffffffff` bytes.

**Impact/remediation:** A hostile peer can stall body collection or induce OOM. Define global dimensions/pixels/bytes/text/compressed-body limits, use checked multiplication, and validate geometry before reading or allocating. This is the same High root cause reported as network finding 1.

### DUP-NET-2 — Medium — CopyRect validates source but not destination

**CWE:** CWE-20, with potential CWE-416 consequence.

**Exact locations:** `src/openvncviewer/rfb.py:406-407`, `452-466`; zero-copy consumer at `src/openvncviewer/ui.py:166-169`.

**Reproduction:** On a 4x4/64-byte framebuffer, CopyRect source `(0,0)`, destination `(65535,0)`, size 1x1 returned normally and changed the buffer length from **64 to 68**. Less extreme out-of-range destinations can overwrite the wrong row without growing the buffer.

**Impact/remediation:** Fixed-buffer invariant violation and possible stale QImage pointer/crash. Validate both source and destination through one shared rectangle checker before copying. This duplicates network finding 2; IF-1 reaches the same dangerous buffer-resize sink through a different ZRLE root cause.

### DUP-NET-3 — Medium — Declared bodies can stall forever because established reads have no deadline

**CWE:** CWE-400/CWE-772.

**Exact locations:** `src/openvncviewer/rfb.py:179-184`, `187-212`, `218-221`.

The prior safe loopback probe established that a server which accepts and sends no banner leaves the network thread alive after `stop()`; it exits only when the peer closes. This combines with hostile `u32` lengths: a peer can declare an allowed/unbounded body and drip or withhold it indefinitely. Add handshake/body/idle deadlines, close the `makefile()` reader, use `shutdown()`, and join/cancel deterministically.

### DUP-NET-4 — Medium — Loose banner parsing permits version/security downgrade behavior

**CWE:** CWE-20/CWE-757.

**Exact locations:** `src/openvncviewer/rfb.py:223-229`, `248-295`.

Only `startswith(b"RFB ")` is validated; the major version and exact syntax/newline are ignored, and most minor values map to 3.3. Security selection can then fall from ARD to legacy VNC when a username and account password were supplied. This is primarily negotiation security rather than memory parsing and duplicates network finding 4. Strictly accept supported `RFB 003.xxx\n` banners and bind user credential intent to allowed security types.

## Negative results and defenses confirmed

- Socket/file `_read(count)` rejects a short EOF rather than returning a partial protocol field (`rfb.py:208-212`). The availability caveat is the missing deadline, not silent partial-field parsing.
- Message discriminators and unrequested encodings fail closed (`rfb.py:371-395`, `412-414`). SetColourMapEntries' 16-bit count limits one body to 393,210 bytes (`rfb.py:376-379`).
- Clipboard receive has a 16 MiB hard limit and 1 MiB practical limit (`rfb.py:35-39`, `381-393`); absurd `u32` clipboard lengths are rejected before reading.
- Raw ZRLE tiles explicitly detect a short pixel body (`rfb.py:505-511`). Plain/palette RLE overlong runs are clamped to tile pixel count, bad palette indices fail, and run-length truncation becomes `RFBError` (`rfb.py:549-597`, `721-727`).
- Decompression uses `max_length` and rejects `unconsumed_tail`, so the existing 50 MB expansion-bomb regression passes (`rfb.py:471-482`, `tests/test_protocol.py:699-705`). This does not replace a global encoded-body/dimension limit.
- `_blit()` rejects geometrically out-of-frame Raw/ZRLE destinations before writing (`rfb.py:435-444`), and valid edge rectangles remain fixed-size. The identified problems are reading before this check, CopyRect bypassing it, and short decoded pixels shrinking slices.
- The 5,000-case deterministic ZRLE corpus completed without hangs or uncaught exception classes: 4,683 cases raised normalized `RFBError`. The 227 fixed-buffer invariant violations among successful cases are captured in IF-1.
- DH key bytes are bounded and malformed key sizes/groups are rejected before credentials; authentication-count fields are at most 255 bytes. No arithmetic wrap was found because Python integers do not overflow, although unbounded resulting allocations remain exploitable.
- No format-string execution, `eval`, pickle/object deserialization, filesystem extraction, command invocation, or inbound service was found in the RFB parser.

## Limitations

- This was bounded adversarial testing, not a coverage-guided native fuzzer run; no sanitizer/instrumented PySide build was available.
- PySide6 is absent in the environment, so the QImage consequences of framebuffer resizing were assessed from the zero-copy boundary and code comments, not reproduced in a GUI process. No claim of code execution is made.
- Deliberately no 4 GiB/17 GiB body, maximum-size framebuffer, decompression OOM, or 65,535-allocation update was executed. Fake readers and small extrapolatable cases were used to avoid harming the host.
- Persistent ZRLE was exercised for parser invariants, but interoperability sequences from multiple real VNC implementations were not available. The trailing-data remediation should be validated against compliant persistent-stream fixtures.
- The full Qt-dependent test suite could not run because PySide6 is not installed. The focused non-Qt protocol suite passed.
- Hostile-server/MITM access requires the user to initiate a connection. The application's already documented lack of server identity and transport integrity makes that boundary realistic, but there is no unsolicited remote listener.
