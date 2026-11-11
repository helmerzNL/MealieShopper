import re
import sys
import tempfile
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

    def test_every_block_has_at_most_one_english_summary(self):
        # Komt het kopje van een nieuwe versie midden in de sectie van de vorige terecht (een merge die misging, zoals
        # 4.10.1 in #300), dan blijft die sectie leeg en staat haar tekst onder de nieuwe versie, en daarmee in de
        # releasetekst van een versie die hem niet bracht. Te zien aan twee Engelse samenvattingen in één blok.
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        for section in re.split(r"^## (?=v\d+\.\d+\.\d+\s*$)", text, flags=re.M)[1:]:
            version = section.split("\n", 1)[0].strip()
            for block in re.split(r"^#{3,4} ", section, flags=re.M):
                count = len(re.findall(r"^\*English summary:", block, re.M))
                self.assertLessEqual(count, 1, f"{version}: {count} Engelse samenvattingen in één blok: {block.split(chr(10), 1)[0][:60]}")

    def test_section_ends_at_the_next_heading(self):
        notes = release_notes.notes_for("v1.2.0")
        self.assertNotIn("## v1.0.0", notes)
        self.assertNotIn("eerste genummerde versie", notes)

    def _changelog(self, text):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "CHANGELOG.md"
        path.write_text(text, encoding="utf-8")
        return path

    SAMPLE = (
        "# Changelog\n\nIntro.\n\n"
        "## v2.10.0\n\nTien.\n\n"
        "## v2.9.1\n\nNegen-een.\n\n"
        "## v2.9.0\n\nNegen.\n\n"
        "## v2.8.0\n\nAcht.\n"
    )

    def test_stable_release_collects_every_version_since_the_previous_one(self):
        changelog = self._changelog(self.SAMPLE)
        self.assertEqual(release_notes.versions_since("v2.10.0", "v2.8.0", changelog), ["v2.10.0", "v2.9.1", "v2.9.0"])
        notes = release_notes.notes_since("v2.10.0", "v2.8.0", changelog)
        for heading, body in (("## v2.10.0", "Tien."), ("## v2.9.1", "Negen-een."), ("## v2.9.0", "Negen.")):
            self.assertIn(f"{heading}\n\n{body}", notes)
        self.assertNotIn("## v2.8.0", notes)
        self.assertNotIn("Acht.", notes)
        self.assertLess(notes.index("## v2.10.0"), notes.index("## v2.9.0"))
        self.assertTrue(notes.startswith("Alles sinds de vorige stabiele versie v2.8.0: v2.9.0, v2.9.1, v2.10.0."))

    def test_single_version_or_no_previous_release_gives_just_that_section(self):
        changelog = self._changelog(self.SAMPLE)
        own = release_notes.notes_for("v2.10.0", changelog)
        self.assertEqual(own, "Tien.")
        self.assertEqual(release_notes.notes_since("v2.10.0", "", changelog), own)
        self.assertEqual(release_notes.notes_since("v2.10.0", "v2.9.1", changelog), own)
        self.assertEqual(release_notes.notes_since("v3.0.0", "v2.8.0", changelog), "")

    def test_versions_compare_as_numbers(self):
        changelog = self._changelog(self.SAMPLE)
        self.assertEqual(release_notes.versions_since("v2.10.0", "v2.9.0", changelog), ["v2.10.0", "v2.9.1"])

    def test_release_workflow_builds_the_release_from_the_changelog(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("gh release create", workflow)
        self.assertIn("release_notes.py", workflow)
        self.assertIn("contents: write", workflow)
        # De stabiele release krijgt alle versies sinds de vorige stabiele release.
        self.assertIn('--since "$previous"', workflow)
        self.assertIn("gh release view --json tagName", workflow)

    def test_beta_builds_get_their_own_tags_and_a_prerelease(self):
        docker = (ROOT / ".github" / "workflows" / "docker.yml").read_text(encoding="utf-8")
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        for workflow in (docker, release):
            self.assertIn("- beta", workflow)
        self.assertIn("type=raw,value=latest,enable=${{ github.ref == 'refs/heads/main' }}", docker)
        self.assertIn("type=raw,value=beta,enable=${{ github.ref == 'refs/heads/beta' }}", docker)
        self.assertIn("--prerelease", release)

    def test_only_beta_may_open_a_pull_request_to_main(self):
        guard = (ROOT / ".github" / "workflows" / "pr-target.yml").read_text(encoding="utf-8")
        self.assertIn("- main", guard)
        self.assertIn('"$HEAD_REF" != "beta"', guard)

    def test_a_dependabot_pull_request_to_main_moves_to_beta_instead_of_failing(self):
        # Beveiligingsupdates van Dependabot gaan altijd naar de standaardbranch (main); de check zet ze over naar beta.
        text = (ROOT / ".github" / "workflows" / "pr-target.yml").read_text(encoding="utf-8")
        steps = {block.split("\n", 1)[0].strip(): block for block in text.split("      - name: ")[1:]}
        move = steps["Move a Dependabot pull request to beta"]
        guard = steps["Require beta as the source branch"]
        self.assertIn("pull-requests: write", text)
        self.assertIn("id: dependabot", move)
        self.assertIn("github.event.pull_request.user.login == 'dependabot[bot]'", move)
        self.assertIn("head.repo.full_name == github.repository", move)  # geen pull request uit een fork
        self.assertIn("-f base=beta", move)
        self.assertIn("git apply --check -R -C0", move)  # staat de update al in beta, dan sluiten
        self.assertIn("-f state=closed", move)
        # De gewone check valt alleen weg als het verzetten of sluiten lukte; anders faalt hij zoals altijd.
        self.assertIn("if: steps.dependabot.outcome != 'success'", guard)
        self.assertIn('"$HEAD_REF" != "beta"', guard)

    def test_branch_rule_is_written_down(self):
        text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("rechtstreeks naar `main`", text)
        self.assertIn("`beta`", text)

    def test_release_rule_is_written_down(self):
        for name in ("CLAUDE.md", "CHANGELOG.md"):
            self.assertIn("GitHub release", (ROOT / name).read_text(encoding="utf-8"), name)


if __name__ == "__main__":
    unittest.main()
