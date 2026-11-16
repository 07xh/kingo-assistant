# NCWU pre-opening target-course alignment, 2026-11-17

## User-facing functionality

The native Windows EXE already reads an authenticated **course-plan
snapshot** via the first-party 5327042 endpoint. The desktop editor already
stores up to 30 **local target preferences** with priority, course code and
optional teaching-class ID. These were previously unrelated features: the
user could read real plan rows but accidentally leave stale or wrong targets.

The new `core/ncwu_plan_alignment.py` connects them **offline**, after a
successful real plan read. In the existing
`读取本学期开课计划（只读快照）` result popup, a separate summary now
indicates how many target course + class **identifiers** align and offers
`查看逐项志愿核对` for concrete differences. Nothing is auto-updated.

## Conservative comparison rules

- The current 5327042 plan response contains only the school's non-sensitive
  `xn` and `xqM` term indicators as in-memory `PlanSnapshot` fields.
  The user's local `xnxq` is checked in the exact `YYYY-YYYY-N` format;
  the preceding first-party investigation established that `xqM=0` is first
  semester, `xqM=1` is second. **A missing/unrecognized/mismatched term
  prevents any target from being shown as aligned.**
- Course codes must match exactly (leading zeros preserved). A target's
  `class_id` is compared to the plan's actual `curent_skbjdm`/`skbjdm`.
  Do **not** equate the S202 displayed `skbh` with these identifiers.
- If no class ID is configured, the report indicates how many classes in
  the *plan snapshot* belong to that course. It **does not** select a default.
- Missing course, unknown class-ID field, unmatched exact class ID,
  duplicate same-code/same-class rows and conflicting name/teacher each
  have distinct, actionable statuses. Unknown teacher data stays
  `metadata_unverified` instead of being treated as a contradiction.
- Even `matched_ids` means only *plan identifier agreement*.
  It never asserts live available seats, eligibility to select, absence of
  timetable clashes, successful enrollment, or server acknowledgment.
- The algorithm does not persist a plan, modify YAML, reset priorities,
  open a browser, send any extra school HTTP requests or print session
  credentials. If local config is invalid, the school plan table still opens,
  with a clear notice that preference matching could not run.

## Verification

- Synthetic unit tests cover both semester indices, mismatches and unknown
  terms; exact code/teaching-class matches; template placeholders; duplicate
  records; missing/mismatching teachers; no class identifiers; empty and
  invalid course configuration; and absence of inferred live-seat claims.
- Headless GUI tests verify the comparison button appears for configured
  preferences and that unreadable local YAML does not hide the plan results.
- The earlier mocked three-request NCWU plan reader additionally verifies
  that only current session's academic year/term metadata is added to the
  snapshot. The actual identity and historical plan availability remain
  excluded from the match verdict.

## Remaining live and write-contract gaps

These tests do not prove the student's account can currently read the real
plan, and do not enable `STU_DynamicInitDataAction.do`,
`isSelectableSkbjdm.action` or any enrollment write. Their parameters and
positive/negative semantics still require first-party authorized evidence.
Use the separately shipped local HAR contract report for those two routes,
without uploading passwords, cookies, active tokens or private class data.
