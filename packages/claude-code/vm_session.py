"""Run the signed-out ACP control on a fresh, credential-free Apple Silicon macOS guest."""

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile

from initialize import check_acp, private_state, unpack, verify


# agent-bot-machine e2537d299cc677a2c29a6e7719036d103048dcc2
PROFILE_SHA256 = "717fd0fa41cf25ac36cc786c357c60814d90e19ec47497614e3ca2adf65f8f86"


def require_guest():
    if (platform.system() != "Darwin" or platform.machine() != "arm64"
            or os.getuid() == 0 or 80 in os.getgroups()):
        raise SystemExit("use a standard account in a disposable Apple Silicon macOS guest")
    model = subprocess.check_output(["/usr/sbin/sysctl", "-n", "hw.model"], timeout=5)
    if not re.fullmatch(rb"VirtualMac[0-9]+,[0-9]+", model.strip()):
        raise SystemExit("requires Apple virtual hardware")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="pinned v0.109.0 Darwin arm64 release zip")
    parser.add_argument("--machine-profile", type=Path, help="pinned Machine harness-egress.sb")
    args = parser.parse_args()
    require_guest()
    os.umask(0o077)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp).resolve()
        unpack(args.archive, root)
        _, cwd, env = private_state(root, "member-temp")
        offline = ["/usr/bin/sandbox-exec", "-p", "(version 1) (allow default) (deny network*)"]
        if args.machine_profile is not None:
            profile = root / "harness-egress.sb"
            shutil.copyfile(args.machine_profile, profile)
            verify(profile, PROFILE_SHA256)
            offline = ["/usr/bin/sandbox-exec", "-D", f"BROKER_SOCKET={root}/broker.sock",
                       "-D", f"MEMBER_SOCKET={root}/member.sock", "-f", str(profile)]
        socket_check = ("import errno,socket,sys; s=socket.socket(); "
                        "sys.exit(0 if s.connect_ex(('127.0.0.1', 9)) in (errno.EPERM, errno.EACCES) else 1)")
        subprocess.run(offline + ["/usr/bin/python3", "-IS", "-c", socket_check],
                       cwd=cwd, env=env, check=True, timeout=10)
        status = subprocess.run(offline + [str(root / "claude"), "auth", "status"],
                                cwd=cwd, env=env, capture_output=True, timeout=15)
        if status.returncode != 1:
            raise SystemExit("expected a signed-out Claude CLI")
        public_state = json.loads(status.stdout)
        if public_state.get("loggedIn") is not False or public_state.get("authMethod") != "none":
            raise SystemExit("expected a signed-out Claude CLI")
        check_acp(offline + [str(root / "claude-agent-acp")], cwd, env, "member-temp")
    policy = "Machine two-socket profile" if args.machine_profile else "offline guest profile"
    print(f"PASS: signed-out ACP v1 initialize, session/new and clean EOF under the {policy}")


if __name__ == "__main__":
    main()
