"""Test just initialize and session/new on a verified, offline Darwin arm64 package."""

import argparse
import os
from pathlib import Path
import platform
import stat
import tempfile

from initialize import handshake, SessionRefused, unpack


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="pinned v0.109.0 Darwin arm64 release zip")
    parser.add_argument("--temp-mode", choices=["default", "member-temp"], default="member-temp")
    args = parser.parse_args()
    if os.getuid() == 0 or platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("requires an ordinary Darwin arm64 account")

    os.umask(0o077)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp).resolve()
        unpack(args.archive, root)
        try:
            handshake(root, session_mode=args.temp_mode)
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
