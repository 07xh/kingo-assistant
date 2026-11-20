"""Offline tests for the HAR -> CurrentS202FormObservation bridge.

The evidence file produced by ``tools/patch/p0_protocol_recon.py`` contains only
hashes. This bridge is the only path from "one captured successful POST" to a
form the provider will accept, so every mismatch must fail closed:
a stale HAR, a swapped evidence file, or a different teaching class must never
produce a writable observation.
"""
import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from core.course_models import CourseTarget
from core.ncwu_har_form_evidence import (
    FormEvidenceError, RawSubmitForm, build_form_observation, cross_check,
    extract_submit_form, load_har, load_items_binding, prepare_from_files,
)
from core.ncwu_live_elective import ORIGIN
from core.ncwu_submission_transport import SUBMIT_PATH
from core.ncwu_verified_form_provider import CurrentS202FormObservation
from core.ncwu_verified_readback_bridge import ReadbackScope

ITEMS = "3141592653589"
HID = "9f2c31ab77"
STUDENT = "20260001"
ROUND = "S202"


def sha(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def make_scope(**overrides) -> ReadbackScope:
    values = dict(
        account_scope="acct-demo",
        semester="2026-2027-1",
        school_origin=ORIGIN,
        internal_student_id=STUDENT,
        academic_year="2026",
        academic_term="1",
        round_id=ROUND,
        session_proof="proof-demo",
    )
    values.update(overrides)
    return ReadbackScope(**values)


def make_target(**overrides) -> CourseTarget:
    values = dict(key="A", course_id="K0012345", class_id=ITEMS,
                  semester="2026-2027-1")
    values.update(overrides)
    return CourseTarget(**values)


def make_evidence(**overrides) -> dict:
    payload = {
        "version": 1,
        "gate": "official_write_verified",
        "captured_at": "2026-11-23T08:00:03+08:00",
        "source": {
            "har_sha256": "0" * 64,
            "catalog_sha256": "1" * 64,
            "origin": ORIGIN,
            "path": SUBMIT_PATH,
            "referer_present": True,
            "content_type": "application/x-www-form-urlencoded; charset=GBK",
            "http_status": 200,
        },
        "form": {
            "field_order": ["xh", "xn", "xq", "lcid", "xktype", "hidOption", "items"],
            "required_present": ["hidOption", "items", "lcid", "xh", "xktype", "xn", "xq"],
            "xktype": "2",
            "academic_year": "2026",
            "academic_term": "1",
            "round_id": ROUND,
            "hid_option_len": len(HID),
            "hid_option_sha256": sha(HID),
            "student_id_sha256": sha(STUDENT),
            "student_id_tail": "0001",
        },
        "items": {
            "value_sha256": sha(ITEMS),
            "length": len(ITEMS),
            "contains_pipe": False,
            "shape": "d",
        },
        "bound_teaching_class": {
            "course_id": "K0012345",
            "class_id": ITEMS,
            "skbh": "(1)",
            "skbjdm": ITEMS,
            "matched_field": "skbjdm",
            "match_strength": "high",
            "matched_token": ITEMS,
            "remain": 3,
            "name": "示例课程",
        },
        "mapping_proven": True,
    }
    payload.update(overrides)
    return payload


def make_har(*, items=ITEMS, hid=HID, student=STUDENT, round_id=ROUND,
             xktype="2", status=200, origin=ORIGIN, extra_entries=()) -> dict:
    entry = {
        "startedDateTime": "2026-11-23T08:00:03.512+08:00",
        "request": {
            "method": "POST",
            "url": origin + SUBMIT_PATH,
            "headers": [{"name": "Content-Type",
                         "value": "application/x-www-form-urlencoded; charset=GBK"}],
            "postData": {
                "mimeType": "application/x-www-form-urlencoded",
                "text": (f"xh={student}&xn=2026&xq=1&lcid={round_id}"
                         f"&xktype={xktype}&hidOption={hid}&items={items}"),
            },
        },
        "response": {"status": status, "headers": [], "content": {"text": "flag:1"}},
    }
    return {"log": {"version": "1.2", "entries": [entry, *extra_entries]}}


class TempFiles(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def write_json(self, name: str, payload: dict) -> Path:
        path = self.dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path


class HappyPathTests(TempFiles):
    def test_end_to_end_observation(self):
        har = self.write_json("capture.har", make_har())
        binding = self.write_json("items_binding.json", make_evidence())
        observation = prepare_from_files(
            har_path=har, binding_path=binding,
            scope=make_scope(), target=make_target(), observed_monotonic=123.0)
        self.assertIsInstance(observation, CurrentS202FormObservation)
        self.assertEqual(observation.selected_item, ITEMS)
        self.assertEqual(observation.course_code, "K0012345")
        self.assertEqual(observation.internal_class_id, ITEMS)
        self.assertEqual(observation.source_path, SUBMIT_PATH)
        self.assertEqual(dict(observation.fields)["hidOption"], HID)
        self.assertAlmostEqual(observation.observed_monotonic, 123.0)

    def test_binding_evidence_bytes_are_the_file_bytes(self):
        har = self.write_json("capture.har", make_har())
        evidence = make_evidence()
        binding = self.write_json("items_binding.json", evidence)
        observation = prepare_from_files(
            har_path=har, binding_path=binding,
            scope=make_scope(), target=make_target())
        self.assertEqual(observation.binding_evidence, binding.read_bytes())

    def test_repr_does_not_leak_the_form(self):
        har = self.write_json("capture.har", make_har())
        binding = self.write_json("items_binding.json", make_evidence())
        observation = prepare_from_files(
            har_path=har, binding_path=binding,
            scope=make_scope(), target=make_target())
        text = repr(observation)
        self.assertIn("redacted", text)
        self.assertNotIn(ITEMS, text)
        self.assertNotIn(STUDENT, text)

    def test_form_repr_hides_values(self):
        form = extract_submit_form(make_har())
        self.assertNotIn(STUDENT, repr(form))
        self.assertIn("fields=7", repr(form))

    def test_cross_check_summary_is_hashes_only(self):
        form = extract_submit_form(make_har())
        binding = load_items_binding(self.write_json("b.json", make_evidence()))
        summary = cross_check(form, binding, make_scope(), make_target())
        self.assertEqual(summary["items_sha256"], sha(ITEMS))
        self.assertEqual(summary["match_strength"], "high")
        blob = json.dumps(summary, ensure_ascii=False)
        # The item value equals the internal class id in this fixture, so the
        # class id legitimately appears. The secrets must not.
        self.assertNotIn(STUDENT, blob)
        self.assertNotIn(HID, blob)
        self.assertNotIn("hidOption=", blob)


class HarRejectionTests(TempFiles):
    def test_missing_log_raises(self):
        with self.assertRaises(FormEvidenceError):
            extract_submit_form({})

    def test_no_matching_entry_raises(self):
        har = make_har()
        har["log"]["entries"][0]["request"]["url"] = ORIGIN + "/hsjw/other.do"
        with self.assertRaises(FormEvidenceError):
            extract_submit_form(har)

    def test_missing_required_field_raises(self):
        har = make_har()
        har["log"]["entries"][0]["request"]["postData"]["text"] = \
            "xh=20260001&xn=2026&xq=1&lcid=S202&xktype=2&items=1"
        with self.assertRaises(FormEvidenceError):
            extract_submit_form(har)

    def test_non_s202_xktype_raises(self):
        with self.assertRaises(FormEvidenceError):
            extract_submit_form(make_har(xktype="1"))

    def test_two_distinct_samples_are_ambiguous(self):
        first = make_har()["log"]["entries"][0]
        second = json.loads(json.dumps(first))
        second["request"]["postData"]["text"] = second["request"]["postData"]["text"] \
            .replace(ITEMS, "9999999999999")
        with self.assertRaises(FormEvidenceError):
            extract_submit_form({"log": {"entries": [first, second]}})

    def test_identical_duplicate_samples_are_accepted(self):
        first = make_har()["log"]["entries"][0]
        duplicate = json.loads(json.dumps(first))
        form = extract_submit_form({"log": {"entries": [first, duplicate]}})
        self.assertEqual(form.items, ITEMS)

    def test_params_form_is_supported(self):
        entry = make_har()["log"]["entries"][0]
        text = entry["request"]["postData"].pop("text")
        entry["request"]["postData"]["params"] = [
            {"name": name, "value": value}
            for name, _, value in (part.partition("=") for part in text.split("&"))
        ]
        form = extract_submit_form({"log": {"entries": [entry]}})
        self.assertEqual(form.field("hidOption"), HID)


class BindingRejectionTests(TempFiles):
    def load(self, **overrides):
        return load_items_binding(self.write_json("e.json", make_evidence(**overrides)))

    def test_not_proven_rejected(self):
        with self.assertRaises(FormEvidenceError):
            self.load(mapping_proven=False)

    def test_wrong_gate_rejected(self):
        with self.assertRaises(FormEvidenceError):
            self.load(gate="something_else")

    def test_unsupported_version_rejected(self):
        with self.assertRaises(FormEvidenceError):
            self.load(version=2)

    def test_low_strength_binding_rejected(self):
        evidence = make_evidence()
        evidence["bound_teaching_class"]["match_strength"] = "low"
        with self.assertRaises(FormEvidenceError):
            load_items_binding(self.write_json("e.json", evidence))

    def test_multi_course_items_rejected(self):
        evidence = make_evidence()
        evidence["items"]["contains_pipe"] = True
        with self.assertRaises(FormEvidenceError):
            load_items_binding(self.write_json("e.json", evidence))

    def test_bad_hash_rejected(self):
        evidence = make_evidence()
        evidence["items"]["value_sha256"] = "not-a-hash"
        with self.assertRaises(FormEvidenceError):
            load_items_binding(self.write_json("e.json", evidence))

    def test_non_s202_evidence_rejected(self):
        evidence = make_evidence()
        evidence["form"]["xktype"] = "1"
        with self.assertRaises(FormEvidenceError):
            load_items_binding(self.write_json("e.json", evidence))

    def test_missing_file_raises(self):
        with self.assertRaises(FormEvidenceError):
            load_items_binding(self.dir / "absent.json")

    def test_invalid_json_raises(self):
        path = self.dir / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(FormEvidenceError):
            load_items_binding(path)

    def test_digest_is_sha256_of_raw_bytes(self):
        path = self.write_json("e.json", make_evidence())
        binding = load_items_binding(path)
        self.assertEqual(binding.digest, sha256(path.read_bytes()).hexdigest())


class CrossCheckTests(TempFiles):
    def binding(self, **overrides) -> object:
        return load_items_binding(self.write_json("e.json", make_evidence(**overrides)))

    def test_items_mismatch_rejected(self):
        with self.assertRaises(FormEvidenceError):
            cross_check(extract_submit_form(make_har(items="7777777777777")),
                        self.binding(), make_scope(), make_target())

    def test_hid_option_mismatch_rejected(self):
        with self.assertRaises(FormEvidenceError):
            cross_check(extract_submit_form(make_har(hid="deadbeef00")),
                        self.binding(), make_scope(), make_target())

    def test_student_mismatch_rejected(self):
        with self.assertRaises(FormEvidenceError):
            cross_check(extract_submit_form(make_har(student="20269999")),
                        self.binding(), make_scope(), make_target())

    def test_round_mismatch_against_live_scope_rejected(self):
        with self.assertRaises(FormEvidenceError):
            cross_check(extract_submit_form(make_har()),
                        self.binding(), make_scope(round_id="S203"), make_target())

    def test_target_course_mismatch_rejected(self):
        with self.assertRaises(FormEvidenceError):
            cross_check(extract_submit_form(make_har()), self.binding(),
                        make_scope(), make_target(course_id="K9999999"))

    def test_target_class_mismatch_rejected(self):
        with self.assertRaises(FormEvidenceError):
            cross_check(extract_submit_form(make_har()), self.binding(),
                        make_scope(), make_target(class_id="0000000000001"))

    def test_foreign_origin_rejected(self):
        har = make_har(origin="https://evil.example.com")
        with self.assertRaises(FormEvidenceError):
            cross_check(extract_submit_form(har), self.binding(),
                        make_scope(), make_target())

    def test_evidence_from_a_different_capture_rejected(self):
        """Swapping in another capture's evidence must break the item hash."""
        other = make_evidence()
        other["items"] = {"value_sha256": sha("1111111111"), "length": 10,
                          "contains_pipe": False, "shape": "d"}
        binding = load_items_binding(self.write_json("other.json", other))
        with self.assertRaises(FormEvidenceError):
            cross_check(extract_submit_form(make_har()), binding,
                        make_scope(), make_target())

    def test_semester_mismatch_rejected(self):
        with self.assertRaises(FormEvidenceError):
            cross_check(extract_submit_form(make_har()), self.binding(),
                        make_scope(), make_target(semester="2025-2026-2"))


class HarFileTests(TempFiles):
    def test_load_har_reads_json(self):
        path = self.write_json("c.har", make_har())
        self.assertIn("log", load_har(path))

    def test_missing_har_raises(self):
        with self.assertRaises(FormEvidenceError):
            load_har(self.dir / "absent.har")

    def test_broken_har_raises(self):
        path = self.dir / "broken.har"
        path.write_text("{oops", encoding="utf-8")
        with self.assertRaises(FormEvidenceError):
            load_har(path)


class RawFormTests(unittest.TestCase):
    def test_field_lookup_returns_empty_string(self):
        form = RawSubmitForm(fields=(("a", "1"),), status=200,
                             source_path=SUBMIT_PATH, origin=ORIGIN)
        self.assertEqual(form.field("a"), "1")
        self.assertEqual(form.field("missing"), "")


if __name__ == "__main__":
    unittest.main()
