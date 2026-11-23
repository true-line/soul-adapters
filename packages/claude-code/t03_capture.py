"""Untrusted raw-wire capture for stock ACP and its assigned B relay.

The A-owned driver must join these blobs to its own command, challenge and OS
observations. This module neither accepts the A command nor certifies R/B state.
"""

import hashlib


CHANNELS = frozenset(("acp-in", "acp-out", "relay-http-in", "relay-http-out",
    "direct-attempts", "soul-result"))
MAX_CHANNEL = 4 * 1024 * 1024
# Reserve the full A-intake artifact allowance within its 8 MiB transfer cap.
MAX_TOTAL = 8 * 1024 * 1024 - 65536


def _digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class RawCapture:
    """Accumulate exact byte ranges; return their final blob digests only on seal."""

    def __init__(self):
        self._blobs = {}
        self._ranges = []
        self._total = 0
        self._sealed = False

    def record(self, channel, raw):
        if self._sealed or channel not in CHANNELS or not isinstance(raw, bytes) or not raw:
            raise ValueError("invalid or closed raw capture")
        current = self._blobs.get(channel)
        if current is None:
            current = bytearray()
        if len(current) + len(raw) > MAX_CHANNEL or self._total + len(raw) > MAX_TOTAL:
            raise ValueError("raw capture limit exceeded")
        offset = len(current)
        self._blobs[channel] = current
        current.extend(raw)
        self._total += len(raw)
        handle = len(self._ranges)
        self._ranges.append((channel, offset, len(raw)))
        return handle

    def seal(self):
        if self._sealed or not self._ranges:
            raise ValueError("raw capture is empty or already sealed")
        self._sealed = True
        blobs = {channel: bytes(raw) for channel, raw in self._blobs.items()}
        refs = {channel: {"sha256": _digest(raw), "size_bytes": len(raw),
                          "purpose": channel} for channel, raw in blobs.items()}
        order = sorted(blobs)
        ranges = [{"blob_sha256": refs[channel]["sha256"], "offset_bytes": offset,
                   "length_bytes": length} for channel, offset, length in self._ranges]
        return {"raw_refs": [refs[channel] for channel in order],
                "ranges": ranges, "blobs": blobs, "blob_order": order,
                "manifest_blobs": [{"purpose": channel, "size_bytes": len(blobs[channel])}
                                   for channel in order]}
