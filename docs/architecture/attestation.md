# Write-gate attestation

Status: implemented (offline).  Owner module: `core/attestation.py`.
No university endpoint is contacted by anything described here.

## The problem this replaces

Before this change the enrollment engine required six conditions to be true,
four of which were capabilities the school adapter had to declare:

```
user_opted_in                       user checkbox
institution_permits_write           constructor argument, default False
gateway.official_catalog_verified   never set anywhere
gateway.official_enrollment_verified never set anywhere
gateway.official_window_verified    never set anywhere
gateway.official_write_verified     never set anywhere
```

`windows_gui.py` hard-coded `self._engine_factory = None` and no module in the
repository could turn those flags on. The result was not *fail-closed* but
*fail-locked*: even with a fully correct protocol the write path was
unreachable, and the only way to test it would have been to edit the safety code
by hand — which is exactly the practice the gates existed to prevent.

## What replaces it

A gateway flag is now necessary but **not sufficient**. Each of the four gates
must additionally be covered by an `Attestation` stored in a local registry:

| Field | Meaning |
|---|---|
| `gate` | one of the four gate names |
| `evidence` | repository/app-relative path to the document that proves it |
| `contract_sha256` | sha256 of that document at the moment of issue |
| `captured_at` | tz-aware instant the evidence was accepted |
| `expires_at` | optional tz-aware deadline; `null` means no expiry |
| `reviewer` | who accepted it (self is acceptable for personal use) |
| `note` | free text, ≤500 chars |

Verification at write time re-hashes the evidence document. Consequences:

* editing the evidence **invalidates** the attestation until reissued;
* deleting the evidence fails closed with "证据文件已丢失";
* passing `expires_at` silently disarms the gate — re-capture before the window;
* a `captured_at` more than 10 minutes in the future is rejected (clock skew).

Gate check order inside `EnrollmentEngine._gate()`:

1. `gateway.account_scope` must equal the journal's scope;
2. `user_opted_in` and `institution_permits_write` must both be `True`;
3. for each gate: the gateway must declare it `True` **and** the registry must
   verify it — otherwise `EnrollmentNotReady`, no partial pass.

## Operating it

Everything below is offline and needs no config file:

```bash
# what is armed right now?
python main.py --attest status

# arm one gate against a real evidence document
python main.py --attest issue \
    --gate official_write_verified \
    --evidence docs/acceptance/ncwu-write-contract.md \
    --reviewer ishii --days 90 --note "one confirmed submission + readback"

# disarm immediately (e.g. school changed the contract)
python main.py --attest revoke --gate official_write_verified
```

The registry file defaults to `attestations.json` beside the application data
(`%LOCALAPPDATA%\KingoAssistantReadOnly\attestations.json`). Override with
`--registry` and `--evidence`-relative root with `--root`:

```bash
python main.py --attest --registry ./attestations.json --root . status
```

Or via config:

```yaml
attestation:
  path: "attestations.json"
  root: "."          # evidence paths resolve against this directory
```

`attestation` may also be omitted entirely; the default file is then
auto-detected and, if absent, all four gates read as unverified.

## What evidence actually satisfies a gate

Evidence is a document, not a promise. Minimum content per gate:

| Gate | Minimum evidence |
|---|---|
| `official_catalog_verified` | the official catalog endpoint, method, request fields, and the response field that carries remaining seats — with a screenshot/UI comparison recorded |
| `official_enrollment_verified` | the read-back endpoint that lists *this account's* enrolled classes for *this semester* |
| `official_window_verified` | how "window open" is decided, including what the server returns immediately before and after T0 |
| `official_write_verified` | the submit endpoint, its required fields, the success and rejection response shapes, and one end-to-end confirmation |

A sanitized HAR audit note, a UI comparison table, or an acceptance report in
`docs/acceptance/` all qualify. Screenshots alone do not: they cannot be hashed
into a contract and cannot be re-verified.

## Why expiry exists

Protocols drift. A contract captured in December may not hold in March: field
names change, a school enables CAPTCHA, or a new signing parameter appears. An
attestation with no expiry would silently authorize a stale understanding, so
`--days` is required in practice and the default is 90.

`expires_at = null` is allowed for genuinely stable, purely local facts, but the
four NCWU gates should always carry a date.

## Test coverage

`tests/test_attestation.py` (28 tests) and `tests/attestation_support.py`:

* every registry fixture is backed by a **real temporary document**, so digest
  verification and expiry are exercised rather than bypassed;
* missing / expired / tampered evidence each block the write gate;
* a valid registry passes the gate with **zero network access**;
* a gateway flag turned `False` still blocks even when evidence is valid;
* evidence paths cannot escape the root (`..`, absolute paths);
* corrupt or wrong-version registry files are errors, not empty registries.

## Deliberate limits

* The registry proves *what was verified*, not *who verified it* — this is a
  single-operator personal tool, not a multi-tenant control plane. `reviewer` is
  an audit string, not an identity.
* Arming all four gates still does not enable submission: no NCWU write adapter
  exists. The GUI indicator reads `验收证据 n/4` and the submit button stays
  disabled regardless.
* Nothing here relaxes the request budget, the 30 s polling floor, the serial
  write rule, or the halt-on-unknown rule.
