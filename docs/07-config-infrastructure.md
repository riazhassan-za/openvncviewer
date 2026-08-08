# Configuration and infrastructure security audit

**Repository:** the repository at the commit above  
**Commit:** `4dbba5643733d31b51f76aef9a6fe67e53af09b8`  
**Method:** static, offline review of the checked-out tree and all 44 commits reachable from local refs. No tracked files were changed and no network services or live GitHub settings were queried.

## Summary

| Severity | Count |
|---|---:|
| High | 0 |
| Medium | 3 |
| Low | 1 |

The main risks are in release supply-chain trust: mutable action references execute in a write-capable release job, any pushed `v*` tag is treated as a release authorization without an in-repository tag ruleset, and release dependency resolution is not actually locked despite documentation claiming reproducibility.

## Findings

### CFG-01 — Privileged release job executes actions referenced by mutable tags

**Severity:** Medium  
**CWE:** CWE-829 — Inclusion of Functionality from Untrusted Control Sphere  
**Configuration surface:** `.github/workflows/ci.yml`

**Evidence**

- `.github/workflows/ci.yml:118-119` grants `contents: write` to the release job.
- `.github/workflows/ci.yml:122`, `:124`, and `:185` invoke `actions/checkout@v4`, `actions/download-artifact@v4`, and third-party `softprops/action-gh-release@v2` by mutable major-version tags rather than immutable commit SHAs.
- The same mutable-reference pattern appears in less-privileged jobs at `.github/workflows/ci.yml:25`, `:27`, `:66`, `:68`, and `:106`.

**Why unsafe**

A tag can be moved or its upstream repository/account compromised. In the release job, changed action code would run with a token able to modify repository contents and releases. The third-party publishing action is the most consequential instance because it directly receives the write-capable job context.

**Likely impact**

A compromised action reference could replace release assets, alter a release, or otherwise abuse the repository token, turning an upstream action compromise into distribution of a malicious executable.

**Minimum effective remediation**

Pin every `uses:` entry to a reviewed full commit SHA, prioritizing `softprops/action-gh-release` and all actions in the release job. Keep the existing job-scoped `contents: write` permission and use a dependency-update mechanism to propose reviewed SHA bumps.

### CFG-02 — A pushed `v*` tag is sufficient to authorize a public release; no tag protection is represented

**Severity:** Medium  
**CWE:** CWE-284 — Improper Access Control  
**Configuration surface:** GitHub workflow triggers, repository rulesets, and release tags

**Evidence**

- `.github/workflows/ci.yml:4-6` runs CI for every pushed tag matching `v*`.
- `.github/workflows/ci.yml:115-119` converts that event into a release job with `contents: write` after the build succeeds; there is no environment approval or actor/ref provenance check.
- `.github/branch-protection.json:3-8` targets only the default **branch**, not tags. No separate tag-ruleset file exists under `.github/`.
- Offline inspection found all 12 local release tags (`v0.1.0` through `v0.9.1`) are annotated but unsigned.

**Why unsafe**

Branch review and status-check rules do not constrain tag creation. Any account or automation identity with permission to create a matching tag can select an arbitrary commit and cause its code to be built and published as a release. Unsigned tags provide no independent cryptographic indication that the maintainer authorized the selected commit.

**Likely impact**

A compromised or mistakenly over-privileged write identity could bypass the `main` workflow and publish a trojaned Windows executable under a plausible version tag. This becomes more material if another maintainer or release bot is added.

**Minimum effective remediation**

Create and enforce a GitHub tag ruleset for `refs/tags/v*` that restricts creation/update/deletion to the release authority and blocks tag updates. Require signed tags if the maintainer can support a signing workflow. For stronger separation, put the release job behind a protected GitHub Environment with required approval.

**Verification caveat:** this audit was intentionally offline. A live tag ruleset may exist but is not represented in this commit; verify it in repository settings/API before treating this as confirmed live exposure.

### CFG-03 — Release dependencies are only partially pinned and are resolved from the network at build time

**Severity:** Medium  
**CWE:** CWE-829 — Inclusion of Functionality from Untrusted Control Sphere  
**Configuration surface:** Python build metadata and release workflow

**Evidence**

- `requirements.txt:1` claims dependencies are pinned “for reproducible builds,” but `requirements.txt:3-4` pins only two direct packages and supplies no hashes or transitive lock.
- `requirements-dev.txt:1-2` adds one direct exact pin, again without hashes or a complete transitive lock.
- `pyproject.toml:2` permits any `setuptools>=68` in the isolated build environment.
- `.github/workflows/ci.yml:74-77` upgrades to the latest available pip, installs the requirements, and performs an editable install. The test job similarly resolves loose project dependencies at `.github/workflows/ci.yml:32-35`.
- `.github/workflows/ci.yml:63-64` and `:70` also float the runner image and Python 3.13 patch release.

**Why unsafe**

Exact top-level versions do not fix their transitive dependencies, build backend, installer, runner, or interpreter patch. Re-running the same tag can therefore consume different code. A compromised or newly malicious transitive release can enter the executable without any repository change, and the “reproducible” label overstates what is controlled.

**Likely impact**

Release contents can drift between builds of the same source commit, weakening incident reconstruction and checksum provenance. A package-index or dependency compromise could be bundled into the distributed one-file executable.

**Minimum effective remediation**

Generate a complete Windows/Python-3.13 build lock with hashes (including build requirements), install it with `pip --require-hashes`, and pin the pip/setuptools toolchain used by the release build. Keep the looser `pyproject.toml` ranges for ordinary source consumers if desired; use the lock specifically for CI release production.

### CFG-04 — Repository security/release documentation contradicts the effective configuration

**Severity:** Low  
**CWE:** CWE-16 — Configuration  
**Configuration surface:** CODEOWNERS comments, workflow comments, and release-process documentation

**Evidence**

- `.github/CODEOWNERS:1-2` says every file needs maintainer review and that `require_code_owner_review` enforces it.
- `.github/branch-protection.json:24-25` explicitly sets zero required approvals and `require_code_owner_review: false`. `TODO.md:415-432` confirms this is intentional for the current single-maintainer model.
- `.github/workflows/ci.yml:10` says the **build** job elevates permissions, while the actual elevation is on the **release** job at `.github/workflows/ci.yml:112-119`.
- `TODO.md:458-460` says release assets have no checksum file, while `.github/workflows/ci.yml:129-132` creates `SHA256SUMS.txt` and `.github/workflows/ci.yml:185-190` publishes it.

**Why unsafe**

The no-approval policy itself is documented as intentional and is not duplicated here as a finding. The problem is false assurance in the adjacent source-of-truth files: maintainers and auditors can incorrectly conclude code-owner review is enforced or misunderstand which job has write authority. Stale release documentation can also lead to unnecessary or incorrect operational changes.

**Likely impact**

Review-policy drift may go unnoticed when a second maintainer is added, and incident responders may scope token exposure to the wrong job. These are control-maintenance failures rather than a direct exploit.

**Minimum effective remediation**

Update `.github/CODEOWNERS:1-2` to state that ownership is defined but currently not required, correct `.github/workflows/ci.yml:10` to name the release job, and reconcile `TODO.md:458-460` with the checksum-producing workflow. Retain the existing explicit TODO trigger to enable reviews when a second writer is added.

## Negative results and validated controls

- **Secrets/history:** Pattern-scanned all text files in all 44 commits reachable from local refs for common private-key headers, AWS/GitHub/Google/Slack token forms, and credential assignments. No private keys or provider-token patterns were found. Credential-assignment hits were clearly synthetic test fixtures such as `testuser`, `secret12`, `hunter2`, and fake `AQAA...` tokens. `git fsck --full --no-reflogs --unreachable` reported no unreachable objects to inspect, and historical path enumeration found no deleted secret/config file classes.
- **Credential storage:** `src/openvncviewer/secretstore.py:56-72` loads DPAPI only on Windows; `:106-119` returns no token rather than storing plaintext when protection is unavailable. `SECURITY.md:80-85` accurately limits DPAPI's threat model. The option is documented as off by default (`README.md:184-187`). Windows DPAPI could not be exercised on this Linux audit host; the offline fallback tests passed.
- **Config/temp-file handling:** `src/openvncviewer/history.py:91-102` creates a same-directory file with `tempfile.mkstemp`, writes through the already-open descriptor, atomically replaces the target, and removes the scratch file on handled failures. All 18 history tests passed. A local POSIX probe produced mode `0600` and no leftover `*.tmp`; this does not independently verify Windows ACL inheritance. The fallback config path at `src/openvncviewer/ui.py:47-50` was noted, but on the supported Windows/Qt target `AppConfigLocation` should resolve to the user config directory, so no unsupported-path issue was raised.
- **CI permissions/triggers:** `.github/workflows/ci.yml:10-12` defaults the token to `contents: read`; only the release job elevates it. There is no `pull_request_target` or `workflow_run` trigger, no secret use, and pull-request code runs with the read-only default. Test/build jobs have explicit timeouts.
- **Branch protection:** The committed ruleset blocks default-branch deletion and non-fast-forward updates and requires strict status checks (`.github/branch-protection.json:18-44`). Zero approvals and the admin bypass are explicitly accepted and accurately discussed in `TODO.md:415-438`; they were not repeated as vulnerabilities. Live enforcement, bypass actors, repository collaborators, private vulnerability reporting, and other GitHub-hosted settings could not be confirmed offline.
- **Debug/logging/admin defaults:** PyInstaller debugging is disabled (`packaging/openvncviewer.spec:58`), the GUI has no console (`:63`), and no application logging, debug listener, admin endpoint, telemetry, or dangerous environment feature flag was found. CI's `PYTHONUNBUFFERED`/faulthandler use is confined to test diagnostics and no secrets are supplied to that job.
- **Packaging/platform flags:** UPX is disabled (`packaging/openvncviewer.spec:61`), reducing packer-related ambiguity. The one-file temporary extraction behavior (`:62`) and unsigned executable (`:67`, `SECURITY.md:86-87`) are expressly documented accepted limitations and were not duplicated. No built PE artifact was present, so DEP/ASLR/CFG/authenticode properties could not be inspected offline.
- **Release checks:** The workflow verifies the PE architecture (`.github/workflows/ci.yml:86-104`) and publishes a SHA-256 checksum file (`:129-132`, `:185-190`). Checksums provide integrity comparison but, being released through the same trust path as the binary, do not replace tag/action provenance controls.
- **Security documentation:** `SECURITY.md:20-30` provides a private-reporting route plus a detail-free fallback; `:55-87` prominently lists known intentional protocol, memory, DPAPI, and signing weaknesses. Those known accepted items were not restated as findings.
- **Workspace integrity:** `git status --short` was empty after checks. No tracked source was modified. No package installation, network access, or live GitHub API query was performed.

## Audit limitations

This was an offline repository audit. It cannot establish live GitHub branch/tag rules, collaborator roles, Actions settings, environment protections, private vulnerability reporting, release-asset state, or organization policy. Dependency vulnerability/advisory checks were not run because no local scanner/advisory database was available and network access was intentionally avoided. Windows-only DPAPI and generated PE security flags require validation on a Windows build artifact.
