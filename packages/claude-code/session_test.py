"""The offline probe implements the entry point's session/new response handling."""

import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from initialize import check_session, SessionRefused


def child_with(*frames):
    stream = b"".join(json.dumps({"jsonrpc": "2.0", **frame}).encode() + b"\n" for frame in frames)
    return SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(stream))


class SessionProbe(unittest.TestCase):
    def test_existing_client_semantics(self):
        child = child_with(
            {"method": "session/update", "params": {}},
            {"id": 4, "method": "session/request_permission"},
            {"id": 5, "method": "unimplemented"},
            {"id": 2, "result": {"sessionId": "fixture-session"}},
        )
        check_session(child, Path("/fixture/member"))
        request, cancelled, unavailable = map(json.loads, child.stdin.getvalue().splitlines())
        self.assertEqual(request["method"], "session/new")
        self.assertEqual(request["params"], {"cwd": "/fixture/member", "mcpServers": []})
        self.assertEqual(cancelled["result"], {"outcome": {"outcome": "cancelled"}})
        self.assertEqual(unavailable["error"]["code"], -32601)

    def test_refusal_is_sanitized(self):
        child = child_with({"id": 2, "error": {"code": -32603, "message": "EACCES, mkdir '/tmp/claude-123/fixture'"} })
        with self.assertRaisesRegex(SessionRefused, r"^session/new: JSON-RPC refusal -32603 \(mkdir outside member-private temp\)$"):
            check_session(child, Path("/fixture/member"))

    def test_missing_session(self):
        for response in [{"id": 1, "result": {}}, {"id": 2, "result": {}}, {"method": "session/update"}]:
            with self.subTest(response=response), self.assertRaises(SessionRefused):
                check_session(child_with(response), Path("/fixture/member"))


if __name__ == "__main__":
    unittest.main()
