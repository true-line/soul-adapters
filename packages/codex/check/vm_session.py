"""Check local Codex session startup in a credential-free Apple Silicon guest."""

import argparse
from functools import partial
import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile

from session import CODEX_VERSION_OUTPUT, PACKAGE, check_signed_out, exchange, initialize, install_packages, private_env, session

# agent-bot-machine e2537d299cc677a2c29a6e7719036d103048dcc2
PROFILE_SHA256 = "717fd0fa41cf25ac36cc786c357c60814d90e19ec47497614e3ca2adf65f8f86"
BASE_URL = "http://127.0.0.1:9/v1"


def require_guest():
    if platform.system() != "Darwin" or platform.machine() != "arm64" or os.getuid() == 0 or 80 in os.getgroups():
        raise SystemExit("requires a standard account in a disposable Apple Silicon macOS guest")
    model = subprocess.check_output(["/usr/sbin/sysctl", "-n", "hw.model"], timeout=5)
    if not re.fullmatch(rb"VirtualMac[0-9]+,[0-9]+", model.strip()):
        raise SystemExit("requires Apple virtual hardware")


def check_local_session(child, wire, state, *, gateway):
    initialize(child, wire, gateway=gateway)
    if gateway:
        method = "authenticate" if wire == 1 else "auth/login"
        params = {"methodId": "gateway", "_meta": {"gateway": {"baseUrl": BASE_URL, "headers": {}}}}
        login = exchange(child, 3, method, params)
        if set(login) != {"jsonrpc", "id", "result"}:
            raise ValueError("custom gateway configuration refused")
    reply = exchange(child, 2, "session/new", {"cwd": str(state), "mcpServers": []})
    session_id = reply.get("result", {}).get("sessionId")
    if set(reply) != {"jsonrpc", "id", "result"} or not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("no local session ID returned")
    if gateway:
        listing = exchange(child, 4, "providers/list", {})
        providers = listing.get("result", {}).get("providers")
        expected = {"apiType": "openai", "baseUrl": BASE_URL}
        if (not isinstance(providers, list) or len(providers) != 1 or not isinstance(providers[0], dict)
                or providers[0].get("providerId") != "openai" or providers[0].get("current") != expected):
            raise ValueError("Codex did not report the selected fixture gateway")


def check(root, packages, node, profile):
    cli = packages / "@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex"
    adapter = packages / PACKAGE / "dist/index.js"
    sandbox = ["/usr/bin/sandbox-exec", "-D", f"BROKER_SOCKET={root}/broker.sock",
               "-D", f"MEMBER_SOCKET={root}/member.sock", "-f", str(profile)]
    for mode in ("default", "provider", "gateway"):
        for wire in (1, 2):
            state = root / f"{mode}-{wire}"
            state.mkdir(mode=0o700)
            env = private_env(state, cli)
            if mode == "provider":
                (Path(env["CODEX_HOME"]) / "config.toml").write_text(
                    'model_provider = "local"\nmodel = "offline-fixture"\n'
                    '[model_providers.local]\nname = "Local fixture"\n'
                    f'base_url = "{BASE_URL}"\nwire_api = "responses"\nrequires_openai_auth = false\n')
            if mode == "default" and wire == 1:
                control = ("import errno,socket,sys; s=socket.socket(); "
                           "sys.exit(0 if s.connect_ex(('127.0.0.1',9)) in (errno.EPERM,errno.EACCES) else 1)")
                subprocess.run(sandbox + ["/usr/bin/python3", "-IS", "-c", control],
                               cwd=state, env=env, check=True, timeout=10)
                version = subprocess.check_output(sandbox + [str(cli), "--version"], cwd=state, env=env, timeout=15)
                if version.strip() != CODEX_VERSION_OUTPUT:
                    raise ValueError("unexpected Codex CLI version")
            handshake = check_signed_out if mode == "default" else partial(check_local_session, gateway=mode == "gateway")
            session(sandbox + [str(node), str(adapter)], env, state, wire, handshake)
            outcome = "authentication required" if mode == "default" else "local session ID returned"
            if mode == "gateway":
                outcome += ", selected fixture gateway confirmed"
            print(f"PASS ACP v{wire} {mode}: {outcome}, clean EOF")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path, help="pinned Machine harness-egress.sb")
    parser.add_argument("npm_cache", type=Path, help="offline npm cache populated from this check's lockfile")
    args = parser.parse_args()
    require_guest()
    policy = args.profile.read_bytes()
    if hashlib.sha256(policy).hexdigest() != PROFILE_SHA256:
        raise SystemExit("unexpected Machine harness profile")
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        raise SystemExit("put standalone Node.js and npm on PATH")
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix="codex-provider-", dir="/private/tmp") as temporary:
        root = Path(temporary)
        profile = root / "harness-egress.sb"
        profile.write_bytes(policy)
        packages = install_packages(root, node, npm, args.npm_cache.resolve())
        check(root, packages, Path(node).resolve(), profile)


if __name__ == "__main__":
    main()
