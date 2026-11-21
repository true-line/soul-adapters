"""Run the signed-out ACP control on a fresh, credential-free Apple Silicon macOS guest."""

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
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


def member_inputs(environ):
    # Only the Souls helper receives the per-launch bootstrap. The stock
    # adapter and its CLI keep their ordinary credential-free environment.
    names = ("AGENT_BOT_SOULS_SOCKET", "AGENT_BOT_SOULS_BROKER", "AGENT_BOT_SOULS_MEMBER_TOKEN")
    values = {name: environ.get(name) for name in names}
    if not all(values.values()):
        raise ValueError("run the member check inside an assigned test member")
    member, broker = (Path(values[name]) for name in names[:2])
    if not all(p.is_absolute() and stat.S_ISSOCK(p.lstat().st_mode) for p in (member, broker)) or member.samefile(broker):
        raise ValueError("expected two distinct assigned Unix sockets")
    if environ.get("AGENT_BOT_SOUL_CONTRACTS"):
        values["AGENT_BOT_SOUL_CONTRACTS"] = environ["AGENT_BOT_SOUL_CONTRACTS"]
    return member, broker, values


def verify_member(command, policy, cwd, env, bootstrap):
    if not all(p.is_absolute() and p.is_file() for p in command):
        raise ValueError("use absolute paths to the installed test Node and Souls CLI")
    checked = subprocess.run([*policy, *command, "member", "runtime"], cwd=cwd,
                             env={**env, **bootstrap}, stdin=subprocess.DEVNULL,
                             capture_output=True, timeout=15)
    if checked.returncode:
        raise ValueError("the assigned Souls member runtime check failed")
    runtime = json.loads(checked.stdout)
    if (not isinstance(runtime, dict) or not isinstance(runtime.get("runtime_id"), str)
            or not runtime["runtime_id"] or type(runtime.get("installation_epoch")) is not int
            or runtime["installation_epoch"] < 1 or type(runtime.get("lease_generation")) is not int
            or runtime["lease_generation"] < 1 or "channel_binding" in runtime):
        raise ValueError("the member runtime check returned unexpected public metadata")
    return {key: runtime[key] for key in ("runtime_id", "installation_epoch", "lease_generation")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="pinned v0.109.0 Darwin arm64 release zip")
    parser.add_argument("--machine-profile", type=Path, help="pinned Machine harness-egress.sb")
    parser.add_argument("--member-cli", nargs=2, type=Path, metavar=("NODE", "SOULS_CLI"),
                        help="verify an already launched test member using its public Souls command")
    args = parser.parse_args()
    if args.member_cli and not args.machine_profile:
        parser.error("--member-cli requires --machine-profile")
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
            member, broker, bootstrap = (member_inputs(os.environ) if args.member_cli
                                         else (root / "member.sock", root / "broker.sock", {}))
            offline = ["/usr/bin/sandbox-exec", "-D", f"BROKER_SOCKET={broker}",
                       "-D", f"MEMBER_SOCKET={member}", "-f", str(profile)]
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
        if args.member_cli:
            observed = verify_member(args.member_cli, offline, cwd, env, bootstrap)
        check_acp(offline + [str(root / "claude-agent-acp")], cwd, env, "member-temp")
    if args.member_cli:
        print("PASS: the public Souls member runtime command authenticated on the assigned endpoints")
        print(json.dumps(observed))
    policy = "Machine two-socket profile" if args.machine_profile else "offline guest profile"
    print(f"PASS: signed-out ACP v1 initialize, session/new and clean EOF under the {policy}")


if __name__ == "__main__":
    main()
