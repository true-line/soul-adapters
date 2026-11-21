"""Run a cooperative Soul front-door turn in a disposable Apple Silicon Mac guest."""

import argparse
import hashlib
import json
import os
import select
import signal
from pathlib import Path
import subprocess
import tarfile
import tempfile
import threading
import time
from contextlib import closing

from loopback_turn import FixtureServer, configured_state
from session import CODEX_VERSION_OUTPUT, install_packages, live_sidecars
from vm_session import require_guest


# Independently prepared and verified inputs, not a claim about installed software.
INPUTS = {
    "hq": "746281207ad2c853257b70d5a2d2b99e23e88c14604589fcab6473e5e609f84c",
    "souls": "13799b84de4d5099a51490553f9b5e8b829f499a7b128ac6666371e33ec93927",
    "conformance": "c9689026339930ee722e690af66fd4e74ece0ef4eaacd4f146c3f7e2c3059f03",
}


def unpack(archive, destination, expected):
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError("fixture archive digest mismatch")
        destination.mkdir(mode=0o700)
        # Extract from the same open file whose exact bytes were verified.
        stream.seek(0)
        with tarfile.open(fileobj=stream) as tar:
            tar.extractall(destination)



def run_driver(command, root, env):
    # Keep the session leader unreaped until its process group is cleaned up,
    # so its PID cannot be reused while descendants are being terminated.
    with closing(select.kqueue()) as events:
        proc = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
        output = {}
        def collect(name, stream):
            output[name] = stream.read()
        readers = [threading.Thread(target=collect, args=(name, stream), daemon=True)
                   for name, stream in (("stdout", proc.stdout), ("stderr", proc.stderr))]
        for reader in readers:
            reader.start()
        timed_out = False
        try:
            watch = select.kevent(proc.pid, filter=select.KQ_FILTER_PROC, flags=select.KQ_EV_ADD,
                                  fflags=select.KQ_NOTE_EXIT)
            events.control([watch], 0, 0)
            if not events.control(None, 1, 90):
                timed_out = True
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                # Darwin may reject a group signal when only the zombie remains.
                pass
            deadline = time.monotonic() + 5
            while live_sidecars(proc.pid):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Soul driver process group survived cleanup")
                time.sleep(0.05)
            code = proc.wait(timeout=5)
            for reader in readers:
                reader.join(timeout=5)
                if reader.is_alive():
                    raise RuntimeError("Soul driver output did not close after cleanup")
        if timed_out:
            raise TimeoutError("Soul front-door integration timed out")
        if code:
            raise ValueError(f"Soul front-door integration failed (exit {code})")
        return json.loads(output["stdout"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hq", type=Path, help="approved packaged HQ tar")
    parser.add_argument("souls", type=Path, help="pinned Soul runtime git archive")
    parser.add_argument("conformance", type=Path, help="pinned conformance git archive")
    parser.add_argument("npm_cache", type=Path, help="offline cache for the locked packages")
    parser.add_argument("runtime", type=Path, help="standalone Node.js directory")
    args = parser.parse_args()
    require_guest()
    os.umask(0o077)
    node, npm = args.runtime.resolve() / "bin/node", args.runtime.resolve() / "bin/npm"
    with tempfile.TemporaryDirectory(prefix="soul-codex-", dir="/private/tmp") as temp:
        root = Path(temp)
        unpack(args.hq, root / "hq-container", INPUTS["hq"])
        unpack(args.souls, root / "souls", INPUTS["souls"])
        unpack(args.conformance, root / "conformance", INPUTS["conformance"])
        packages = install_packages(root, str(node), str(npm), args.npm_cache.resolve())
        cli = packages / "@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex"
        if subprocess.check_output([cli, "--version"], timeout=15).strip() != CODEX_VERSION_OUTPUT:
            raise ValueError("unexpected Codex CLI version")
        server = FixtureServer()
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}/v1"
            _, env = configured_state(root, "client", cli, node, base)
            driver = Path(__file__).with_name("soul_turn.mjs")
            result = run_driver([str(node), str(driver)], root, env)
            if (result.get("stop_reason") != "end_turn" or not result.get("answer_received") or
                    not result.get("required_receipts_present") or not result.get("all_receipts_cooperative")):
                raise ValueError("Soul did not complete a cooperative turn with its required receipts")
            if (len(server.requests) != 1 or server.requests[0].get("path") != "/v1/responses" or
                    server.requests[0].get("method") != "POST" or server.requests[0].get("stream") is not True or
                    server.requests[0].get("fixture_prompt_present") is not True):
                raise ValueError("the literal user prompt did not reach the streaming provider request")
            print("PASS Soul ACP front door: cooperative receipts, real Codex turn and end_turn")
            print("Sanitized request shape:", json.dumps(server.requests[0], sort_keys=True))
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    main()
