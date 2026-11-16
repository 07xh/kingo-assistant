"""Repository-layout contract: the current Windows source is canonical.

The repo root is optional when the Windows source has been copied into an
external development workspace, so standalone source test runs still work.
"""
from pathlib import Path
from unittest import TestCase


class WindowsRepositoryLayoutTests(TestCase):
    def test_only_root_windows_ci_and_live_source_exist(self):
        root = Path(__file__).resolve().parents[4]
        workflow = root / ".github" / "workflows" / "windows-build.yml"
        if not workflow.is_file():
            self.skipTest("Standalone extracted Windows source: no repository root")
        active = root / "clients" / "windows" / "source"
        self.assertTrue((active / "windows_gui.py").is_file())
        self.assertTrue((active / "core" / "ncwu_live_elective.py").is_file())
        self.assertTrue((active / "core" / "ncwu_course_plan.py").is_file())
        self.assertTrue((active / "core" / "ncwu_signed_login.py").is_file())
        self.assertFalse(
            (active / ".github" / "workflows" / "windows-build.yml").exists(),
            "An inert nested Actions file must not be treated as current CI",
        )
        self.assertFalse(
            (root / "clients" / "windows" / "kingo-windows-v4.6.tar.xz").exists(),
            "Historical bootstrap archive must not replace live source",
        )
        self.assertIn(
            "working-directory: clients/windows/source",
            workflow.read_text(encoding="utf-8"),
        )

    def test_packaged_fictional_demo_is_still_in_source(self):
        source = Path(__file__).resolve().parents[1]
        for name in (
            "offline-config.yaml",
            "snapshot-01-full.html",
            "snapshot-02-available.html",
            "snapshot-03-full-again.html",
        ):
            self.assertTrue(
                (source / "demo" / name).is_file(),
                f"Packaged self-test still requires demo/{name}",
            )


if __name__ == "__main__":
    import unittest
    unittest.main()
