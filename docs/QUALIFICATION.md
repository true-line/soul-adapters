# Adapter qualification

An adapter qualifies per enforcement profile on a named host, adapter version, and harness binary digest. Qualification is a `soul-conformance` run, not a self-declaration.

Required for **managed protected**:
- All protected egress goes through the broker: inference and service calls cannot bypass the proxy (T03).
- No credential or signing capability is reachable from the harness (T02).
- Harness-native state (transcripts, caches, config) lives inside the member's private state under the Soul root; the adapter honors `HOME`, `XDG_*`, `PATH`, and temp redirection.
- Interruption at a safe point and resume from durable handoff state.
- Session reuse only where the adapter proves compatibility for a route change.
- Cost upper bound reported before dispatch, or the adapter declares that it cannot and the receipt says so.
- Render coverage: the plugin renders every primitive class it declares, deterministically.

Required for **managed cooperative**: the same list, with items that depend on host isolation reported as unenforced rather than claimed.

Record: `qualification.json` per package with adapter id, version, harness digest, host profile, profile qualified, renderable classes, cost-bound support, suite revision, date, and signature.
