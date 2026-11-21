# Adapter qualification

An adapter qualifies per enforcement profile on a named host, adapter version, and harness binary digest. Qualification is a `soul-conformance` run, not a self-declaration. The evidence payload in [soul-contracts PR #46](https://github.com/true-line/soul-contracts/pull/46) is still a draft: a shape-valid payload does not qualify an adapter or authorize a launch.

## Authority for a protected claim

A designated qualification host signs the independent runner’s measured results.
On macOS the signing operation runs under admin-side Machine/keyd. Ordinary Mac
enrollment grants no authority to qualify releases. The designated release
workflow in `true-line/soul-adapters` verifies the host-signed passing evidence
and signs a qualification attestation. Its subject is the SHA-256 of the exact
`qualification.json` bytes. Owner-controlled signed policy identifies the
authorized test hosts, release source and workflow. [Owner decision](https://github.com/true-line/agent-bot-platform/discussions/102#discussioncomment-19798348).

The evidence covers the adapter package digest, executable path and digest,
ordered arguments, complete fixed code inputs and their digests, selected
harness binary path and digest, actual host and profile, suite revision and
results. The protected manifest entry references the signed attestation.
Publication can precede installation. Each operator approves the exact local
launch-rule installation separately.

Machine verifies the delivered evidence and trust policy offline. HQ may fetch
and deliver signed policy updates. Machine rejects older policy revisions and
refuses a protected launch when inputs change, qualification expires, revocation
information is stale, evidence or its signer is unknown or revoked, or the
required pairing is unavailable. Cooperative execution requires an explicit
choice. The owner selected a maximum revocation-snapshot age of 3,600 seconds. A snapshot exactly 3,600 seconds old is current; a missing, future-dated, or older snapshot refuses protected launch. Reissuing policy without a newly authenticated snapshot does not refresh it.
[Platform decision record](https://github.com/true-line/agent-bot-platform/blob/main/docs/04-decisions/PDR-0102-protected-adapter-qualification.md).

Required for **managed protected**:
- All protected egress goes through the broker: inference and service calls cannot bypass the proxy (T03).
- No credential or signing capability is reachable from the harness (T02).
- Harness-native state (transcripts, caches, config) lives inside the member's private state under the Soul root; the adapter honors `HOME`, `XDG_*`, `PATH`, and temp redirection.
- Interruption at a safe point and resume from durable handoff state.
- Session reuse only where the adapter proves compatibility for a route change.
- Cost upper bound reported before dispatch, or the adapter declares that it cannot and the receipt says so.
- Render coverage: the plugin renders every primitive class it declares, deterministically.

Required for **managed cooperative**: the same list, with items that depend on host isolation reported as unenforced rather than claimed.

The proposed evidence record is per measured run and includes adapter identity and package digest, executable path and digest, ordered arguments, fixed code inputs, harness path and digest, actual host profile, conformance suite revision and report, observations, and dated references. Follow the draft contract; do not infer that its current shape or a nonempty manifest list is sufficient.
