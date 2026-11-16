# NCWU S202 current-session selected-count and missing-contract audit

**Basis:** the owner's first-party 2026-11-16 NCWU interface inventory,
the actual 07xh/kingo-assistant Windows code, and earlier authorized NCWU
read-only implementations in `07xh/kingo-live/ncwu/hs_sniper.py`.
The inventory names the initializer and class-eligibility endpoints, but does
**not** contain their complete parameter/response contracts.

## What is integrated

1. **S202 selection count**. `core/ncwu_s202_score.py` makes a bounded
   two-request, no-browser **read-only** query. It POSTs the previously observed
   `getWsxkTimeRange.action?xktype=2` with an empty body, obtains `xn`,
   `xqM` and `xh` from that *same logged-in EXE session*, then POSTs the
   observed `getSelectLessonScoreKcsInfo.action` using the prior captured
   `xn/xq_m/xh` field names and the repo's GBK form encoder.
2. It recognizes only an explicit `result.yxms` (bounded non-negative integer)
   from a business-`status=200` response. Missing or changed fields return
   `score_unrecognized`. An observed aggregate count is **not** proof that
   any target course or teaching class was enrolled.
3. Window status `400` can still have a current account/term context in the
   first-party `result`; this information is read-only even when the round is
   closed. No archived account, term, round, cookies or login credentials are
   substituted. The EXE never logs the response's `xh` or `lcid`.
4. Existing NCWU request-policy limits apply; 403/429/redirect/login expiry stop
   the flow without retry. The user-triggered check is independently
   cooldown-limited to at least 60 seconds. There is no background loop.
5. Windows `工具与日志` includes
   `核对本人已选门数（只读）`; this is separate from
   `读取华水 S202 已选结果`.

## What is still unverified, now easier to inspect

- `STU_DynamicInitDataAction.do` and
  `isSelectableSkbjdm.action` are **not called** by this patch. The first
  endpoint may initialize a new selection context; the second may require an
  authoritative `skbjdm` not present in a table's visible `skbh` column.
  Sending guessed values is not acceptable evidence.
- New local-only `core/ncwu_contract_capture.py` parses a user-selected,
  at-most-16MiB HAR for **exact host + exact path + POST** entries for those two
  endpoints. It outputs the capture counts, HTTP status codes and **only**
  recognized request/response property names; never raw payload values,
  cookies, student identifiers, token contents, page HTML or response bodies.
  It cannot send a network request.
- Windows `工具与日志` provides
  `核验初始化／资格预检 HAR（离线）` for this evidence report.
  The message may report missing fields or missing samples without pretending
  the interface has been implemented.

## Remaining acceptance gates

- User tests actual native login, S202 round and course query using their own
  credentials in the EXE. Offline CI cannot establish live-school acceptance.
- The user inspects **their own normal first-party interaction** with the
  initialization/eligibility interfaces, using the local HAR inspector.
  Only field *names* are recorded; human review still has to confirm request
  values' derivation and success/failure semantics before any live adapter.
- Revalidate field names, selection round, teach-class ID vs display class
  number, response failure statuses, official readback, terms of use and
  permission. Automatic enrollment/cancel remain disabled.
- Run unit tests, source self-test, Windows PyInstaller and packaged Tk GUI
  smoke on the exact commit. Never treat a successful build as a verified school
  write.
