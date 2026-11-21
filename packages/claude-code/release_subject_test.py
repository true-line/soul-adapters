"""Refusal tests for the unqualified Claude ACP release subject draft."""

import copy
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import release_subject as subject


class DraftSubjectTests(unittest.TestCase):
    def setUp(self):
        self.draft = json.loads(subject.SUBJECT.read_text())
        self.manifest = json.loads(subject.MANIFEST.read_text())

    def test_pinned_draft_is_valid_but_never_qualified(self):
        subject.validate(self.draft, self.manifest)
        self.assertEqual(self.draft["status"], "unqualified-draft")
        self.assertEqual(self.draft["profiles_qualified"], [])
        self.assertEqual(self.manifest["profiles_qualified"], [])
        self.assertIsNone(self.draft["build_provenance"]["signed_build_receipt"])
        self.assertIsNone(self.draft["adapter_release"]["signed_receipt"])

    def test_changed_release_subject_pins_refuse(self):
        cases = (
            ("archive", lambda d: d["adapter"].__setitem__("package_sha256", "sha256:" + "0" * 64)),
            ("ACP binary", lambda d: d["adapter"].__setitem__("executable_sha256", "sha256:" + "0" * 64)),
            ("adjacent CLI", lambda d: d["adjacent_cli"].__setitem__("binary_sha256", "sha256:" + "0" * 64)),
            ("source tree", lambda d: d["upstream_source"].__setitem__("tree", "0" * 40)),
            ("CLI source claim", lambda d: d["adjacent_cli"].__setitem__("source_tree", "0" * 40)),
            ("workflow", lambda d: d["build_provenance"].__setitem__("workflow_source_sha256", "sha256:" + "0" * 64)),
            ("lock", lambda d: d["build_provenance"].__setitem__("package_lock_sha256", "sha256:" + "0" * 64)),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                changed = copy.deepcopy(self.draft)
                mutate(changed)
                with self.assertRaises(ValueError):
                    subject.validate(changed, self.manifest)

    def test_unsigned_claims_cannot_promote_the_manifest(self):
        cases = (
            ("candidate profile", lambda d: d["profiles_qualified"].append({"profile": "managed-protected"})),
            ("claimed build receipt", lambda d: d["build_provenance"].__setitem__("signed_build_receipt", "fixture:unsigned")),
            ("claimed toolchain", lambda d: d["build_provenance"].__setitem__("toolchain_sha256", "sha256:" + "1" * 64)),
            ("claimed adapter source", lambda d: d["adapter_release"].__setitem__("source_commit", "1" * 40)),
            ("extra attestation", lambda d: d.__setitem__("qualification_attestation", "fixture:unsigned")),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                changed = copy.deepcopy(self.draft)
                mutate(changed)
                with self.assertRaises(ValueError):
                    subject.validate(changed, self.manifest)
        changed_manifest = copy.deepcopy(self.manifest)
        changed_manifest["profiles_qualified"] = [{"profile": "managed-protected"}]
        with self.assertRaisesRegex(ValueError, "physical qualification"):
            subject.validate(self.draft, changed_manifest)

    def test_dirty_source_checkout_refuses_even_with_matching_declared_tree(self):
        with tempfile.TemporaryDirectory() as root, patch.object(subject, "git_value", return_value=" M src/index.ts"):
            with self.assertRaisesRegex(ValueError, "not clean"):
                subject.validate(self.draft, self.manifest, source_checkout=root)

    def test_source_checkout_commit_must_match_the_release_tag(self):
        with tempfile.TemporaryDirectory() as root, patch.object(subject, "git_value", side_effect=["", "0" * 40]):
            with self.assertRaisesRegex(ValueError, "tag, commit or tree mismatch"):
                subject.validate(self.draft, self.manifest, source_checkout=root)

    def test_cli_reports_exactly_which_optional_inputs_were_checked(self):
        cases = (
            ([], ("upstream source checkout", "release ZIP and both binaries"), ()),
            (["--source-checkout", "/fixture/source"], ("release ZIP and both binaries",),
             ("upstream source checkout",)),
            (["--archive", "/fixture/archive.zip"], ("upstream source checkout",),
             ("release ZIP and both binaries",)),
            (["--source-checkout", "/fixture/source", "--archive", "/fixture/archive.zip"], (),
             ("upstream source checkout", "release ZIP and both binaries")),
        )
        for options, skipped, checked in cases:
            with self.subTest(options=options), patch.object(sys, "argv", ["release_subject.py", *options]), \
                 patch.object(subject, "validate") as verify, redirect_stdout(io.StringIO()) as output:
                subject.main()
            verify.assert_called_once()
            message = output.getvalue()
            self.assertIn("UNQUALIFIED DRAFT: checked draft pins and empty qualification manifest", message)
            self.assertIn("; skipped " + (", ".join(skipped) if skipped else "none"), message)
            checked_section = message.split("; skipped ", 1)[0]
            for name in checked:
                self.assertIn(name, checked_section)
            for name in skipped:
                self.assertNotIn(name, checked_section)
            self.assertIn("signed build and physical evidence remain required", message)

    def test_archive_members_and_both_executables_are_measured(self):
        with tempfile.TemporaryDirectory() as root:
            archive = Path(root) / "fixture.zip"
            binaries = {"claude-agent-acp": b"adapter", "claude": b"adjacent CLI"}
            def write_archive(extra=None):
                with zipfile.ZipFile(archive, "w") as bundle:
                    for name, contents in binaries.items():
                        bundle.writestr(name, contents)
                    if extra:
                        bundle.writestr(extra, b"unexpected")
            write_archive()
            fixture_draft = copy.deepcopy(self.draft)
            fixture_draft["adapter"]["package_sha256"] = subject.sha256_file(archive)
            fixture_draft["adapter"]["executable_sha256"] = "sha256:" + hashlib.sha256(binaries["claude-agent-acp"]).hexdigest()
            fixture_draft["adjacent_cli"]["binary_sha256"] = "sha256:" + hashlib.sha256(binaries["claude"]).hexdigest()
            fixture_pins = {name: hashlib.sha256(contents).hexdigest() for name, contents in binaries.items()}
            with patch.object(subject, "ARCHIVE_SHA", fixture_draft["adapter"]["package_sha256"][7:]), \
                 patch.object(subject, "BINARIES", fixture_pins):
                subject.validate(fixture_draft, self.manifest, archive=archive)
                linked_archive = Path(root) / "linked.zip"
                linked_archive.symlink_to(archive)
                with self.assertRaisesRegex(ValueError, "archive is not a regular file"):
                    subject.validate(fixture_draft, self.manifest, archive=linked_archive)
                write_archive("unreviewed")
                fixture_draft["adapter"]["package_sha256"] = subject.sha256_file(archive)
                with patch.object(subject, "ARCHIVE_SHA", fixture_draft["adapter"]["package_sha256"][7:]):
                    with self.assertRaisesRegex(ValueError, "members changed"):
                        subject.validate(fixture_draft, self.manifest, archive=archive)
                binaries["claude"] = b"substituted adjacent CLI"
                write_archive()
                fixture_draft["adapter"]["package_sha256"] = subject.sha256_file(archive)
                with patch.object(subject, "ARCHIVE_SHA", fixture_draft["adapter"]["package_sha256"][7:]):
                    with self.assertRaisesRegex(ValueError, "claude digest mismatch"):
                        subject.validate(fixture_draft, self.manifest, archive=archive)
                with zipfile.ZipFile(archive, "w") as bundle:
                    bundle.writestr("claude-agent-acp", binaries["claude-agent-acp"])
                    linked = zipfile.ZipInfo("claude")
                    linked.external_attr = (stat.S_IFLNK | 0o777) << 16
                    bundle.writestr(linked, b"claude-agent-acp")
                fixture_draft["adapter"]["package_sha256"] = subject.sha256_file(archive)
                with patch.object(subject, "ARCHIVE_SHA", fixture_draft["adapter"]["package_sha256"][7:]):
                    with self.assertRaisesRegex(ValueError, "not a regular file"):
                        subject.validate(fixture_draft, self.manifest, archive=archive)

    def test_archive_path_replacement_during_inspection_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            archive = Path(root) / "fixture.zip"
            binaries = {"claude-agent-acp": b"adapter", "claude": b"adjacent CLI"}
            real_zip = zipfile.ZipFile
            with real_zip(archive, "w") as bundle:
                for name, contents in binaries.items():
                    bundle.writestr(name, contents)
            fixture_draft = copy.deepcopy(self.draft)
            fixture_draft["adapter"]["package_sha256"] = subject.sha256_file(archive)
            fixture_pins = {name: hashlib.sha256(contents).hexdigest() for name, contents in binaries.items()}
            fixture_draft["adapter"]["executable_sha256"] = "sha256:" + fixture_pins["claude-agent-acp"]
            fixture_draft["adjacent_cli"]["binary_sha256"] = "sha256:" + fixture_pins["claude"]

            def swap_after_hash(raw):
                archive.rename(archive.with_suffix(".original"))
                with real_zip(archive, "w") as replacement:
                    replacement.writestr("changed", b"different archive")
                return real_zip(raw)

            with patch.object(subject, "ARCHIVE_SHA", fixture_draft["adapter"]["package_sha256"][7:]), \
                 patch.object(subject, "BINARIES", fixture_pins), \
                 patch.object(subject.zipfile, "ZipFile", side_effect=swap_after_hash):
                with self.assertRaisesRegex(ValueError, "path changed during inspection"):
                    subject.validate(fixture_draft, self.manifest, archive=archive)


if __name__ == "__main__":
    unittest.main()
