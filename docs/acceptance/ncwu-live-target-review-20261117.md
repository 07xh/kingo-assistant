# NCWU S202 live catalog × locally configured targets — 2026-11-17

## Goal

The EXE can log in using the official NCWU signed form, read S202 course
table `2568` using a fresh first-party `ActionForm`, read the planning
table `5327042`, and manage local target preferences. However, its **live
S202 popup** previously displayed only a course list, with no indication
whether any code corresponded to the user's configured target. This release
adds an immediate local-only comparison on every successful *real* read.

## Source-of-truth and strict limits

- `head_kcdm` is the S202 displayed course code; `head_skbh` is the
  visible teaching-class number. The local `class_id` can be the internal
  `skbjdm` from plan table `5327042`; **these are not interchangeable**.
  The application never treats a displayed `skbh` as verified identity,
  even when its string coincides with a configured `class_id`.
- The first-party `getWsxkTimeRange.action?xktype=2` business
  status-200 response can contain `xh/xn/xqM/lcid/nj/zydm` from the same
  current session. These fields now allow the read-only S202 snapshot to
  retain only its **academic year and semester**, and only if the current
  HTML `ActionForm` does not contradict those values. A conflict stops
  the catalog request; never send a stale user's form.
- Closed windows and incomplete time-window JSON cannot confirm the
  correct academic term. Any course matches without confirmed term are
  reported as **unverified** and are not labelled target candidates.
- Candidate association requires exact course code and consistent
  nonempty course name/teacher values when the target specifies them.
  A row can be a related *course candidate*, **not** a confirmed target
  teaching class. Not finding a course in one DataTable response cannot
  prove the course is absent from the university offering: the table
  could have filters, pagination or unpublished records.
- A course candidate is not a seat count, eligibility, purchased coins,
  successful course selection, enrolled result or permission to execute
  a write. No request is sent for this local review.

## Windows UI

After **实时读取可选课程（S202 · 只读）**, the existing live course popup now
has an extra rightmost **本地志愿（仅候选）** column. When official round
semester and local configuration agree, candidate rows can show e.g.
`课程候选：A`. A **查看志愿候选分析** button explains missing codes,
teacher/name differences, unknown or mismatched terms and the distinction
between `skbh` and `class_id`. If the local YAML is invalid, the official
course table remains visible and comparison is skipped.

The feature neither changes local saved preferences nor edits school data.
It does not enable submission, withdrawal, credential persistence, automatic
CAPTCHA solving or new polling.

## Acceptance boundaries

Synthetic tests validate a positive server term, user-account form
contradictions, missing/mismatched term, leading-zero course codes,
displayed teaching-class numbers vs internal class IDs, teacher conflicts,
multiple displayed classes, incomplete metadata and headless GUI behavior.
A passing Windows build does not establish real account acceptance. When
the user can log in and the S202 page returns an actual catalog, compare
the list with their official page and verify class mapping separately from
the school's own class-identity fields.

Still blocked by missing protocol evidence: `STU_DynamicInitDataAction.do`
and `isSelectableSkbjdm.action` request derivation/positive-negative
responses; no enrollment POST or result-confirmed write is available.
