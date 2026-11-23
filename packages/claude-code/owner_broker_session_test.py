"""Protocol checks for the disposable HQ owner ACP probe."""

import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import owner_broker_session as probe


def child_with(*frames):
    stdout = io.BytesIO(b"".join(json.dumps({"jsonrpc": "2.0", **frame}).encode() + b"\n"
                              for frame in frames))
    return SimpleNamespace(stdin=io.BytesIO(), stdout=stdout)


class OwnerBrokerProtocol(unittest.TestCase):
    def test_stock_session_and_provider_turn(self):
        child = child_with(
            {"id": 1, "result": {"protocolVersion": 1,
                                "agentInfo": {"name": probe.PACKAGE, "version": probe.VERSION}}},
            {"method": "auth/status_update", "params": {"state": {}}},
            {"id": 2, "result": {"sessionId": "fixture-session"}},
            {"method": "session/update", "params": {"sessionId": "fixture-session",
              "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "pin"}}}},
            {"method": "session/update", "params": {"sessionId": "fixture-session",
              "update": {"sessionUpdate": "usage_update"}}},
            {"id": 3, "result": {"stopReason": "end_turn"}},
        )
        result = probe.run_protocol(child, Path("/private/member/work"), "fixture prompt", "pin")
        requests = [json.loads(line) for line in child.stdin.getvalue().splitlines()]
        self.assertEqual([item["method"] for item in requests],
                         ["initialize", "session/new", "session/prompt"])
        self.assertEqual(requests[1]["params"], {"cwd": "/private/member/work", "mcpServers": []})
        self.assertEqual(requests[2]["params"], {"sessionId": "fixture-session",
                         "prompt": [{"type": "text", "text": "fixture prompt"}]})
        self.assertEqual(result["stop_reason"], "end_turn")
        self.assertTrue(result["usage_seen"])
        self.assertEqual(result["notification_counts"], {"auth/status_update": 1, "session/update": 2})

    def test_refuses_changed_provider_text(self):
        child = child_with(
            {"id": 1, "result": {"protocolVersion": 1,
                                "agentInfo": {"name": probe.PACKAGE, "version": probe.VERSION}}},
            {"id": 2, "result": {"sessionId": "fixture-session"}},
            {"method": "session/update", "params": {"sessionId": "fixture-session",
              "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "changed"}}}},
            {"method": "session/update", "params": {"sessionId": "fixture-session",
              "update": {"sessionUpdate": "usage_update"}}},
            {"id": 3, "result": {"stopReason": "end_turn"}},
        )
        with self.assertRaisesRegex(ValueError, "differs from the pinned provider fixture"):
            probe.run_protocol(child, Path("/private/member/work"), "fixture prompt", "pin")

    def test_cancels_permission_requests(self):
        child = child_with(
            {"id": 1, "result": {"protocolVersion": 1,
                                "agentInfo": {"name": probe.PACKAGE, "version": probe.VERSION}}},
            {"id": 9, "method": "session/request_permission", "params": {}},
            {"id": 2, "result": {"sessionId": "fixture-session"}},
            {"method": "session/update", "params": {"sessionId": "fixture-session",
              "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "pin"}}}},
            {"method": "session/update", "params": {"sessionId": "fixture-session",
              "update": {"sessionUpdate": "usage_update"}}},
            {"id": 3, "result": {"stopReason": "end_turn"}},
        )
        probe.run_protocol(child, Path("/private/member/work"), "fixture prompt", "pin")
        responses = [json.loads(line) for line in child.stdin.getvalue().splitlines()]
        self.assertEqual(responses[2], {"jsonrpc": "2.0", "id": 9,
                         "result": {"outcome": {"outcome": "cancelled"}}})


    def test_owner_cli_pipe_and_clean_eof(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / "fake_hq.py"
            fake.write_text('''import json, sys
def send(frame):
    print(json.dumps({"jsonrpc": "2.0", **frame}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if request["id"] == 1:
        send({"id": 1, "result": {"protocolVersion": 1, "agentInfo":
              {"name": "@agentclientprotocol/claude-agent-acp", "version": "0.109.0"}}})
    elif request["id"] == 2:
        send({"id": 2, "result": {"sessionId": "local"}})
    elif request["id"] == 3:
        send({"method": "session/update", "params": {"sessionId": "local", "update":
              {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "pinned"}}}})
        send({"method": "session/update", "params": {"sessionId": "local", "update":
              {"sessionUpdate": "usage_update"}}})
        send({"id": 3, "result": {"stopReason": "end_turn"}})
''')
            prompt = root / "prompt.txt"
            expected = root / "expected.txt"
            prompt.write_text("fixture prompt")
            expected.write_text("pinned")
            args = SimpleNamespace(node=Path(sys.executable), hq_main=fake,
                                   package_root=root, cwd=root, prompt_file=prompt,
                                   expected_response_file=expected, turn_timeout=5,
                                   exit_timeout=5)
            with patch.object(probe, "require_guest"):
                result = probe.run(args)
            self.assertEqual(result["outcome"], "pass")
            self.assertEqual(result["response_sha256"], result["expected_response_sha256"])

    def test_owner_start_refusal_code_is_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / "fake_hq.py"
            fake.write_text('import sys\nsys.stderr.write(\'{"code":"host.launch_unavailable"}\\n\')\n')
            prompt = root / "prompt.txt"
            expected = root / "expected.txt"
            prompt.write_text("fixture prompt")
            expected.write_text("pinned")
            args = SimpleNamespace(node=Path(sys.executable), hq_main=fake,
                                   package_root=root, cwd=root, prompt_file=prompt,
                                   expected_response_file=expected, turn_timeout=5,
                                   exit_timeout=5)
            with patch.object(probe, "require_guest"):
                with self.assertRaisesRegex(ValueError, "HQ refusal host.launch_unavailable"):
                    probe.run(args)


if __name__ == "__main__":
    unittest.main()
