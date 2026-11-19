# NCWU S202 round scope: verified EXE account and semester

## Historical evidence and actual boundary

The owner's authorized 2026-11-14 HAR contains two normal
`POST /hsjw/jw/common/getWsxkTimeRange.action?xktype=2`
responses. Both include a real account scope (`xh`), academic year
(`xn`), term code (`xqM`), term label (`xqName`) and selection-round
identifier (`lcid`), even though the school reported that the window
was **closed** (business `status=400`). No captured account or round ID
is copied to this public repository.

The same capture's S202 selected-results HTML uses visible course name,
teacher and display-class columns; it has **no independently proven
exact `kcdm/skbjdm` record linked to that `lcid`**. The existing
legacy result-table query has separate scope checks, but it is not
proven to be a current S202 round-specific authoritative enrollment
source. These datasets MUST NOT be silently promoted to final success.

## Source and Windows changes

The official round probe still sends **one** first-party, budget-gated,
no-body POST using an EXE-owned authenticated session. It now optionally
checks the returned raw context against the independently verified account
and target semester supplied by the GUI.

- `xh` must exactly match the verified EXE login account.
- `xn`, `xqM` and `xqName` must match the **observed first semester**
  mapping for the locally selected `YYYY-(YYYY+1)-1`. Second-semester
  school encoding has not been accepted and is deliberately not inferred.
- `lcid` must be a single bounded, well-formed opaque round identifier,
  present in this specific official response; its bytes stay in the parser.
- `session_scope_checked` and `round_id_observed` are independent
  diagnostic flags. They do **not** represent selected-course proof.
- An account mismatch immediately revokes the in-process EXE session
  through the normal GUI completion path. Term mismatch and missing
  round return fail-closed statuses.
- Closed window responses may have a verified identity and a round ID.
  A matching round never changes the school's actual open/closed status.

The old independent, structurally unbound parser remains usable by legacy
offline callers but clearly reports that it is **not account verified**.
The GUI refuses an unbound current-round probe. No browser, HAR replay,
credential persistence, CAPTCHA bypass, fast polling or enrollment POST
was introduced.

## Tests and remaining proof

Offline tests exercise exact account + first-term matching, wrong-account
and wrong-term responses, missing round IDs, closed windows, no-network
local preflight, and Windows session revocation. All fixtures use fictional
IDs, and no tests send university traffic.

Before the enrollment engine may use a current-round result, an official
selected-class record source must be separately accepted as authoritative
and show an exact course code + internal `skbjdm`, the current account and
term, the **same verified `lcid`**, and accepted server-side selection
status. The current S202 HTML observations do NOT demonstrate these
requirements. Production writes remain disabled.
