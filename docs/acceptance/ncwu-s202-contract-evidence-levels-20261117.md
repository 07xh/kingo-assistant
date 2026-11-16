# NCWU S202 two-endpoint HAR contract evidence: exact next gaps (2026-11-17)

**Problem:** The November 16 firsthand inventory named both
`STU_DynamicInitDataAction.do` and
`jw/common/isSelectableSkbjdm.action`, but did not establish the
complete request field/meaning and positive + negative result contract.
The existing Windows HAR inspector could list some POST body keys yet
could not distinguish a matched request from a usable JSON response,
omitted query-string fields, and ignored base64-encoded HAR responses.

## Implemented (no school HTTP requests)

- `core/ncwu_contract_capture.py` continues to accept only an
  explicitly selected, <=16 MiB local HAR and only the two exact, POST,
  HTTPS `jwmis.ncwu.edu.cn` paths.
- It reports capture counts, HTTP status codes, URL query **field names**,
  POST body **field names**, allowlisted MIME categories, JSON/HTML/empty
  response **shapes**, and outer/result JSON **field names**.
  It merges HAR `postData.params` and form/json `postData.text` schema.
  It can decode bounded base64 JSON **locally** for its structure, then
  discards the bytes. No cookies, authorization headers, token values,
  student IDs, teaching-class values, business message texts or raw
  captured payloads are included.
- Each endpoint now has one of five precise evidence levels:
  `missing_capture`, `missing_request_fields`,
  `missing_response_shape`, `missing_structured_response`, or
  `schema_only`. None represents validated success or authorization
  to perform an enrollment write.
- Windows `工具与日志` now has **复制脱敏契约报告** next to the existing
  local HAR inspection button. Copying requires an actual inspection and
  copies only the derived text. A malformed HAR clears the previous report
  instead of allowing accidental copying of stale evidence. The source
  HAR is not persisted or uploaded.

## How to use for future first-party contract checks

1. Record only your own normal, permitted site navigation while the
   relevant endpoint is called (if it is called at all).
2. Select the local HAR in Windows Tools. It remains on the user's PC.
3. Read both distinct status lines and use **复制脱敏契约报告** to share
   the field names/evidence level, not the HAR or active credentials.
4. Any report claiming `schema_only` still needs a human review of
   the original code for field derivation and at least one *authorized*
   successful and unsuccessful response to establish business semantics.
5. If no matching request appears, the user should not repeatedly send
   synthetic probes to a server. Use normal official UI flows or original
   public JS to determine whether the action actually occurs.

## Remaining blocked gates

This changes **only offline evidence extraction**, not the current native
S202 query/round/plan/result HTTP adapters. We do not treat an HTTP 200,
field-name match, a historical HAR or a bundled synthetic test as proof of
a working initializer, a positive/negative teaching-class eligibility
decision, live available seats, or successful enrollment. Those interfaces
remain disabled until the complete first-party request derivation,
permission and authoritative readback are verified.

## Acceptance tests

The synthetic tests cover mixed request query + POST parameters,
encoded form/json structures, UTF-8 base64 JSON responses, invalid base64,
incomplete captures, wrong hosts/methods, missing requests, zero exposure
of request/response *values*, and one-click GUI copy behavior. CI must
also build the native EXE and run packaged self-test/Tk smoke tests.
