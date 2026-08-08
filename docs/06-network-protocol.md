# Network / protocol security audit — `4dbba5643733d31b51f76aef9a6fe67e53af09b8`

## Scope and attack surface

Reviewed the exact checked-out commit (the working-tree `rfb.py` SHA-256 matched `git show` at the target commit). The reachable network boundary is the outbound RFB TCP client in `src/openvncviewer/rfb.py`: user-supplied host/port resolution and connection, RFB 3.3/3.7/3.8 negotiation, None/VNC/ARD authentication, ServerInit, framebuffer messages (Raw, CopyRect, ZRLE, DesktopSize), colour-map messages, bell, and clipboard messages. There is no listening socket, HTTP API, CORS boundary, or remotely triggered discovery endpoint. Connecting to arbitrary IPs is the application's intended function, not an SSRF privilege crossing.

Hostile-server and active-MITM reachability is high once a user initiates a connection. Findings below assume that prerequisite; none is remotely reachable before the user connects.

## Findings

### 1. High — Peer-controlled dimensions and lengths are consumed before global/semantic bounds (CWE-770, allocation of resources without limits)

**Boundary:** ServerInit, authentication failure strings, Raw/ZRLE framebuffer rectangles, and DesktopSize.

**Exact locations:**

- `src/openvncviewer/rfb.py:234-237` — unbounded 32-bit desktop-name length is passed to `_read()`.
- `src/openvncviewer/rfb.py:287-289` — unbounded 32-bit failure-reason length is passed to `_read()`.
- `src/openvncviewer/rfb.py:364-368` — server dimensions directly allocate `width * height * 4` bytes.
- `src/openvncviewer/rfb.py:397-410` — Raw payload is read before `_blit()` validates the rectangle; ZRLE's 32-bit compressed length is unbounded and read before rectangle validation/decompression.
- `src/openvncviewer/rfb.py:418-420` — DesktopSize directly reaches `_resize()`.
- `src/openvncviewer/rfb.py:474-481` — the decompression ceiling is rectangle-derived but has no global ceiling and is computed before geometry is validated.

**Evidence / safe repro:** An offline fake reader supplied a one-rectangle update with `w=h=65535`, encoding Raw, and stopped before allocation. `_handle_framebuffer_update()` requested reads of `[3, 12, 17179344900]`; the final request is **17,179,344,900 bytes** even when the actual framebuffer is 1x1. Separately, the protocol permits ServerInit/DesktopSize dimensions up to 65535x65535, causing the same approximately 16 GiB framebuffer allocation. Name, failure-reason, and compressed-ZRLE fields permit up to 4 GiB each. No large allocation was performed during testing.

**Impact:** A hostile server or MITM can make the process block while accumulating an oversized body, terminate it with `MemoryError`, or drive the machine into memory pressure/OOM termination. The Raw path does this even for a rectangle that is obviously outside the current framebuffer. A small compressed payload can no longer expand without limit, but a server can still declare/send a very large compressed body or first establish enormous rectangle dimensions so that the rectangle-derived decompression cap is enormous.

**Remediation:** Define global limits for width, height, total pixels/framebuffer bytes, desktop/failure text, encoded rectangle bytes, and per-update work. Validate destination geometry and checked multiplication *before* reading payloads or computing decode limits. Bound ZRLE compressed bytes independently of expanded bytes. Reject DesktopSize and initial dimensions over policy before allocating. Prefer a bounded exact-read loop that enforces both per-field and per-session byte budgets.

### 2. Medium — CopyRect omits destination bounds validation and can resize the buffer under QImage (CWE-416, use after free / CWE-20, improper input validation)

**Boundary:** CopyRect framebuffer encoding.

**Exact locations:**

- `src/openvncviewer/rfb.py:406-407` dispatches CopyRect directly.
- `src/openvncviewer/rfb.py:452-466` validates only the source rectangle, then assigns to destination slices without checking `x + w` / `y + h`.
- `src/openvncviewer/ui.py:166-169` wraps the same `bytearray` in a zero-copy `QImage`.
- `src/openvncviewer/rfb.py:435-443` documents why resizing this buffer is unsafe, but that guard is used only by `_blit()`.

**Evidence / safe repro:** With a 4x4 framebuffer, source `(0,0)`, destination `(65535,0)`, and size 1x1, direct `_copy_rect()` changed `len(framebuffer)` from 64 to 68 instead of raising. Python slice assignment with a start beyond the end appends; on a live UI this can reallocate the storage to which QImage retains a raw pointer. Existing hostile-stream tests cover `_blit()` bounds but do not exercise CopyRect destination bounds.

**Impact:** A hostile server/MITM can corrupt framebuffer state and potentially leave Qt reading stale native memory, producing a deterministic disconnect/crash and a memory-unsafe stale-pointer condition. Code-execution exploitability was not established, so severity is capped at Medium.

**Remediation:** Apply one shared destination-rectangle validator before dispatching every encoding and before reading its payload. CopyRect must validate both source and destination. Never resize an exported framebuffer in place; allocate a replacement only through the resize path and recreate QImage before painting it.

### 3. Medium — No post-connect deadlines; `stop()` does not reliably interrupt the buffered reader (CWE-400, uncontrolled resource consumption; CWE-772, missing release of resource)

**Boundary:** DNS/connect lifecycle and every RFB exact read.

**Exact locations:**

- `src/openvncviewer/rfb.py:179-184` closes only `_sock`; it neither shuts down the connection nor closes/joins `_reader`/the thread.
- `src/openvncviewer/rfb.py:187-204` permits the daemon thread to remain blocked and again closes only `_sock`.
- `src/openvncviewer/rfb.py:208-212` performs a blocking buffered read with no deadline.
- `src/openvncviewer/rfb.py:218-221` gives connect attempts 15 seconds but immediately changes the established socket to infinite timeout.

**Evidence / safe loopback repro:** A loopback server accepted TCP and sent no RFB banner. After `client.stop()` and a 300 ms join, `client._thread.is_alive()` remained `True`; it exited only after the peer socket was closed. This follows `socket.makefile()` ownership semantics: closing the socket object alone does not necessarily close the underlying descriptor while the file object exists. The focused protocol suite also emitted repeated `ResourceWarning: unclosed <socket.socket ...>` messages despite passing.

`socket.create_connection(..., timeout=15)` also does not impose a deadline on the preceding blocking `getaddrinfo()` call, and its timeout is per attempted address rather than a single DNS+connect budget.

**Impact:** A hostile or nonresponsive server can retain a network thread, socket/file object, callbacks/client state, and credentials indefinitely. Reconnect appears to work because callbacks are silenced, but repeated attempts against stalling peers accumulate threads and resources. A resolver problem can likewise leave an uncancellable background attempt. This is an availability/local resource-exhaustion issue; the GUI thread itself is not blocked.

**Remediation:** Use explicit total deadlines for resolution/connect, handshake, idle reads, and large message bodies. Resolve asynchronously with a cancellable strategy where supported. On stop, set state, call `shutdown(SHUT_RDWR)`, close the buffered reader and socket in a defined order, and join with a bounded timeout. Ensure `finally` closes `_reader` as well as `_sock`. Consider avoiding `makefile()` and implementing `recv_into` exact reads with deadline/cancellation checks.

### 4. Medium — Credential-aware security selection silently falls back from ARD to legacy VNC (CWE-757, selection of less-secure algorithm during negotiation)

**Boundary:** RFB version/security negotiation.

**Exact locations:**

- `src/openvncviewer/rfb.py:223-229` accepts any banner starting `RFB `, ignores the major version, and maps all minors except exactly 7 or >=8 to RFB 3.3.
- `src/openvncviewer/rfb.py:248-264` trusts the unauthenticated offered security list.
- `src/openvncviewer/rfb.py:276-295` chooses ARD only if offered, then chooses VNC whenever a password is present—even when a username indicates macOS-account credentials.
- `src/openvncviewer/rfb.py:297-308` derives and returns a legacy DES challenge response from that password.

**Evidence / safe offline repro:** `_choose_security([SEC_VNC], "macuser", "AccountPassword")` returns `SEC_VNC`. Thus an endpoint expected to be a Mac can omit type 30 and obtain a type-2 challenge response derived from the first eight UTF-8 password bytes. The loose version parsing also permits an unauthenticated peer to force 3.3's server-dictated security mode with a banner such as `RFB 999.003\n`.

**Impact:** A hostile endpoint can induce disclosure of a crackable VNC challenge/response based on the user's macOS account password rather than failing closed when the expected ARD mode disappears. An active MITM can do the same, although the already documented lack of server identity makes full credential theft via a fake ARD exchange even easier; this finding is therefore negotiation-specific hardening, not an independent cure for MITM.

**Remediation:** Bind credential intent to allowed authentication modes: when a username is supplied, require ARD (or a future authenticated encrypted type) unless the user explicitly opts into VNC-password fallback with a separate credential. Strictly parse `RFB 003.xxx\n`, reject unsupported major/minor versions, and prevent silent version downgrade. Ultimately authenticate the server and cryptographically bind the negotiation.

## Already documented / accepted limitations (reported separately)

These are explicit in `SECURITY.md` and `TODO.md` and are not counted as new findings:

1. **No server identity verification:** a MITM or hostile server can impersonate the target and collect credentials.
2. **No transport confidentiality or integrity after authentication:** screen, clipboard, keyboard, and pointer traffic are plaintext and mutable; SSH/VPN tunneling is advised.
3. **ARD design weakness:** MD5-derived AES-128 key and ECB credential encryption.
4. **Legacy VNC weakness:** 56-bit DES, observable challenge/response, offline recovery, and an eight-byte password limit.
5. **No support for encrypted RSA-AES/TLS/VeNCrypt security types.**
6. **Password retained as immutable Python `str`.**
7. **No automatic reconnect/retry.**

The current DH checks do not establish server identity or negotiation integrity. Their peer-key check rejects 0, 1, and `p-1`, but it is not a general subgroup-membership proof; this does not add a materially stronger attack than the documented unauthenticated-server boundary.

## Negative results / defenses confirmed

- `_read()` rejects short EOF reads, and `sendall()` handles partial writes (`rfb.py:208-218`). Writes are serialized with a lock and input is suppressed until ServerInit completes (`rfb.py:214-217`, `239-244`, `617-649`).
- Unsupported server message types and unrequested encodings terminate the session rather than being interpreted (`rfb.py:371-395`, `397-414`).
- Clipboard receive and send lengths have 16 MiB hard / 1 MiB practical limits (`rfb.py:35-39`, `381-393`, `639-648`).
- Raw and ZRLE writes eventually pass through destination bounds checks (`rfb.py:426-449`); the exceptions are pre-validation payload reads and CopyRect described above.
- ZRLE expanded output has a rectangle-derived cap, overlong runs are clamped to tile size, palette indices are checked, and malformed/truncated tiles become protocol errors (`rfb.py:470-499`, `501-597`, `721-727`). A 50 MB expansion bomb test passes.
- ARD key length is bounded to 1024-8192 bits; modulus size/primality, generator range, degenerate peer keys, and degenerate shared secrets are checked before credentials are sent (`rfb.py:312-350`, `659-689`).
- Exceptions in the network thread end the session and expose only the exception text to the local UI, not a traceback (`rfb.py:187-204`, `ui.py:783-794`). No network-side error response leaks local metadata.
- No inbound listener, HTTP endpoint, CORS policy, or rate-limited authentication service exists. DNS/internal-address reachability is user-directed functionality, not an externally triggerable SSRF path.

## Test notes

Only local/offline tests were run; no external hosts were contacted and tracked source was not altered.

- Focused suite: `PYTHONPATH=src python -m unittest discover -s tests -t . -p 'test_protocol.py'` — **26 passed** in 15.4s, with unclosed-socket ResourceWarnings.
- Safe probes confirmed CopyRect buffer growth, pre-validation 17,179,344,900-byte Raw read request, and a stalled reader surviving `stop()` until peer close.
- Full-suite attempt was not usable in this environment because PySide6 is absent: protocol/non-Qt tests ran, while Qt imports and dependent probes failed. This is an environment limitation, not a target-code test failure.
