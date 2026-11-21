"""Test just initialize and session/new on a verified, offline Darwin arm64 package."""

import argparse
import os
from pathlib import Path
import platform
import stat
import sys
import tempfile

if __name__ == "__main__":
    sys.dont_write_bytecode = True

from initialize import BINARIES, handshake, SessionRefused, unpack, verify


def staged_bundle(directory):
    bundle = directory.resolve(strict=True)
    if not bundle.is_dir():
        raise ValueError("expected a staged bundle directory")
    for name, digest in BINARIES.items():
        binary = bundle / name
        try:
            mode = binary.lstat().st_mode
        except FileNotFoundError:
            raise ValueError(f"missing staged release member: {name}") from None
        if not stat.S_ISREG(mode) or not os.access(binary, os.X_OK):
            raise ValueError(f"staged release member must be a regular executable: {name}")
        verify(binary, digest)
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("archive", type=Path, nargs="?", help="pinned v0.109.0 Darwin arm64 release zip")
    source.add_argument("--staged-bundle", type=Path, help="directory with both pinned release executables")
    parser.add_argument("--temp-mode", choices=["default", "member-temp"], default="member-temp")
    args = parser.parse_args()
    if os.getuid() == 0 or platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("requires an ordinary Darwin arm64 account")

    bundle = None
    if args.staged_bundle is not None:
        try:
            bundle = staged_bundle(args.staged_bundle)
        except (OSError, ValueError) as error:
            raise SystemExit(str(error)) from None
    os.umask(0o077)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp).resolve()
        if args.archive is not None:
            unpack(args.archive, root)
        try:
            handshake(root, session_mode=args.temp_mode, bundle=bundle)
        except (SessionRefused, TimeoutError) as error:
            raise SystemExit(str(error)) from None
        finally:
            internal_temp = root / "state" / "tmpdir" / f"claude-{os.getuid()}"
            try:
                redirected = stat.S_ISDIR(internal_temp.lstat().st_mode)
            except FileNotFoundError:
                redirected = False
            print("Member-private vendor temp directory observed:", "yes" if redirected else "no")
    print("ACP initialize and session/new passed with the member-private state. EOF exited zero.")


if __name__ == "__main__":
    main()
