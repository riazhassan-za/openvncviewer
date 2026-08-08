# Authentication and secret-storage audit — `4dbba5643733d31b51f76aef9a6fe67e53af09b8`

## Scope and result

Reviewed the commit snapshot in `security-evaluation-main`, including RFB 3.3/3.7/3.8 security negotiation, VNC and ARD authentication, DH checks, transport/server identity, credential handling, Windows DPAPI, recent-server persistence, configuration trust, tests, documentation, and the commit's release-workflow change. The commit itself changes only `.github/workflows/ci.yml`; the defects below are pre-existing in the audited snapshot, not introduced by that release-notes change.

**New findings:** 2 Medium, 1 Low. No tracked source was changed.

---

## Newly discovered defects

### AUTH-01 — Medium — Security negotiation silently downgrades entered ARD credentials to VNC or no authentication

**CWE:** CWE-757 (Selection of Less-Secure Algorithm During Negotiation)

**Locations:**

- `src/openvncviewer/rfb.py:277-294` (`_choose_security`)
- `src/openvncviewer/rfb.py:256-267` (unauthenticated offer/selection)
- `src/openvncviewer/rfb.py:301-311` (weak VNC challenge response)

**Actor / resource / forbidden action:** An active network attacker controlling or modifying the unauthenticated RFB handshake can make a user who supplied a macOS username and account password use legacy VNC authentication, exposing an offline-crackable response derived from the first eight password bytes. The attacker can alternatively offer only `SEC_NONE`, causing the client to accept an unauthenticated attacker-controlled desktop despite the user's explicit provision of credentials.

**Failed trust assumption:** The client treats the server's unauthenticated security-type list as authoritative and interprets “strongest offered” as “strong enough for the user's intent.” At `rfb.py:286-289`, VNC and then None remain acceptable even when the presence of a username shows that the user intended ARD account authentication. The fallback at lines 292-294 similarly speaks an offered authentication scheme even when the supplied credential shape does not match it. The security-type list has no integrity protection.

**Evidence / minimal reproduction:** A safe loopback test using the repository's `FakeVNCServer` offered only type 2 while the client was given both username and macOS-style password:

```text
username_supplied= True chosen= 2 vnc_response_valid_for_first8= True
```

Equivalent direct checks are:

```python
c.username, c.password = "mac-user", "AccountPassword"
assert c._choose_security([2]) == 2
assert c._choose_security([1]) == 1
```

The first path sends the DES challenge response at `rfb.py:311`; it is an offline verifier for the password's first eight UTF-8 bytes. The second path sends no credential at all and proceeds to attacker-controlled framebuffer/input handling.

**Impact:** Account-password exposure to offline cracking, silent loss of authentication, server impersonation, and subsequent capture of user input/clipboard. The accepted absence of server identity verification makes active interception broadly possible already, but this is a separate fail-open policy defect: the implementation discards a clear signal of the security mechanism the user intended and enables avoidable cross-scheme downgrade.

**Remediation:** Bind authentication policy to user intent, not merely the offered list. If a username is supplied, require ARD (or a future explicitly selected stronger Apple scheme) and refuse VNC/None. If only a password is supplied, require VNC and refuse None. Permit None only when no credentials were supplied or after an explicit, per-connection warning/choice. Prefer a UI security-mode selector and persist the expected mode per server. For RFB 3.3, where the server dictates one type, refuse a mismatch rather than silently complying. Ultimately protect negotiation with an authenticated transport.

---

### AUTH-02 — Medium — ARD accepts small-subgroup peer keys, allowing the credential AES key to be enumerated

**CWE:** CWE-327 (Use of a Broken or Risky Cryptographic Algorithm); CWE-20 (Improper Input Validation)

**Locations:**

- `src/openvncviewer/rfb.py:317-352` (`_auth_ard`)
- `src/openvncviewer/rfb.py:341-345` (insufficient shared-secret check)
- `src/openvncviewer/rfb.py:666-681` (`_validate_dh_group`)

**Actor / resource / forbidden action:** A network attacker able to tamper with ARD DH parameters (or a broken/malicious endpoint) can supply a prime modulus whose multiplicative group has a tiny subgroup and place `peer_key` in that subgroup. A network observer can then enumerate the tiny set of shared secrets and decrypt the username/password credential block, an action that should require solving a large discrete logarithm.

**Failed trust assumption:** Primality of `p`, range checks on `g`, and rejecting only peer/shared values `0`, `1`, and `p-1` do not establish that the peer public key has large order. The comment at `rfb.py:341-342` claims to catch a peer key in a tiny subgroup, but the code only catches order-one/two degeneracies. It accepts order 3, 5, and other small subgroups. Nor does it establish that `p` is a safe prime or validate subgroup membership/order.

**Evidence / offline reproduction:** A deterministic offline test generated a valid 1024-bit prime `p` with `3 | p-1`, constructed an order-3 peer element, and called the production validator with the wire-representable generator 2:

```python
p = nextprime((1 << 1023) + 123456789)
while p % 3 != 1:
    p = nextprime(p + 1)
peer = next(pow(h, (p-1)//3, p) for h in range(2, 100)
            if pow(h, (p-1)//3, p) != 1)
_validate_dh_group(2, p, peer)       # accepted
```

Observed result:

```text
accepted_bits= 1024 generator= 2 peer^3= 1 nontrivial_shared= 2 candidate_AES_keys= 2
```

For a random client exponent, the shared secret is only `1`, `peer`, or `peer²`; line 343 rejects `1`, but the other two pass and produce only two candidate MD5/AES keys. The known 64-byte NUL-terminated credential field structure makes the correct decryption readily identifiable.

**Impact:** Recovery of the full ARD username and password from a credential exchange that the documentation claims is protected from weak/tampered parameters. Lack of server identity already lets an active attacker impersonate the server and obtain credentials, which limits incremental severity, but this defect specifically defeats the implemented and documented DH hardening and permits passive decryption after weak parameters are introduced.

**Remediation:** Prefer an allowlist of exact, reviewed Apple DH groups. If arbitrary groups must remain supported, establish a large prime-order subgroup: for a safe-prime construction, verify both `p` and `q=(p-1)/2` are prime, reject `±1`, and enforce the protocol-appropriate generator/public-key subgroup rules. Otherwise validate against known factorization of `p-1` and reject public keys with insufficient order. Add an order-3 regression test alongside the existing 0/1/`p-1` cases. Authenticated server identity/transport remains necessary because DH parameter validation cannot authenticate a hostile endpoint.

---

### AUTH-03 — Low — Unticking “Save password” does not revoke the stored credential unless the next connection succeeds

**CWE:** CWE-459 (Incomplete Cleanup)

**Locations:**

- `src/openvncviewer/ui.py:715-720` (empty replacement is only queued)
- `src/openvncviewer/ui.py:742-746` (history update only after successful server initialization)
- `src/openvncviewer/ui.py:783-786` (failed connection discards the queued deletion)
- `src/openvncviewer/history.py:126-140` (empty token would correctly remove it if called)

**Actor / resource / forbidden action:** After a user unticks password saving and initiates a connection, a later same-account process or config reader can still recover the old DPAPI blob if that connection fails. The user's requested credential deletion has not occurred.

**Failed trust assumption:** The code couples a security-sensitive deletion to the “remember only successfully connected servers” UX rule. `connect_to` queues an empty token, but `history.remember` runs only in `_on_resize` after authentication/server initialization. `_on_disconnect` throws the pending deletion away on failure. On reopening/selecting the entry, the old password decrypts and the box is checked again.

**Evidence / reproduction:** Save a password for an existing server; reopen it, untick **Save password**, and connect to an unreachable port or force authentication failure. After failure, inspect/reopen `%LOCALAPPDATA%\OpenVNCViewer\servers.json`: the old `password` token remains, and selecting the server restores the password. The existing unit test verifies `history.remember(..., password="")` deletes correctly, but no call is made on this failure path.

**Impact:** Credential retention contrary to the user's revocation action, especially problematic if the user unticks because the machine/account is about to become less trusted. The token remains DPAPI-protected, so this is Low rather than plaintext exposure.

**Remediation:** Separate credential revocation from successful-connection history updates. When an existing entry is submitted with saving unticked (or when the checkbox transitions off, after confirmation if desired), immediately persist an empty password while preserving its other metadata. A failed network connection must not roll back credential deletion. Add a UI/controller test for failure after unticking.

---

## Documented / accepted architectural limitations (not new defects)

These are explicitly accepted in `SECURITY.md:49-79`, `README.md:297-328`, and `TODO.md:42-78` and are not counted above:

1. **No server identity verification (critical architectural exposure; CWE-295).** No certificate, host key, pin, or TOFU record exists. An interceptor can impersonate an ARD server and receive account credentials. DNS names and saved server labels provide usability, not identity.
2. **No transport protection (high architectural exposure; CWE-319).** TLS/VeNCrypt and Apple RSA-AES types are unsupported; framebuffer, keyboard, pointer, and standard RFB clipboard traffic are plaintext after authentication. The documented mitigation is SSH/VPN with proper SSH host-key verification.
3. **Legacy VNC authentication is intrinsically weak (CWE-327).** DES, an eight-byte password limit, and visible challenge/response permit offline recovery. The implementation matches the protocol and warns users.
4. **ARD's MD5-derived AES-128-ECB credential format is protocol-defined (CWE-327).** This cannot be replaced unilaterally, though stronger Apple security types could be implemented.
5. **Python immutable-string credential lifetime (CWE-316).** `RFBClient.password` is retained for the active client/session and cannot be zeroed reliably; this and crash-dump exposure are already documented. Avoidable lifetime reduction after `_authenticate` would still be worthwhile.
6. **DPAPI's same-user boundary.** Code running as the logged-in user can decrypt saved blobs. This is accurately documented and same-user code execution is explicitly out of scope. Windows Credential Manager integration is a documented future improvement.
7. **Unsigned release executables.** This is documented, with published SHA-256 verification as the current mitigation.

---

## Negative results and defensive observations

- **DPAPI use:** Static review found user-scoped `CryptProtectData`/`CryptUnprotectData`, application entropy, `CRYPTPROTECT_UI_FORBIDDEN`, explicit 64-bit-safe ctypes signatures, `LocalFree`, strict Base64 parsing, and no plaintext fallback. Saving is disabled when a real probe round trip fails. No machine-wide DPAPI flag is used.
- **DPAPI test limitation:** The audit host is Linux, so six real-DPAPI tests were skipped. The non-Windows/no-backend tests passed; no claim is made that Windows DPAPI was dynamically exercised here.
- **Saved-password defaults:** Saving is off by default; empty/failed encryption is never written as plaintext. Removing a server deletes its token, and a successful reconnect with saving unticked replaces the token with an empty value.
- **Config-file parsing:** `servers.json` is treated as untrusted structured input: malformed JSON is discarded, entry types are checked, ports are bounded, entries are capped, writes use a same-directory temporary file and atomic replace, and `tempfile.mkstemp` gives restrictive permissions on POSIX. Hostnames/usernames/labels are intentionally plaintext metadata.
- **Config trust boundary:** Editing a token/host mapping can redirect where the UI connects, but under the normal `%LOCALAPPDATA%` ACL this requires the same user (who can already invoke DPAPI) or prior compromise of that user's profile. No cross-user credential decryption path was found. Users still must visually verify the host; labels are not authenticated identity.
- **Credential CLI/log exposure:** Passwords are not accepted on the command line and are not logged or placed in error messages. The password widget uses masked echo mode.
- **RFB parsing:** Security failure reasons do not include client credentials. Offered-type counts are byte-bounded. Unsupported security types fail rather than being interpreted as a supported mechanism.
- **Tests:** `PYTHONPATH=src pytest -q tests/test_protocol.py tests/test_secretstore.py tests/test_history.py` completed with **46 passed, 6 skipped**. Safe offline and loopback reproductions above did not modify tracked files; final `git status --short` was clean.
- **Commit-specific workflow:** The audited commit adds checkout and release-note generation only. It does not add credential interpolation or secret handling, and the release job retains narrowly stated `contents: write` permission. No auth/secret defect specific to the commit diff was found.
