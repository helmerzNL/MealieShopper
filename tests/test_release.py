import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import release_notes  # noqa: E402


class ReleaseTest(unittest.TestCase):
    def test_version_file_is_plain_semver(self):
        self.assertRegex((ROOT / "VERSION").read_text(encoding="utf-8").strip(), r"^\d+\.\d+\.\d+$")

    def test_current_version_has_release_notes(self):
        version = release_notes.current_version()
        notes = release_notes.notes_for(version)
        self.assertTrue(notes, f"CHANGELOG.md mist een sectie '## {version}': elke versie krijgt een GitHub release")
        self.assertGreater(len(notes), 200)

    def test_changelog_lists_versions_newest_first(self):
        versions = re.findall(r"^## v(\d+)\.(\d+)\.(\d+)\s*$", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
        numbers = [tuple(int(part) for part in version) for version in versions]
        self.assertEqual(numbers, sorted(numbers, reverse=True))
        self.assertEqual(numbers[0], tuple(int(p) for p in release_notes.current_version()[1:].split(".")))

    def test_section_ends_at_the_next_heading(self):
        notes = release_notes.notes_for("v1.2.0")
        self.assertNotIn("## v1.0.0", notes)
        self.assertNotIn("eerste genummerde versie", notes)

    def test_release_workflow_builds_the_release_from_the_changelog(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("gh release create", workflow)
        self.assertIn("release_notes.py", workflow)
        self.assertIn("contents: write", workflow)

    def test_release_rule_is_written_down(self):
        for name in ("CLAUDE.md", "CHANGELOG.md"):
            self.assertIn("GitHub release", (ROOT / name).read_text(encoding="utf-8"), name)


if __name__ == "__main__":
    unittest.main()
