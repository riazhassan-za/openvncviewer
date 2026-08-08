# Injection-style security review — `4dbba5643733d31b51f76aef9a6fe67e53af09b8`

## Executive summary

The commit changes only `.github/workflows/ci.yml` (55 additions, 8 deletions) to derive release notes from `CHANGELOG.md`. **No command, shell, workflow-expression, path, template, deserialization, or log-injection vulnerability was introduced by the commit.** The tag name reaches the shell through the runner-provided `GITHUB_REF_NAME` environment variable and remains quoted; it is not inserted into the script by `${{ ... }}`. The `awk` value is data (`-v v="$version"`), not generated awk source.

A contextual review of the Python/PySide6 VNC client found three **pre-existing snapshot issues**, none authored by this commit:

| ID | Finding | Severity | CWE | Introduced by commit? |
|---|---|---:|---|---|
| F1 | CopyRect destination is unchecked before mutating a buffer wrapped by `QImage` | **Medium** | CWE-787 / CWE-416 | No |
| F2 | Several RFB lengths/dimensions can force multi-gigabyte reads/allocations | **Medium** | CWE-400 | No |
| F3 | Remote server strings reach auto-rich-text Qt widgets | **Low** | CWE-451 | No |

Severity assumes the realistic boundary for a VNC client: the victim must connect to a malicious or compromised VNC server (or an active attacker must already be able to impersonate the unauthenticated/unencrypted RFB endpoint). No web-only findings were forced onto the desktop application.

## Scope and method

Reviewed the target tree and parent diff, concentrating on `.github/workflows/ci.yml`, production Python under `src/openvncviewer`, packaging entry points, tests, and configuration. Searches covered shell/process APIs, dynamic execution, path construction, serializers, Qt rich-text sinks, `ctypes`, protocol lengths, decompression, and Python-buffer-to-Qt boundaries. Tests were offline and did not contact GitHub or a VNC server. No tracked source was modified.

## Confirmed findings (pre-existing context)

### F1 — Unchecked CopyRect destination can reallocate a framebuffer still wrapped by `QImage`

**Severity:** Medium  
**CWE:** CWE-787 (Out-of-bounds Write), with a resulting CWE-416-style stale native reference risk  
**Commit attribution:** Pre-existing. `git blame` attributes the relevant lines to commits before `4dbba56`.

**Input source**

A remote RFB server controls rectangle `x`, `y`, `w`, `h`, and encoding in `src/openvncviewer/rfb.py:397-406`, and controls CopyRect source coordinates read at `src/openvncviewer/rfb.py:452-453`.

**Dangerous sink and missing control**

* `src/openvncviewer/rfb.py:454-457` validates only the **source** rectangle.
* `src/openvncviewer/rfb.py:465-467` calculates the attacker-controlled destination and assigns to a `bytearray` slice without checking that `x + w <= self.width` and `y + h <= self.height`.
* `src/openvncviewer/ui.py:163-169` wraps that same `bytearray` in a zero-copy native `QImage`.

Python slice assignment beyond the end grows the bytearray rather than rejecting the write. Growth may reallocate its backing storage while Qt retains the prior buffer address. The adjacent raw-blit path explicitly recognizes this hazard and validates destination bounds at `src/openvncviewer/rfb.py:436-450`; CopyRect lacks the equivalent check.

**Safe offline reproduction**

```bash
python - <<'PY'
from src.openvncviewer.rfb import RFBClient
c = object.__new__(RFBClient)
c.width = c.height = 2
c.framebuffer = bytearray(range(16))
c._read = lambda n: b'\x00\x00\x00\x00'  # CopyRect source (0,0)
print(len(c.framebuffer))
c._copy_rect(65535, 0, 1, 1)               # invalid destination
print(len(c.framebuffer))
PY
```

Observed output: `16` then `20`. At protocol level, send a CopyRect rectangle header with `(x=65535, y=0, w=1, h=1, encoding=1)` followed by source `(0,0)` against a 2x2 framebuffer.

**Impact**

A malicious server can mutate/reallocate the framebuffer outside its declared geometry. At minimum this can crash or corrupt rendering. Because Qt consumes a borrowed native pointer, stale-pointer dereference is plausible; code execution was **not** demonstrated, so the issue is not rated High.

**Remediation**

Before reading/copying rows, apply the same complete destination check used by `_blit`, and reject zero/invalid geometry as appropriate:

```python
if x + w > self.width or y + h > self.height:
    raise RFBError("CopyRect destination lies outside the framebuffer")
```

Keep the source check. Add a regression test asserting invalid source and destination rectangles raise `RFBError` and never change framebuffer length. Longer-term, avoid concurrent mutation of storage borrowed by `QImage`, or guarantee its address and lifetime through an explicitly owned immutable/native buffer strategy.

### F2 — Unbounded RFB lengths and desktop geometry permit memory-exhaustion denial of service

**Severity:** Medium  
**CWE:** CWE-400 (Uncontrolled Resource Consumption)  
**Commit attribution:** Pre-existing.

**Input source and sinks**

A remote server supplies all of the following:

* Desktop name length: `src/openvncviewer/rfb.py:234-237` reads an unbounded u32 via `_read(name_len)`.
* Authentication failure reason length: `src/openvncviewer/rfb.py:313-315` reads an unbounded u32.
* Initial and resized desktop dimensions: `src/openvncviewer/rfb.py:234`, `src/openvncviewer/rfb.py:364-368`, and `src/openvncviewer/rfb.py:410-412` allocate `width * height * 4` bytes and then pass the storage to Qt without a policy limit.
* RAW rectangle body: `src/openvncviewer/rfb.py:402-405` reads `w * h * 4` bytes before rectangle bounds are checked in `_blit`.
* ZRLE compressed body: `src/openvncviewer/rfb.py:407-409` reads an unbounded u32-sized compressed body before the decompression expansion ceiling is applied.

The clipboard parser demonstrates the missing pattern correctly: `src/openvncviewer/rfb.py:381-389` rejects announced lengths above 16 MiB before reading.

**Safe offline reproduction / proof**

No large allocation is needed. RFB dimensions are unsigned 16-bit, so accepted arithmetic reaches:

```text
65535 * 65535 * 4 = 17,179,344,900 bytes
```

The u32 name, failure-reason, and ZRLE lengths can each announce up to 4,294,967,295 bytes. Inspection of the cited lines confirms these values are handed directly to `_read`/`bytearray` without a preceding cap. A malicious server can therefore make the client block waiting for an enormous body and/or attempt memory allocation until the process or desktop session is terminated.

**Impact**

Reliable client denial of service and potentially system-wide memory pressure after connecting. This does not cross into command execution or deserialization-based code execution.

**Remediation**

Define centralized protocol limits and validate **before** reading or allocating: maximum desktop width/height and total framebuffer bytes; small caps for desktop names and failure reasons; maximum compressed rectangle size; and rectangle bounds before RAW body reads. Reject zero dimensions if unsupported. Prefer chunked/discarding reads where a body must be consumed. Catch `MemoryError` at the session boundary only as defense in depth, not as the primary control.

### F3 — Remote RFB strings can be interpreted as rich text in client chrome

**Severity:** Low  
**CWE:** CWE-451 (User Interface Misrepresentation of Critical Information)  
**Commit attribution:** Pre-existing.

**Input source**

* The server-controlled desktop name is decoded at `src/openvncviewer/rfb.py:236-237`.
* A server-controlled authentication failure reason is returned at `src/openvncviewer/rfb.py:313-315`, propagated as `str(exc)` by `src/openvncviewer/rfb.py:185-190`, and delivered to the UI.

**Dangerous sinks and missing control**

* The desktop name reaches the status `QLabel` at `src/openvncviewer/ui.py:755-761`.
* The failure reason reaches the same label and a `QMessageBox` at `src/openvncviewer/ui.py:783-793`.

`QLabel`/`QMessageBox` default to Qt `AutoText`; strings that look like HTML can therefore be rendered rather than displayed literally. There is no explicit `Qt.PlainText` setting or HTML escaping. A payload such as `<b>Trusted system message</b>` is sufficient to select rich-text rendering; richer supported markup can visually obscure or restyle status content.

**Reproduction**

Connect to a controlled test RFB server whose ServerInit desktop name is `<b>Trusted system message</b>`, or whose RFB 3.8 failure reason is that value. Observe formatted text in the status area/dialog rather than literal angle-bracket text. The local environment lacked PySide6, so the Qt visual probe could not be executed here; the dataflow and documented `AutoText` behavior are direct. This limitation does not affect F1's Python-only reproduction.

**Impact**

A malicious endpoint can spoof client-owned status/disconnect presentation. Practical severity is Low because the endpoint already controls the remote desktop pixels and the user chose to connect; no command execution or automatic external-link opening was established for these widgets.

**Remediation**

Force plain text for every widget that receives protocol, exception, host, or history text, e.g. `self.status.setTextFormat(Qt.PlainText)`. For message boxes, construct the instance and call `setTextFormat(Qt.PlainText)` before setting the reason. Keep intentionally rich About-dialog content separate and constant.

## Commit-specific workflow analysis

### No confirmed workflow command/expression injection

Exact flow:

* Tags trigger at `.github/workflows/ci.yml:3-8`; release execution is tag-gated at `:112-119`.
* The tag arrives as runner environment variable `GITHUB_REF_NAME` and is assigned with a quoted expansion at `:134-144`.
* `awk -v v="$version"` supplies one argv value; `v` is concatenated by the fixed awk program at `:145-149` and is never parsed as awk syntax.
* Output paths are fixed (`section.md`, `notes.md`) at `:149-164`, and the release action consumes the fixed path at `:185-190`.

Offline probes used accepted shell metacharacter classes in tag-like values:

```text
v1.2.3;echo PWNED
v1.2.3$(echo PWNED)
v1.2.3`echo PWNED`
```

All remained literal data in both awk and `echo`; no nested command ran. `git check-ref-format` confirmed those metacharacter-bearing ref forms can be syntactically valid, while newline and `::` examples were rejected. Quoting therefore matters and is effective here. The workflow's `${{ matrix.arch }}` and `${{ matrix.runner }}` uses at `.github/workflows/ci.yml:61-64,82-109` are sourced from a static matrix, not PR/tag/user data.

### Release-note/log content is not a vulnerability at this trust boundary

`CHANGELOG.md` content is copied to notes and printed at `.github/workflows/ci.yml:144-149,161-183`. That content is repository code checked out from the release tag, not an issue title, PR body, remote response, or other lower-trust event field. Pull requests do not execute the release job (`:116`). Someone able to place malicious bytes in the released commit and create/push its release tag already controls release source and can change the workflow itself. Treating intended Markdown rendering as template injection would therefore be a false positive.

**Hardening only:** protect `v*` tags so only release maintainers can create them; optionally require a conservative version format such as `^[0-9]+\.[0-9]+\.[0-9]+$`; use `printf` instead of `echo` for diagnostics; and disable workflow commands around `cat notes.md` if repository governance ever permits lower-trust changelog content into a privileged tag. These improve defense in depth but do not remediate a demonstrated exploit in this commit.

## Negative results

* **Shell/command execution and subprocesses:** no production `subprocess`, `os.system`, `QProcess`, `eval`, or `exec` sink was found. Test subprocesses use argv arrays without `shell=True`; `tests/test_hidpi.py` has a test-only `eval` over output from a fixed local probe, not remote/runtime application input.
* **Path traversal:** the new workflow reads/writes fixed paths. Application history uses `QStandardPaths` plus fixed `servers.json` (`src/openvncviewer/ui.py:47-51`); remote host/name/clipboard fields are JSON values and are not used as path components.
* **Format/template injection:** no dynamic Python format string or template engine receives untrusted input. Qt's protocol-string rich-text interpretation is reported separately as F3. The new awk program is fixed.
* **Workflow expression/script injection:** no untrusted `${{ github.* }}` value is embedded into a `run:` script. Matrix expressions are static. The tag is consumed through an environment variable and quoted.
* **Unsafe deserialization:** `src/openvncviewer/history.py:47-75` uses `json.loads`, checks container/value types, bounds ports, and caps entries. No pickle, marshal, YAML object construction, or Qt object deserialization was found.
* **Log spoofing:** tag refs cannot contain CR/LF, and tested metacharacters remain literal. Repository-controlled changelog output is not a lower-trust log boundary. F3 covers the actual remote-to-UI spoof path.
* **Parser/native boundaries:** DPAPI `ctypes` signatures are explicitly declared at `src/openvncviewer/secretstore.py:51-71`, backing buffers are retained during calls at `:93-116,128-140`, and DPAPI output is freed at `:99-104`. Clipboard length and ZRLE expansion/run-size controls are present at `src/openvncviewer/rfb.py:381-393,471-589`. Remaining confirmed gaps are F1 and F2.
* **Web findings:** none reported; this is a desktop VNC client and has no HTTP request/router/database/template surface in scope.

## Test notes

* F1's Python-only reproduction passed and grew the framebuffer from 16 to 20 bytes.
* Workflow shell/ref probes passed and showed no command execution.
* `git diff --check` passed.
* The full unit suite could not run in this checkout because `PySide6` and the installed `openvncviewer` package were absent; failures were import/environment errors, not product test failures. Dependencies were not installed to keep testing offline and non-invasive.
