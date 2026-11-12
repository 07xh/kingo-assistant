# NCWU / Xiqueer HAR structural audit — 2026-11-11

**Source:** locally reviewed, user-provided HAR capture from 2026-11-11. This document contains **structural metadata only**. The original HAR, session credentials, tokens, signatures, student identifiers, course titles, class identifiers and response payloads are **not committed**.

## Observations

| Item | Evidence from the local capture | Interpretation |
|---|---|---|
| Total network entries | 67 | Includes unrelated services and CONNECT entries |
| Xiqueer API | 56 entries for `api.xiqueer.com`; 53 are POSTs to `/manager//wap/wapController.jsp` | Shared mobile-app API; request payloads are signed / encoded |
| School teaching-system host | 2 POSTs to `jwmis.ncwu.edu.cn` | Direct school-domain read responses |
| Timetable | `/hsjw//wap/mycourseschedule.action`, HTTP 200, JSON response | Fields include `xn`, `xq`, `zc`, `week1`…`week7`, `sjhjinfo`; 10 total weekday array entries |
| Timetable auxiliary data | `/hsjw//wap/kb_kbdetail_bz.action`, HTTP 200 | `bz` array is empty in this capture |
| Xiqueer course/selection metadata | Responses contain `yxinfo`, `zxinfo`, `kcxq`, `xksj` | Related structures exist, but have not been mapped to an authenticated, stable, read-only *catalog* endpoint |
| Response `zxinfo[0].zxxq` | 11 entries; keys include `kcmc`, `skbj`, `kcid`, `yixuan`, `kex` | Course/selection records; **not** evidence of 11 available courses, open seats or successful enrollment |
| Response `yxinfo[0].yxxq` / `kcxq` | Both empty in the sampled responses | Do not interpret as a verified empty elective catalog |
| Selection gate | One sampled response has `prohibit: "1"` and the message `当前时间不是有效的选课时间区段!` | Selection-window restriction is explicitly reported; do not circumvent it |
| Live seat availability | No response validated against official UI containing a confirmed remaining-seat field | **Unverified** |
| Enrollment submission | No authorized end-to-end submission/confirmation evidence | **Not implemented** |

## Security / privacy notes

The capture contains non-empty `token`, `xqerSign`, `encrptSecretKey`, `param`, and `param2` values in the 53 Xiqueer POST bodies, plus multiple `Set-Cookie` headers. It also contains personal timetable/class fields. Do not publish the HAR or replay it against school services. Assume exposed session material is sensitive; sign out and refresh relevant sessions if the raw file was shared publicly. Do not commit or log any raw request, response, cookie, signature, or class record.

## Code alignment (Windows V4.6)

- Existing `clients/windows/source/core/client.py` expects a verified HTML elective-catalog page, a GBK form submission and table-column selectors.
- The captured school timetable response is **JSON** and does not match that HTML course/seat parser.
- The captured Xiqueer requests have encoded or signed application-specific fields; their presence does not make a safe or stable public API contract.
- Keep `protocol.verified: false` in `clients/windows/source/config.example.yaml`. Do **not** map the timetable endpoint into `protocol.list`, invent a `remain` field, or relabel `zxxq` as available seats.

## Next acceptance gates

1. **Offline JSON schema tests (implemented in this follow-up):** `core/ncwu_timetable_schema.py` validates only the observed personal-timetable top-level keys, types, JSON uniqueness, and bounded sizes. Synthetic regression tests cover missing/invalid days, duplicate keys, encoded HAR content, privacy-safe summaries, and the distinction from elective catalog evidence. No personal records or university requests are used.
2. **Official UI comparison:** during a permitted selection window, compare a personally authorized, read-only course-catalog screen against a sanitized response schema; identify genuine course identity, teaching-class identity, capacity and remaining-seat meanings.
3. **Authentication and access verification:** confirm a supported, permission-compliant read-only access path. Do not reuse credentials or signed request material from HAR.
4. **Fail closed:** preserve explicit `unverified`, `selection window closed`, `authentication expired` and `unknown availability` states. Do not equate a successful HTTP status with available capacity or successful enrollment.

**Conclusion:** This HAR validates **the existence and response schema** of a real NCWU timetable API and confirms that Xiqueer returns selection-related metadata. It does **not** validate KingoAssistant's live elective-catalog monitoring.
