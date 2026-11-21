# Adapter qualification

An adapter qualifies per enforcement profile on a named host, adapter version, and harness binary digest. Qualification is a `soul-conformance` run, not a self-declaration. The evidence payload in [soul-contracts PR #46](https://github.com/true-line/soul-contracts/pull/46) is still a draft: a shape-valid payload does not qualify an adapter or authorize a launch.


## Release producer

The designated workflow is [release-qualification.yml](../.github/workflows/release-qualification.yml). It can be dispatched only from `main` with an exact `soul-conformance` evidence commit, a directory under `reports/`, and a UTC qualification expiry. That directory supplies `qualification.json`, the protected and cooperative host signature envelopes, and the owner-signed policy envelope. The workflow fetches the exact evidence commit as data, builds Machine's `mm-qualification-preflight` from its reviewed pinned commit, and takes the owner root, key ID, and exact Sigstore trusted-root JSON only from that Machine source's `trust/qualification/` files. Missing trust files fail before signing.

After Machine verifies the policy and both host signatures, the producer binds its verified report digests and the exact qualification bytes to an in-toto Statement. It compares the owner-pinned source commit, workflow path, OIDC issuer, certificate identity, and Sigstore trusted-root digest with this run, then uses keyless Cosign to produce a Sigstore v0.3 bundle. The workflow runs Machine's separate offline release verifier against the final bundle and both report envelopes. After that passes, it checks the relocatable raw-file SHA-256 list and creates a new draft GitHub release containing the complete evidence archive and its own SHA-256 file. The tag binds the exact release-source and physical-evidence commits; an existing tag is never overwritten. The draft candidate is not a protected launch decision.

The production owner root, key ID, initial signed policy, and physical designated-host reports have not been supplied. The workflow remains unable to sign a production qualification until those inputs exist and its pinned Machine commit includes the reviewed trust files. Shape-valid synthetic fixtures and locally generated signatures do not satisfy this gate. The [platform trust decision](https://github.com/true-line/agent-bot-platform/blob/main/docs/04-decisions/PDR-0102-protected-adapter-qualification.md) owns the trust rule; this section describes this repository's producer.

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
