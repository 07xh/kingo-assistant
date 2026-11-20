"""Offline tests for the ``main.py --har-form`` pairing check.

This is the operator-facing entry point: it must work on a machine that is not
logged in, must never print a field value, and must return a non-zero code when
the HAR and the evidence file are not the same write.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from core.har_form_cli import main as har_form_main  # noqa: E402

ITEMS = "3141592653589"
HID = "9f2c31ab77"
STUDENT = "20260001"
ROUND = "S202"
ORIGIN = "https://jwmis.ncwu.edu.cn"
SUBMIT_PATH = "/hsjw/STU_ElectCourseAction.do"


def sha(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def evidence(**overrides) -> dict:
    payload = {
        "version": 1, "gate": "official_write_verified",
        "captured_at": "2026-11-23T08:00:03+08:00",
        "source": {"har_sha256": "0" * 64, "catalog_sha256": "1" * 64,
                   "origin": ORIGIN, "path": SUBMIT_PATH,
                   "referer_present": True,
                   "content_type": "application/x-www-form-urlencoded; charset=GBK",
                   "http_status": 200},
        "form": {"field_order": ["xh", "xn", "xq", "lcid", "xktype", "hidOption", "items"],
                 "required_present": ["hidOption", "items", "lcid", "xh", "xktype", "xn", "xq"],
                 "xktype": "2", "academic_year": "2026", "academic_term": "1",
                 "round_id": ROUND, "hid_option_len": len(HID),
                 "hid_option_sha256": sha(HID),
                 "student_id_sha256": sha(STUDENT), "student_id_tail": "0001"},
        "items": {"value_sha256": sha(ITEMS), "length": len(ITEMS),
                  "contains_pipe": False, "shape": "d"},
        "bound_teaching_class": {"course_id": "K0012345", "class_id": ITEMS,
                                 "skbh": "(1)", "skbjdm": ITEMS,
                                 "matched_field": "skbjdm", "match_strength": "high",
                                 "matched_token": ITEMS, "remain": 3,
                                 "name": "示例课程"},
        "mapping_proven": True,
    }
    payload.update(overrides)
    return payload


def har(*, items=ITEMS, hid=HID, student=STUDENT, round_id=ROUND, origin=ORIGIN) -> dict:
    return {"log": {"version": "1.2", "entries": [{
        "startedDateTime": "2026-11-23T08:00:03.512+08:00",
        "request": {
            "method": "POST", "url": origin + SUBMIT_PATH,
            "headers": [{"name": "Content-Type",
                         "value": "application/x-www-form-urlencoded; charset=GBK"}],
            "postData": {"mimeType": "application/x-www-form-urlencoded",
                         "text": (f"xh={student}&xn=2026&xq=1&lcid={round_id}"
                                  f"&xktype=2&hidOption={hid}&items={items}")},
        },
        "response": {"status": 200, "headers": [], "content": {"text": "flag:1"}},
    }]}}


class Fixture(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def files(self, *, evidence_payload=None, har_payload=None):
        evidence_path = self.dir / "items_binding.json"
        har_path = self.dir / "capture.har"
        evidence_path.write_text(
            json.dumps(evidence_payload or evidence(), ensure_ascii=False),
            encoding="utf-8")
        har_path.write_text(
            json.dumps(har_payload or har(), ensure_ascii=False), encoding="utf-8")
        return har_path, evidence_path


class PairingTests(Fixture):
    def test_pairing_passes_without_scope(self):
        har_path, evidence_path = self.files()
        code = har_form_main(["--har", str(har_path),
                              "--evidence", str(evidence_path)])
        self.assertEqual(code, 0)

    def test_json_output_is_machine_readable(self):
        har_path, evidence_path = self.files()
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = har_form_main(["--har", str(har_path),
                                  "--evidence", str(evidence_path), "--json"])
        payload = json.loads(buffer.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["stage"], "pairing")
        self.assertEqual(payload["evidence"]["course_id"], "K0012345")
        self.assertIn("missing_scope", payload)

    def test_tampered_items_fails(self):
        har_path, evidence_path = self.files(har_payload=har(items="9999999999999"))
        code = har_form_main(["--har", str(har_path),
                              "--evidence", str(evidence_path)])
        self.assertEqual(code, 3)

    def test_tampered_hid_option_fails(self):
        har_path, evidence_path = self.files(har_payload=har(hid="deadbeef00"))
        code = har_form_main(["--har", str(har_path),
                              "--evidence", str(evidence_path)])
        self.assertEqual(code, 3)

    def test_missing_evidence_file_fails(self):
        har_path, _ = self.files()
        code = har_form_main(["--har", str(har_path),
                              "--evidence", str(self.dir / "absent.json")])
        self.assertEqual(code, 2)

    def test_course_mismatch_fails(self):
        har_path, evidence_path = self.files()
        code = har_form_main(["--har", str(har_path), "--evidence", str(evidence_path),
                              "--course", "K9999999", "--class-id", ITEMS])
        self.assertEqual(code, 3)

    def test_course_without_class_fails(self):
        har_path, evidence_path = self.files()
        code = har_form_main(["--har", str(har_path), "--evidence", str(evidence_path),
                              "--course", "K0012345"])
        self.assertEqual(code, 2)

    def test_output_never_prints_field_values(self):
        har_path, evidence_path = self.files()
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            har_form_main(["--har", str(har_path), "--evidence", str(evidence_path),
                           "--course", "K0012345", "--class-id", ITEMS])
        text = buffer.getvalue()
        self.assertNotIn(STUDENT, text)
        self.assertNotIn(HID, text)


class ObservationTests(Fixture):
    def scope_args(self, **overrides):
        values = {"--account-scope": "acct-demo", "--semester": "2026-2027-1",
                  "--student-id": STUDENT, "--academic-year": "2026",
                  "--academic-term": "1", "--round": ROUND}
        values.update(overrides)
        argv = []
        for name, value in values.items():
            argv += [name, value]
        return argv

    def test_full_scope_reaches_observation(self):
        har_path, evidence_path = self.files()
        argv = ["--har", str(har_path), "--evidence", str(evidence_path),
                "--course", "K0012345", "--class-id", ITEMS] + self.scope_args()
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = har_form_main(argv + ["--json"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["stage"], "observation")
        self.assertEqual(payload["observation"], "ready")

    def test_stale_student_id_fails(self):
        har_path, evidence_path = self.files()
        argv = ["--har", str(har_path), "--evidence", str(evidence_path),
                "--course", "K0012345", "--class-id", ITEMS] + \
               self.scope_args(**{"--student-id": "20269999"})
        code = har_form_main(argv)
        self.assertEqual(code, 4)

    def test_stale_round_fails(self):
        har_path, evidence_path = self.files()
        argv = ["--har", str(har_path), "--evidence", str(evidence_path),
                "--course", "K0012345", "--class-id", ITEMS] + \
               self.scope_args(**{"--round": "S203"})
        code = har_form_main(argv)
        self.assertEqual(code, 4)


class MainEntryPointTests(Fixture):
    def run_main(self, *extra):
        return subprocess.run(
            [sys.executable, "main.py", *extra],
            cwd=str(SOURCE_ROOT), capture_output=True, text=True, timeout=120)

    def test_har_form_without_files_returns_two(self):
        result = self.run_main("--har-form")
        self.assertEqual(result.returncode, 2)

    def test_har_form_end_to_end(self):
        har_path, evidence_path = self.files()
        result = self.run_main("--har-form", "--har-file", str(har_path),
                               "--har-evidence", str(evidence_path), "--har-json")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])

    def test_har_form_does_not_load_config(self):
        """No config.yaml is needed: the check is purely local."""
        har_path, evidence_path = self.files()
        result = self.run_main("-c", "definitely-absent.yaml", "--har-form",
                               "--har-file", str(har_path),
                               "--har-evidence", str(evidence_path), "--har-json")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
