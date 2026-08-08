# Business-logic, state-machine, and concurrency audit

**Target:** the repository at the commit above  
**Commit:** `4dbba5643733d31b51f76aef9a6fe67e53af09b8`  
**Audit focus:** clipboard synchronization and overwrite/disclosure, connect/disconnect/reconnect sequencing, input forwarding, recent-server/password save/delete flows, UI/network thread handoff, DesktopSize/update behavior, and zero-copy framebuffer/QImage lifetime.

The named commit itself changes only `.github/workflows/ci.yml`; this report audits the complete source snapshot at that commit, as requested.

## Methodology

* Read the RFB state machine, Qt signal bridge, `RemoteView`, clipboard workflow, and recent-server persistence code.
* Traced attacker-controlled server messages from the network thread through queued Qt signals into mutable `MainWindow`/`RemoteView` session state.
* Ran safe local/offscreen Python reproducers. No external VNC server was contacted. Memory-exhaustion behavior was tested in a subprocess with `RLIMIT_AS=256 MiB`.
* Ran the repository suite in a temporary dependency environment: **140 tests passed, 10 skipped**. Existing tests do not exercise queued signals that survive a session transition, concurrent history writers, CopyRect destination bounds, or framebuffer dimension limits.
* Verified the checkout remained clean after testing; no tracked source was modified.

## Confirmed findings

### 1. Queued callbacks from an old session are applied to the new session

**Severity:** High  
**CWE:** CWE-362 (Concurrent Execution using Shared Resource with Improper Synchronization), CWE-367 (TOCTOU Race Condition)  
**Locations:**

* `src/openvncviewer/rfb.py:176-185` — `stop()` replaces callbacks, but cannot revoke callbacks already emitted.
* `src/openvncviewer/ui.py:604-608` — all clients emit through one long-lived `ClientSignals` object; signal payloads carry no client/session identity.
* `src/openvncviewer/ui.py:702-731` — reconnect disconnects the old client and immediately installs a new one using the same signals.
* `src/openvncviewer/ui.py:742-753` — any queued resize operates on current state, commits current `_pending_history`, and primes current clipboard state.
* `src/openvncviewer/ui.py:763-768` — any queued clipboard event overwrites the local clipboard according to the current session's policy.
* `src/openvncviewer/ui.py:783-793` — any queued disconnect unconditionally clears the current client and view.
* `src/openvncviewer/ui.py:163-170` — resize dimensions from the signal are combined with whichever client's framebuffer is currently attached.

**Preconditions:** The old connection emits resize, clipboard, damage, or disconnect from its network thread; before the UI thread dispatches the queued event, the user disconnects/reconnects or starts another connection. A slow/busy UI or a server timing an update makes the window larger.

**Attack/user journey:**

1. The user is connected to server A and starts connecting to server B.
2. A emits a callback just before `stop()` silences future callbacks. The Qt event is already queued.
3. `connect_to()` attaches B and sets B's `_pending_history` and clipboard policy.
4. The old event runs without an A/B generation check.
   * Old `disconnected` detaches B and sets `self.client = None`, while B's network client can remain running and unmanaged.
   * Old `clipboard` overwrites the user's local clipboard after the transition.
   * Old `resized` wraps B's framebuffer using A's dimensions and marks B as successfully connected even if B never completed its handshake.

**Evidence/reproduction:** An offscreen worker thread emitted signals, the main thread installed a new 2x2/16-byte stub client before processing Qt events, and then called `processEvents()`:

```text
stale_resize_image 100 100 new_fb_bytes 16
premature_history ['failed-new'] pending None
clipboard_after_stale_signal 'OLD-SERVER-TEXT'
client_after_stale_disconnect None view_client None
```

The 100x100 `QImage` backed by only 16 bytes also creates an out-of-bounds native read risk on a later paint. This was confirmed up to construction; deliberately forcing a potentially unsafe paint was not necessary.

**Business impact:** Cross-server clipboard overwrite; misleading connection state; recording a failed/unverified server in history; loss of control of a live new connection; stale framebuffer dimensions; and possible crash/native memory-safety consequences. The workflow violates the key invariant that an event may mutate only the session that produced it.

**Remediation:** Give every connection a monotonically increasing session ID or use a per-client signal proxy. Include the client/ID in every signal and reject it unless it equals the currently attached client/ID. Invalidate the generation before stopping the old client. Make `_on_resize`, `_on_disconnect`, `_on_remote_clipboard`, and damage handling identity-aware. Carry an immutable framebuffer snapshot/reference with resize events rather than reading `self.client.framebuffer` later. Add deterministic tests that queue each old-session event, reconnect, then drain the event loop.

### 2. CopyRect validates only the source; an out-of-range destination can resize the bytearray under a live QImage

**Severity:** High  
**CWE:** CWE-787 (Out-of-bounds Write), CWE-416 (Use After Free)  
**Locations:**

* `src/openvncviewer/rfb.py:452-467` — `_copy_rect()` checks `src_x/src_y` but never validates destination `x/y/w/h`; slice assignment occurs at line 467.
* `src/openvncviewer/rfb.py:436-440` — the RAW path explicitly documents that out-of-bounds slice assignment can grow/reallocate the bytearray under QImage, and correctly prevents it there.
* `src/openvncviewer/ui.py:166-169` — QImage wraps the mutable framebuffer without copying.

**Preconditions:** A connected malicious or compromised VNC server sends a CopyRect whose source is valid but destination lies outside the negotiated framebuffer.

**Attack steps:** Send a 1x1 CopyRect from source `(0,0)` with destination `x=65535` against a 2x2 framebuffer. Source validation passes. Python assigns beyond the destination and grows the framebuffer. A live QImage still references the original buffer storage.

**Evidence/reproduction:** Safe direct protocol-unit repro:

```text
copyrect_before (..., 16) after (..., 20) tail b'\x00\x01\x02\x03'
copyrect_with_live_qimage grew_to 20 image_null False
```

The second run constructed a live `QImage` first; the bytearray still resized and QImage remained non-null. Native exploitation beyond stale-pointer creation was not attempted.

**Business impact:** A server can corrupt framebuffer state or reliably crash the viewer. Because QImage/QPainter are native code and retain a pointer to the pre-resize storage, use-after-free style reads are plausible; code execution was not established and should be treated as unconfirmed.

**Remediation:** Before reading/copying, apply the same destination check used by `_blit`: reject if `x + w > width` or `y + h > height` (and reject zero/invalid rectangles according to protocol policy). Validate source and destination before allocating the row snapshot. Prefer a fixed-size framebuffer abstraction that cannot resize, and test all four destination edges with a live QImage.

### 3. Server-controlled DesktopSize has no pixel/memory budget and can be repeated in one update

**Severity:** High (availability)  
**CWE:** CWE-770 (Allocation of Resources Without Limits or Throttling), CWE-400 (Uncontrolled Resource Consumption)  
**Locations:**

* `src/openvncviewer/rfb.py:364-368` — `_resize()` allocates `width * height * 4` with no maximum or zero-size validation.
* `src/openvncviewer/rfb.py:397-421` — up to 65,535 rectangles are accepted per update; every DesktopSize immediately reallocates, and only after the whole batch is a fresh full update requested.
* `src/openvncviewer/rfb.py:239-245` — initial server dimensions use the same unlimited allocation.

**Preconditions:** The user connects to a malicious/compromised server. RFB dimensions are attacker-controlled unsigned 16-bit values.

**Attack steps:** The server announces a very large initial size or DesktopSize (for example 10,000x10,000 requests about 381 MiB; the protocol maximum requests about 16 GiB), or sends many alternating DesktopSize rectangles in a single update. The client repeatedly allocates/zeros buffers before returning to the message loop. Zero-sized updates can also drive a nonproductive full-update cycle if the server keeps replying with DesktopSize.

**Evidence/reproduction:** Under a safe 256 MiB address-space cap, direct `_resize(10000, 10000)` produced:

```text
desktop_size_memoryerror_under_256MiB_limit
```

`MemoryError` will normally terminate that session through `_run()`, but without an OS resource cap the process may be heavily paged or killed before it can recover. Repeated resize allocation is directly visible in the per-rectangle loop.

**Business impact:** Remote denial of service, UI freeze, memory pressure affecting other applications, and a forced disconnect/reconnect loop whenever the user retries the same server.

**Remediation:** Define a maximum width, height, pixel count, and framebuffer byte budget before allocation (with checked multiplication). Reject zero dimensions. Limit DesktopSize occurrences per update (normally at most one, with clear ordering rules), coalesce to the final validated size, and rate-limit repeated resize/full-update cycles. Consider catching `MemoryError` at the allocation boundary to produce a controlled protocol error.

### 4. Clipboard sharing opt-out is not retained, and every newly enabled connection proactively sends existing clipboard text

**Severity:** Medium  
**CWE:** CWE-200 (Exposure of Sensitive Information to an Unauthorized Actor)  
**Locations:**

* `src/openvncviewer/ui.py:383-385` and `428-429` — each dialog defaults clipboard sharing to checked.
* `src/openvncviewer/ui.py:639` — remembered connection defaults contain only host, port, username, and wheel speed.
* `src/openvncviewer/ui.py:691-693` — the next dialog is reconstructed from that incomplete tuple.
* `src/openvncviewer/ui.py:702-714` — `connect_to()` also defaults sharing to true, but does not store the choice in `last_connection`.
* `src/openvncviewer/ui.py:748-753` and `770-781` — the first resize automatically offers whatever text is currently on the local clipboard.

**Preconditions:** A user previously unticks “Share clipboard with this server,” later reconnects (even in the same process), has sensitive text on the clipboard, and does not notice that the option reset to checked.

**Attack/user journey:** The user disables sharing for an untrusted server, disconnects, copies a password/token, and reconnects through the normal dialog. The checkbox silently returns to its global default. As soon as the first resize marks the session up, current clipboard text is sent without a new copy action.

**Evidence:** This follows deterministically from the four-element `last_connection` assignment at line 714 versus the `share_clipboard=True` positional default at line 384 and initial synchronization at lines 748-753. Existing `test_clipboard.py` confirms pre-connect clipboard contents are intentionally sent once a session comes up, but no test preserves an opt-out across reconnect.

**Business impact:** A privacy decision made for a server is lost, enabling unintended disclosure of passwords, tokens, or copied business data. The tooltip warns that enabled sharing sends clipboard data, but it does not cure the state-reset problem.

**Remediation:** Persist clipboard policy per history entry/server, default new/untrusted servers to off, and preserve the last explicit choice. Make initial synchronization separately consented or send only clipboard changes occurring after connection. Visibly indicate when existing clipboard content is about to be shared.

### 5. Concurrent viewer instances can undo history/password deletion through stale whole-file writes

**Severity:** Medium  
**CWE:** CWE-367 (TOCTOU Race Condition)  
**Locations:**

* `src/openvncviewer/history.py:40-58` — each instance loads a private snapshot once.
* `src/openvncviewer/history.py:87-100` — `save()` atomically replaces the entire file but performs no inter-process locking or merge.
* `src/openvncviewer/history.py:126-140` — remember modifies the stale in-memory list and saves it wholesale.
* `src/openvncviewer/history.py:142-150` — remove does the same.

**Preconditions:** Two viewer instances run under the same OS account/config path. Both loaded an entry containing a saved encrypted password token.

**Attack/user journey:** Instance A removes the server and token. Instance B, opened earlier, remembers another server. B writes its stale complete list and resurrects A's deleted server and token.

**Evidence/reproduction:** Two `ServerHistory` objects sharing a temporary file produced:

```text
after_delete []
after_stale_save ['other-host', 'secret-host'] resurrected_token TOKEN
```

**Business impact:** “Remove Server” and “untick Save password” are not durable deletion operations in a normal multi-window workflow. A credential token users believe deleted can reappear on disk and in the UI.

**Remediation:** Serialize read-modify-write with an inter-process file lock; while holding it, reload current disk state, apply one operation, fsync the temporary file/directory as appropriate, and replace. Alternatively use a transactional store such as SQLite. Add two-instance tests for remove-vs-remember and unsave-vs-remember.

## Confirmed behavior with server-dependent impact / hypotheses

### Input release on disconnect

`RemoteView.detach()` clears `_pressed` and `_buttons` without forwarding releases (`src/openvncviewer/ui.py:150-159`), although `release_all_keys()` exists at `371-375` and is used for fullscreen transitions at `647-649`. `MainWindow.disconnect()` stops the socket before detaching (`733-740`). Thus a held key/button is not explicitly released before an intentional disconnect. Many VNC servers clear input state when a client socket closes, so persistent remote “stuck key/button” impact was **not confirmed**. Harden by best-effort release of keys and pointer buttons while the client is still ready, followed by stop, and retain server-side disconnect cleanup as defense in depth.

### General UI/network framebuffer data race

The network thread writes framebuffer slices (`src/openvncviewer/rfb.py:448-450`, `465-467`) while the UI thread's QImage/QPainter reads the same zero-copy storage (`src/openvncviewer/ui.py:166-169`, `222-224`). `_send_lock` at `rfb.py:163,210-213` protects socket sends only; no lock, immutable frame handoff, or double buffering protects pixels. Tearing is structurally possible, but a deterministic crash from ordinary in-bounds blits was **not confirmed**. The confirmed stale-resize and CopyRect issues make lifetime hazards concrete. Prefer front/back buffers swapped on the UI thread, immutable damage snapshots, or a narrowly scoped framebuffer lock with no painting while resize/reallocation is possible.

## Negative results and existing safeguards

* Clipboard server lengths are capped: over 16 MiB terminates the session and over 1 MiB is dropped; local clipboard sends are capped at 1 MiB (`src/openvncviewer/rfb.py:43-47`, `376-393`, `691-704`).
* Clipboard echo suppression prevented simple server→local→server loops in the existing tests, and sends before `_ready` are rejected/retried rather than spliced into the handshake (`rfb.py:243-245`, `691-704`; `ui.py:770-781`).
* Disabling clipboard sharing blocks both current-session send and receive paths (`ui.py:763-776`). The defect is loss of that choice on a later dialog, not bypass while false.
* Future callbacks are silenced before socket close (`rfb.py:176-185`), and the existing stopped-client loopback test passes. The unresolved defect is callbacks already in Qt's queue.
* RAW/ZRLE destination writes use `_blit()`'s framebuffer bounds check (`rfb.py:427-450`), and ZRLE expansion has a ceiling. The missing equivalent check is specific to CopyRect destination handling.
* Input is not forwarded until the handshake sets `_ready`, pointer movement is coalesced, wheel amplification is capped, and fullscreen explicitly releases held keys. Existing handshake/input and pointer tests passed.
* Disconnect clears the displayed frame/title/status (`ui.py:150-161`, `733-740`), avoiding stale-screen presentation in the ordinary non-racing path.
* History writes use write-then-rename, so a single-process crash is unlikely to leave partial JSON (`history.py:87-103`). Atomic replacement does not prevent stale-writer lost updates.
* Saved passwords are represented as encrypted tokens and an explicit unsave/remove removes them in a single instance. Encryption backend tests were skipped on Linux, so Windows DPAPI behavior was not independently exercised in this audit.

## Limitations

* Testing was Linux/offscreen and loopback/unit-level; Windows clipboard timing, DPAPI, and a real macOS Screen Sharing server were not available.
* Native-code exploitation of stale QImage pointers was not attempted. Buffer growth and mismatched QImage backing were confirmed; code execution is only a hypothesis.
* Resource-exhaustion testing used an OS memory cap and did not risk allocating protocol-maximum (~16 GiB) buffers.
* No tracked source was changed and no fixes were applied.
