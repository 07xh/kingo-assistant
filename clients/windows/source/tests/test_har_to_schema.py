"""Offline, draft-only HAR -> protocol mapping tests.

These cover the safe surface: redaction, timetable exclusion, HTML column
inference, term/list_fields split, and the enforced verified=False draft.
No network, no replay, no credential leakage.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.har_to_schema import (
    build_protocol_draft, load_entries, guess_host, score_all,
    merge_protocol, redact, is_sensitive, self_test,
)


def _html_catalog(xnxq="2026-2027-1", kkyx="01"):
    th = ("<th>课号</th><th>课程名称</th><th>教师</th><th>余量</th>"
          "<th>教学班</th>")
    rows = "".join(
        f"<tr><td>K00{n}</td><td>课程{n}</td><td>老师{n}</td>"
        f"<td>{3 - n}</td><td>0{n}</td></tr>" for n in range(2))
    return f"<table>{th}{rows}</table>", xnxq, kkyx


def _entry(idx, method, path, params=None, body="", mime="text/html; charset=GBK"):
    return {
        "request": {
            "method": method,
            "url": "https://jw.example.edu.cn" + path,
            "headers": [{"name": "Cookie", "value": "JSESSIONID=PRIVATE_TOK"}],
            "postData": {"mimeType": "application/x-www-form-urlencoded",
                         "params": [{"name": k, "value": v}
                                    for k, v in (params or {}).items()]},
        },
        "response": {"status": 200,
                     "headers": [{"name": "Content-Type", "value": mime}],
                     "content": {"mimeType": mime, "text": body}},
    }


def _har() -> str:
    catalog_body, xnxq, kkyx = _html_catalog()
    entries = [
        _entry(0, "GET", "/js/app.js", body="var x=1;",
               mime="application/javascript"),
        _entry(1, "GET", "/xsxk/courseList",
               params={"xnxq": xnxq, "kkyx": kkyx}, body=catalog_body),
        _entry(2, "GET", "/hsjw/wap/mycourseschedule.action",
               body=json.dumps({"sjhjinfo": [], "prohibit": "1",
                                "msg": "当前时间不是有效的选课时间区段!"},
                               ensure_ascii=True)),
    ]
    return json.dumps({"log": {"entries": entries}}, ensure_ascii=False)


def _cands():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "s.har"
        p.write_text(_har(), encoding="utf-8")
        c = load_entries(str(p))
        c = [x for x in c if x.host == guess_host(c)]
    score_all(c)
    return c


class HarToSchemaTest(unittest.TestCase):
    def test_self_test_passes(self):
        self.assertEqual(self_test(), 0)

    def test_catalog_mapped_to_html_columns(self):
        draft, _ = build_protocol_draft(_cands(), {})
        self.assertEqual(draft["list"], "/xsxk/courseList")
        self.assertEqual(draft["list_method"], "GET")
        self.assertEqual(draft["columns"], {
            "course_id": 0, "course_name": 1, "teacher": 2,
            "remain": 3, "class_id": 4,
        })

    def test_term_field_and_list_fields_split(self):
        draft, _ = build_protocol_draft(_cands(), {})
        self.assertEqual(draft["term_field"], "xnxq")
        self.assertEqual(draft["list_fields"], {"kkyx": "01"})
        self.assertNotIn("xnxq", draft["list_fields"])

    def test_verified_always_false(self):
        draft, _ = build_protocol_draft(_cands(), {})
        self.assertIs(draft["verified"], False)

    def test_identity_left_empty_with_warning(self):
        draft, report = build_protocol_draft(_cands(), {})
        self.assertEqual(draft["identity_path"], "")
        self.assertTrue(any("identity" in w.lower() for w in report["warnings"]))

    def test_timetable_excluded_and_warned(self):
        _, report = build_protocol_draft(_cands(), {})
        self.assertTrue(any("timetable" in w.lower()
                            for w in report["warnings"]))

    def test_cookie_redacted_everywhere(self):
        _, report = build_protocol_draft(_cands(), {})
        text = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("PRIVATE_TOK", text)
        self.assertNotIn("JSESSIONID", text)

    def test_merge_protocol_preserves_other_keys(self):
        cfg = {"protocol": {"verified": False, "list": "/OLD"},
               "courses": [{"course_id": "KEEP"}]}
        merged = merge_protocol(cfg, build_protocol_draft(_cands(), {})[0])
        self.assertEqual(merged["courses"], [{"course_id": "KEEP"}])
        self.assertEqual(merged["protocol"]["list"], "/xsxk/courseList")
        self.assertIs(merged["protocol"]["verified"], False)

    def test_sensitive_list_param_excluded(self):
        entries = [
            _entry(1, "POST", "/xsxk/courseList",
                   params={"xnxq": "2026-2027-1", "token": "SUPERSECRET",
                           "kkyx": "01"},
                   body=_html_catalog()[0]),
        ]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.har"
            p.write_text(json.dumps({"log": {"entries": entries}}), encoding="utf-8")
            c = load_entries(str(p))
            score_all(c)
            draft, report = build_protocol_draft(c, {})
        self.assertNotIn("token", draft["list_fields"])
        self.assertTrue(any("token" in w for w in report["warnings"]))

    def test_redact_scrubs_phones_and_idcards(self):
        self.assertIn("<PHONE>", redact("note", "call 13800138000 now"))
        self.assertIn("<IDCARD>", redact("note", "id 110101199003071234X"))

    def test_is_sensitive_matches_common_names(self):
        for name in ("Cookie", "Authorization", "token", "JSESSIONID", "sign"):
            self.assertTrue(is_sensitive(name))


if __name__ == "__main__":
    unittest.main()
