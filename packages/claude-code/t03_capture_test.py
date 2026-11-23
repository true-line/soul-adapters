"""Exact-byte and refusal checks for the untrusted T03 stock wire recorder."""

import hashlib
import io
import unittest

from t03_capture import MAX_CHANNEL, MAX_TOTAL, RawCapture
from initialize import read_frame
from owner_broker_session import reply, send


class Child:
    def __init__(self, incoming):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(incoming)


class RawCaptureTest(unittest.TestCase):
    def test_exact_wire_ranges(self):
        capture = RawCapture()
        child = Child(b'{"jsonrpc":"2.0","id":1,"result":{}}\n')
        send(child, {"id": 1, "method": "initialize", "params": {}}, capture)
        result = reply(child, 1, lambda _: None, hashlib.sha256(), capture)
        self.assertEqual(result, {})
        capture.record("relay-http-in", b"POST /v1/messages HTTP/1.1\r\n\r\n")
        capture.record("acp-in", b"second line\n")
        sealed = capture.seal()
        self.assertEqual(sealed["blobs"]["acp-in"], child.stdin.getvalue() + b"second line\n")
        self.assertEqual(sealed["blobs"]["acp-out"],
                         b'{"jsonrpc":"2.0","id":1,"result":{}}\n')
        self.assertEqual(sealed["ranges"][0]["offset_bytes"], 0)
        self.assertEqual(sealed["ranges"][3]["offset_bytes"], len(child.stdin.getvalue()))
        self.assertEqual([ref["purpose"] for ref in sealed["raw_refs"]], sealed["blob_order"])
        self.assertEqual([row["purpose"] for row in sealed["manifest_blobs"]], sealed["blob_order"])
        with self.assertRaisesRegex(ValueError, "closed"):
            capture.record("acp-out", b"late")

    def test_refuse_oversize_without_partial_append(self):
        capture = RawCapture()
        capture.record("acp-in", b"first")
        with self.assertRaisesRegex(ValueError, "limit"):
            capture.record("acp-in", b"x" * MAX_CHANNEL)
        sealed = capture.seal()
        self.assertEqual(sealed["blobs"]["acp-in"], b"first")

    def test_reserve_artifact_space_within_total_transfer_limit(self):
        capture = RawCapture()
        capture.record("acp-in", b"a" * MAX_CHANNEL)
        capture.record("acp-out", b"b" * (MAX_TOTAL - MAX_CHANNEL))
        with self.assertRaisesRegex(ValueError, "limit"):
            capture.record("soul-result", b"c")
        sealed = capture.seal()
        self.assertEqual(sum(row["size_bytes"] for row in sealed["manifest_blobs"]), MAX_TOTAL)
        self.assertNotIn("soul-result", sealed["blobs"])

    def test_read_frame_sink_preserves_original_bytes(self):
        raw = b'{ "jsonrpc": "2.0", "id": 9, "result": {} }\n'
        seen = []
        self.assertEqual(read_frame(io.BytesIO(raw), seen.append)["id"], 9)
        self.assertEqual(seen, [raw])


if __name__ == "__main__":
    unittest.main()
