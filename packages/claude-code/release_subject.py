"""Validate the unqualified, pinned stock Claude ACP release subject draft.

This is a byte and source preflight, not a qualification verifier. It never
accepts a signed report or changes the adapter manifest.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import zipfile

from initialize import ARCHIVE_SHA, ASSET, BINARIES, PACKAGE, VERSION


HERE = Path(__file__).resolve().parent
SUBJECT = HERE / "release-subject.draft.json"
MANIFEST = HERE / "manifest.json"
UPSTREAM_COMMIT = "9f1ca6eb9c65c2f5d1bbf48335af42947ddb74d6"
UPSTREAM_TREE = "f7d42951a63c708567a4ef4f0f70275c735ed7b6"
SOURCE_FILES = {
    ".github/workflows/binaries.yml": ("workflow_source_sha256", "ac4c50f637ae5193bf7b7a15e3ddf02d4076ddb48e700d15c7cea4c7d653650e"),
    "scripts/build-binary.mjs": ("build_script_sha256", "b6d557070868e3e372c7e63da080168b7def0ea7e43e54ca83f8f9631d126f58"),
    "package-lock.json": ("package_lock_sha256", "df8282614078b4eb944a4a0d44a7adb230dbe607cc14c5821445b5f25e2932d7"),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def git_value(checkout, *args):
    return subprocess.check_output(
        ["git", "-C", str(checkout), *args], stderr=subprocess.DEVNULL,
        text=True, timeout=10,
    ).strip()


def validate(subject, manifest, source_checkout=None, archive=None):
    require(set(subject) == {"schema_version", "status", "adapter", "adjacent_cli",
                             "upstream_source", "adapter_release", "build_provenance",
                             "profiles_qualified"}, "unexpected release subject fields")
    require(subject["schema_version"] == 1 and subject["status"] == "unqualified-draft",
            "release subject must remain a draft")
    require(subject["profiles_qualified"] == [] and manifest.get("profiles_qualified") == [],
            "physical qualification cannot be inferred from this draft")
    adapter = subject["adapter"]
    require(set(adapter) == {"id", "version", "package_name", "release_asset", "package_sha256",
                             "executable", "executable_sha256"}, "unexpected adapter fields")
    require(adapter == {
        "id": manifest["adapter_id"], "version": VERSION, "package_name": PACKAGE,
        "release_asset": ASSET, "package_sha256": "sha256:" + ARCHIVE_SHA,
        "executable": "claude-agent-acp",
        "executable_sha256": "sha256:" + BINARIES["claude-agent-acp"],
    }, "stock ACP archive or executable pin changed")
    cli = subject["adjacent_cli"]
    require(set(cli) == {"name", "version", "binary_sha256", "npm_package",
                         "npm_package_version", "npm_integrity", "source_tree"},
            "unexpected adjacent CLI fields")
    require(cli["name"] == "claude" and cli["version"] == "2.1.328" and
            cli["binary_sha256"] == "sha256:" + BINARIES["claude"] and
            cli["npm_package"] == "@anthropic-ai/claude-agent-sdk-darwin-arm64" and
            cli["npm_package_version"] == "0.3.328" and
            cli["npm_integrity"] == "sha512-vjBASSSZFNs5M84XONCM4xAJMKPD0E9RtF0UDQ/a1Qqt5L+slNxhSADkH5n0tEM5HJMK7j9DCPi+0FVOBpZr3w==" and
            cli["source_tree"] is None, "adjacent CLI pin or source claim changed")
    upstream = subject["upstream_source"]
    require(upstream == {"repository": "agentclientprotocol/claude-agent-acp",
                         "tag": "v" + VERSION, "commit": UPSTREAM_COMMIT,
                         "tree": UPSTREAM_TREE}, "upstream source pin changed")
    release = subject["adapter_release"]
    require(release == {"source_repository": "true-line/soul-adapters", "source_commit": None,
                        "source_tree": None, "workflow": None, "signed_receipt": None},
            "draft cannot assert an adapter release")
    build = subject["build_provenance"]
    require(set(build) == {"workflow", *(entry[0] for entry in SOURCE_FILES.values()), "resolved_node_version",
                           "resolved_bun_version", "builder_sha256", "toolchain_sha256",
                           "signed_build_receipt"}, "unexpected build provenance fields")
    require(build["workflow"] == ".github/workflows/binaries.yml" and
            all(build[key] is None for key in ("resolved_node_version", "resolved_bun_version",
                                               "builder_sha256", "toolchain_sha256",
                                               "signed_build_receipt")),
            "unverified build provenance cannot be asserted")
    for key, expected in SOURCE_FILES.values():
        require(build[key] == "sha256:" + expected, "source file digest pin changed")

    if source_checkout is not None:
        checkout = Path(source_checkout).resolve(strict=True)
        require(git_value(checkout, "status", "--porcelain") == "", "upstream checkout is not clean")
        require(git_value(checkout, "rev-parse", "HEAD") == upstream["commit"] and
                git_value(checkout, "rev-parse", "HEAD^{tree}") == upstream["tree"] and
                git_value(checkout, "rev-parse", "refs/tags/" + upstream["tag"]) == upstream["commit"],
                "upstream tag, commit or tree mismatch")
        package = json.loads((checkout / "package.json").read_text())
        lock = json.loads((checkout / "package-lock.json").read_text())
        cli_package = lock["packages"]["node_modules/" + cli["npm_package"]]
        require(package["name"] == adapter["package_name"] and
                package["version"] == adapter["version"] and
                cli_package["version"] == cli["npm_package_version"] and
                cli_package["integrity"] == cli["npm_integrity"],
                "upstream package or adjacent CLI lock mismatch")
        for source, (key, _) in SOURCE_FILES.items():
            require(sha256_file(checkout / source) == build[key], source + " digest mismatch")

    if archive is not None:
        archive = Path(archive)
        before = archive.lstat()
        require(stat.S_ISREG(before.st_mode), "ACP release archive is not a regular file")
        with archive.open("rb") as raw:
            opened = os.fstat(raw.fileno())
            require((before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino),
                    "ACP release archive changed before inspection")
            def checked_archive_hash():
                raw.seek(0)
                digest = hashlib.sha256()
                for chunk in iter(lambda: raw.read(1024 * 1024), b""):
                    digest.update(chunk)
                require("sha256:" + digest.hexdigest() == adapter["package_sha256"],
                        "ACP release archive digest mismatch")

            checked_archive_hash()
            raw.seek(0)
            with zipfile.ZipFile(raw) as bundle:
                names = bundle.namelist()
                require(sorted(names) == sorted(BINARIES) and len(names) == len(set(names)),
                        "ACP release members changed")
                for name, expected in BINARIES.items():
                    info = bundle.getinfo(name)
                    file_kind = stat.S_IFMT(info.external_attr >> 16)
                    require(not info.is_dir() and file_kind in (0, stat.S_IFREG),
                            "ACP release member is not a regular file")
                    digest = hashlib.sha256()
                    with bundle.open(info) as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(chunk)
                    require(digest.hexdigest() == expected, name + " digest mismatch")
            checked_archive_hash()
            after = archive.lstat()
            require(stat.S_ISREG(after.st_mode) and
                    (after.st_dev, after.st_ino) == (opened.st_dev, opened.st_ino),
                    "ACP release archive path changed during inspection")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-checkout", type=Path)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    try:
        validate(json.loads(SUBJECT.read_text()), json.loads(MANIFEST.read_text()),
                 args.source_checkout, args.archive)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, zipfile.BadZipFile) as error:
        raise SystemExit(f"release subject draft refused: {error}") from None
    checked = ["draft pins and empty qualification manifest"]
    skipped = []
    for supplied, name in ((args.source_checkout is not None, "upstream source checkout"),
                           (args.archive is not None, "release ZIP and both binaries")):
        (checked if supplied else skipped).append(name)
    print("UNQUALIFIED DRAFT: checked " + ", ".join(checked) +
          "; skipped " + (", ".join(skipped) if skipped else "none") +
          "; signed build and physical evidence remain required")


if __name__ == "__main__":
    main()
