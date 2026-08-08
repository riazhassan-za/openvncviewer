# Dependency and software-supply-chain audit

**Target:** `4dbba5643733d31b51f76aef9a6fe67e53af09b8`  
**Tree:** `85fcfc05f17605f5b32a4c1dc92ad80c155498ce`  
**Repository:** `/home/riaanroos/SoftwareDevelopment/riaz_stuff/openvncviewer/security-evaluation-main`  
**Method:** static/offline review of the exact checked-out commit and local Git objects. No tracked source was changed, and no network-derived assertions are made.

## Executive summary

Five confirmed supply-chain weaknesses were found: mutable GitHub Action references in the release path, a tag-to-release trust gap, incomplete Python locking/integrity controls, unauthenticated release artifacts, and branch controls that do not require review and are bypassable by admins. The first two provide the clearest realistic release-compromise paths. Release token permissions are otherwise sensibly scoped, direct runtime/build versions are pinned in `requirements*.txt`, UPX is disabled, and checksums are generated for released executables.

No conclusion about known vulnerabilities in the selected package versions can be made offline: no local vulnerability scanner or advisory database was available. This is **not** a clean vulnerability-scan result.

## Confirmed findings

### SC-01 — Mutable Actions references can execute with release-write authority

- **Severity:** High
- **CWE:** CWE-829 (Inclusion of Functionality from Untrusted Control Sphere), CWE-494 (Download of Code Without Integrity Check)
- **Surface:** GitHub Actions / release publication
- **Location:** `.github/workflows/ci.yml:25`, `:27`, `:66`, `:68`, `:106`, `:122`, `:124`, `:185`; release permission at `:112-120`
- **Evidence:** Every action is selected by a movable major tag (`@v4`, `@v5`, or `@v2`) rather than a full commit SHA. In particular, `softprops/action-gh-release@v2` runs in the `release` job, where `contents: write` is granted. `actions/checkout`, `setup-python`, `upload-artifact`, and `download-artifact` are also tag-pinned rather than SHA-pinned.
- **Compromise mechanism:** A compromised action publisher account/repository, or malicious movement of a referenced major tag, changes code executed by this workflow without a repository commit. Compromise of the third-party release action is especially consequential because it receives the write-capable `GITHUB_TOKEN` and the release files.
- **Blast radius / impact:** Ability to replace or add release assets, alter releases, or otherwise use the repository-scoped token. Compromise of build actions can also contaminate the executable before upload. Every future CI/release run is exposed.
- **Remediation:** Pin every `uses:` reference to a reviewed 40-character commit SHA and retain the human-readable version in a comment. Prioritize `softprops/action-gh-release`. Enable Dependabot/Renovate for controlled Action SHA updates, and review diffs before accepting them. Keep the current job-level `contents: write` restriction.

### SC-02 — Any `v*` tag can select and publish code outside the protected branch

- **Severity:** High
- **CWE:** CWE-284 (Improper Access Control)
- **Surface:** release/tag trust boundary
- **Location:** `.github/workflows/ci.yml:3-8`, `:112-122`, `:185-190`; `.github/branch-protection.json:5-10`; `.github/branch-protection.json:18-45`
- **Evidence:** A push of any tag matching `v*` triggers the workflow, and the only release gate is `startsWith(github.ref, 'refs/tags/v')`. The release job checks out that tag and receives `contents: write`. The committed ruleset applies only to `~DEFAULT_BRANCH`; no tag ruleset, required signed tag, protected release environment, or assertion that the tagged commit is contained in `main` appears in the repository. The twelve local annotated tags are unsigned; all currently resolve to commits contained in `main`, which is good historical evidence but not an enforcement control.
- **Compromise mechanism:** Any actor able to push a matching tag can tag a commit that never passed the protected-main path. Because a tag workflow uses the workflow/content at the tagged revision, such an actor can publish unreviewed or malicious code and potentially alter the privileged release job itself. The five test jobs do not establish that the revision was reviewed or belongs to `main`.
- **Blast radius / impact:** A malicious official GitHub Release and executable under the project's identity; downstream users may execute it and expose Windows account/VNC credentials and remote-session data.
- **Remediation:** Add a repository ruleset for `refs/tags/v*` restricting creation/update/deletion to a small release role; prohibit tag movement; require signed annotated tags and verify them in policy. Before granting release authority, verify the tagged commit is an ancestor of the protected release branch and that the tag/version matches project metadata. Prefer a protected GitHub Environment with required approval for publication. Consider splitting build and publication so a reviewed workflow publishes an immutable artifact by digest.

### SC-03 — Python “pins” are not a complete lock and downloads are not hash-verified

- **Severity:** Medium
- **CWE:** CWE-494 (Download of Code Without Integrity Check), CWE-829 (Inclusion of Functionality from Untrusted Control Sphere)
- **Surface:** pip, PEP 517 build isolation, PyInstaller build dependencies/hooks
- **Location:** `requirements.txt:1-4`; `requirements-dev.txt:1-2`; `pyproject.toml:1-3`, `:31-38`; `.github/workflows/ci.yml:32-35`, `:73-80`
- **Evidence:** Only the three direct requirements are exact-pinned. There are no transitive lock entries and no `--hash=sha256:...` values. `setuptools>=68` is resolved in an isolated build environment; project metadata and the optional build extra retain open-ended ranges. CI upgrades pip to whatever is current and the test job installs only from the loose `pyproject.toml` ranges. The release build installs `requirements-dev.txt`, then `pip install -e .`; exact direct pins happen to constrain the named dependencies, but transitive packages/build tooling and their artifacts remain resolver/index-selected.
- **Compromise mechanism:** A compromised dependency release/index path, newly selected transitive release, or changed wheel can execute during installation/build or be frozen into the EXE. This is particularly sensitive for PyInstaller and its hook ecosystem, whose Python code runs while analyzing and packaging the application. Exact names reduce typosquatting risk, but they do not authenticate package artifacts.
- **Blast radius / impact:** Compromise of CI and every produced executable. The frozen application handles passwords and remote desktop input/output, so a malicious dependency can steal high-value user data.
- **Remediation:** Generate a Windows/Python-version-specific, fully transitive lock with hashes (for example, `pip-compile --generate-hashes`), review it, and install with `python -m pip install --require-hashes -r ...`. Pin the build backend exactly and ensure it is installed from the hashed lock; use `--no-build-isolation` only after constructing that controlled environment. Prefer wheels with `--only-binary=:all:` where feasible. Install the local project with `--no-deps` after locked dependencies. Make tests consume a deliberate locked test set while separately testing minimum/maximum supported ranges in non-release CI.

### SC-04 — Executable and checksum have no independent authenticity proof

- **Severity:** Medium
- **CWE:** CWE-347 (Improper Verification of Cryptographic Signature)
- **Surface:** Windows executable, checksum, release assets
- **Location:** `.github/workflows/ci.yml:129-132`, `:168-176`, `:185-190`; `packaging/openvncviewer.spec:51-68`; `SECURITY.md:86-87`
- **Evidence:** CI publishes a SHA-256 checksum generated in the same workflow and channel as the executable. The project explicitly states the executable is unsigned. The PyInstaller spec has no signing operation, and the workflow contains no Authenticode, Sigstore, or provenance attestation step. A checksum detects accidental corruption only when the checksum itself is obtained through an independently trusted/authenticated path; an attacker controlling the release can replace both files.
- **Compromise mechanism:** Compromise of the release workflow/account/channel permits coordinated replacement of the EXE and `SHA256SUMS.txt`. Users have no cryptographic identity binding with which to reject the replacement.
- **Blast radius / impact:** All users of a compromised release can be induced to run attacker code; SmartScreen reputation is also weaker for unsigned binaries.
- **Remediation:** Authenticode-sign the final EXE with a protected key (preferably HSM/keyless service with approval controls), verify the signature after signing, and publish a signed checksum/manifest. Add GitHub artifact provenance (or Sigstore attestation) bound to the commit, workflow, and artifact digest, and document verification. Keep SHA-256 publication as a useful corruption check, but do not present it as publisher authentication.

### SC-05 — Committed main-branch policy has no mandatory reviewer and an always-admin bypass

- **Severity:** Medium
- **CWE:** CWE-284 (Improper Access Control)
- **Surface:** source/release branch governance
- **Location:** `.github/branch-protection.json:11-16`, `:22-29`, `:33-42`; `.github/CODEOWNERS:1-3`; `TODO.md:400-438`
- **Evidence:** The source-of-truth ruleset sets `required_approving_review_count` to `0`, disables code-owner and last-push approval, and grants the admin repository role an always bypass. This directly contradicts the CODEOWNERS comment claiming code-owner review is enforced. Required checks cover only test matrix jobs; the PyInstaller `build` job is not a required branch check. `TODO.md` confirms that the ruleset is claimed to be live, that review removal was deliberate, and that the owner bypass makes the rules advisory for the owner. The live GitHub setting could not be independently queried offline.
- **Compromise mechanism:** A compromised admin credential can bypass all main controls. If another write-capable maintainer is added without updating the policy, changes can merge with no independent approval. A test-only status policy can accept changes that later fail or unexpectedly affect packaging.
- **Blast radius / impact:** Repository-wide source/workflow compromise and, together with tag release automation, malicious official releases.
- **Remediation:** Remove or tightly scope always-bypass; require at least one independent code-owner approval once administratively possible; require approval of the last push; protect `.github/workflows/**`, dependency locks, and `packaging/**` with explicit ownership. Add a required packaging/build validation status (at minimum a smoke build) before release. Correct the stale CODEOWNERS comment. Enforce phishing-resistant MFA/passkeys for maintainers.

## Recommendations / defense-in-depth gaps

These are missing assurances, not evidence that an exploit or vulnerable package is present.

1. **SBOM:** No CycloneDX/SPDX artifact is generated or published. Produce an SBOM from the actually resolved Windows build environment and, ideally, inspect the frozen EXE contents. Bind the SBOM digest into provenance and release it beside the binary.
2. **Provenance:** Add a build attestation that identifies commit, workflow identity, runner, resolved dependencies, and artifact digest. Grant `id-token: write` only to the dedicated attestation step/job, not globally.
3. **Reproducibility:** `.github/workflows/ci.yml:16`, `:63`, and `:117` use mutable runner images; `:70` selects a moving Python 3.13 patch; `:34` and `:75` install a moving pip; `pyproject.toml:2` selects moving setuptools; action tags and unhashed wheels are also moving inputs. Record exact runner image/OS, Python and tool versions, lock/hash every downloaded artifact, set deterministic build metadata where supported, and compare two clean-build digests. Current configuration does not establish bit-for-bit reproducibility.
4. **Vulnerability monitoring:** Add an authenticated/offline-capable vulnerability scan of the resolved lock/SBOM and a dependency update bot. Treat scan results as release gates according to documented severity/exploitability policy, with reviewed exceptions and expiry dates.
5. **PyInstaller hardening:** The spec usefully disables UPX (`packaging/openvncviewer.spec:61`) and excludes unused Qt modules (`:15-31`). Also archive the PyInstaller warning/cross-reference outputs, inventory bundled DLLs, scan the final PE, and verify the frozen application starts in a clean Windows VM. One-file packaging should not be treated as a security boundary.
6. **Release immutability:** Prevent release asset overwrite/tag update after publication where platform controls permit it. Publish expected artifact digests in signed provenance and retain CI artifacts/logs according to an explicit retention policy.

## Checks performed

- Verified checkout HEAD equals the requested commit and recorded commit/tree/parent IDs.
- Enumerated dependency/build manifests: only `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, and `packaging/openvncviewer.spec`; no lockfile, hash lock, Dependabot/Renovate config, SBOM, signing, or provenance config was present.
- Reviewed all workflow triggers, permissions, action references, dependency installation, artifact transfer, checksum generation, and release publication steps.
- Reviewed PyInstaller entry/spec: local entry point, no extra binaries/data/hooks/runtime hooks, broad unused-Qt exclusions, `upx=False`, and no signing stage.
- Reviewed committed branch ruleset, CODEOWNERS, security policy, and repository-process notes.
- Inspected all 12 local annotated tags: all are unsigned; all currently resolve to commits contained in `main`. Inspected recent commit signature status: target and ordinary commits are unsigned; several GitHub merge commits report signature status `E` (signature present but unverifiable in the local key/trust context), so no verified commit-signing assurance was established.
- Searched the target tree for common high-confidence token/private-key/password-assignment patterns outside tests; no match was found. Neither `gitleaks` nor `trufflehog` was locally available, so this was not a full historical secret scan.
- Checked for local scanners (`pip-audit`, `osv-scanner`, `syft`, `grype`, `trivy`, `cyclonedx-py`, `bandit`): none was installed. A search for a usable local advisory database did not complete within the bounded offline check. Therefore no vulnerability IDs are asserted and no “zero vulnerabilities” claim is made.
- Ran `git fsck --full --no-dangling` and `git diff-tree --check` for the audited commit; both completed without findings.
- No build/reproducibility comparison was run: the release target is Windows, the available host is not the declared Windows build environment, required wheels were not cached locally, and network access was intentionally not used.

## Positive controls observed

- Workflow permissions default to `contents: read`, with `contents: write` limited to the release job (`.github/workflows/ci.yml:10-12`, `:112-120`).
- Release depends on successful tests/build and consumes run-scoped uploaded artifacts (`.github/workflows/ci.yml:45-46`, `:112-127`).
- Runtime direct dependencies and PyInstaller are exact-version-pinned for the release build (`requirements.txt:3-4`, `requirements-dev.txt:2`).
- The release publishes SHA-256 checksums and fails on missing artifacts (`.github/workflows/ci.yml:106-110`, `:129-132`).
- PyInstaller disables UPX and excludes substantial unused Qt attack surface (`packaging/openvncviewer.spec:15-31`, `:61`).
