# Adapter qualification

An adapter qualifies per enforcement profile on a named host, adapter version, and harness binary digest. Qualification is a `soul-conformance` run, not a self-declaration. The evidence payload in [soul-contracts PR #46](https://github.com/true-line/soul-contracts/pull/46) is still a draft: a shape-valid payload does not qualify an adapter or authorize a launch.


## Release producer

The designated workflow is [release-qualification.yml](../.github/workflows/release-qualification.yml). It accepts a manual run only on protected `main`; a separate first gate fails another ref before requesting approval for the `qualification-release` environment, rather than appearing green with a skipped job. Its inputs name an exact `soul-conformance` evidence commit, a directory under `reports/`, and a UTC qualification expiry. The reviewed evidence commit must identify the exact private `qualification.json`, protected v2 and cooperative v1 host envelopes, owner-signed control/v2 policy, Souls package, and separately signed Souls build receipt. The workflow must fetch private bytes through an approved immutable staging path, then build Machine's preflight and offline verifier from a reviewed exact commit. Missing trust files, private assets, or authenticated references fail before signing. The private staging transport is not configured, so the current draft stops before any GitHub Release upload.

After Machine authenticates the owner policy, both host signatures, and the Souls build receipt against separately pinned roots and identities, the producer binds the exact verified policy envelope, qualification, report payloads and envelopes, Souls package digest, receipt digest, and source tree to an in-toto Statement. It compares the owner-pinned release workflow and Souls build workflow identities with the actual source before keyless Cosign signing. Machine must then verify the resulting Sigstore v0.3 bundle offline. Signed physical host reports and envelopes, owner policy, full qualification bytes, Souls package and receipt, and complete verification outputs stay in immutable private staging. A later public draft GitHub Release may carry only reviewed redacted metadata, exact digests, references, and verdicts; it must never archive the private working directory. This release producer does not authorize protected launch.

The production owner root, key ID, signed control/v2 policy, signed Souls build receipt, approved private asset transport, and physical designated-host reports have not been supplied. The workflow remains unable to sign a production qualification until these inputs exist and its pinned Machine commit includes the reviewed trust files and v2 verifier. Shape-valid synthetic fixtures and locally generated signatures do not satisfy this gate. The [platform trust decision](https://github.com/true-line/agent-bot-platform/blob/main/docs/04-decisions/PDR-0102-protected-adapter-qualification.md) owns the trust rule; this section describes this repository's producer.

The public `soul-conformance` evidence commit also needs a reviewed index of immutable private asset URIs and exact byte digests. The producer must fetch by that pinned index, check every downloaded digest, pass the private bytes to Machine's offline verifier, and retain the complete evidence in private staging. The index format, private provider, credential scope, and retention rule are still open release-process decisions. The three explicit workflow holds keep private fetch, signing, and GitHub Release upload closed until those decisions have an implemented, reviewed path.

The draft `reports/<run>/private-assets.json` index uses two-space JSON with one trailing newline, `schema_version: 1`, and exactly six `assets` entries: `qualification`, `policy-envelope`, `protected-envelope`, `cooperative-envelope`, `souls-package`, and `souls-build-bundle`. Each entry has only `uri`, `sha256` (lowercase `sha256:<64 hex>`), and positive `byte_length`. The URI must be unique, immutable, and under the separately approved `QUALIFICATION_PRIVATE_ASSET_URI_PREFIX`. The workflow reads the index bytes from the pinned conformance Git object and requires a regular-file tree entry. The private fetch will map those roles to the fixed local filenames used by Machine. `scripts/verify-private-assets.mjs` rejects alternate JSON encodings or duplicate keys, absent or extra roles, unapproved or duplicate URIs, symlinked files, changed sizes, and changed hashes. Its prefix check compares URI text only; an approved fetch transport must also reject path normalization, dot segments, percent escapes, and redirects that could reach a different object. The workflow runs the byte check after the private fetch hold and before Machine preflight. This draft does not select a storage provider or grant release authority.

The Statement builder accepts only the ten contracted `souls_build` fields and a receipt containing exactly `uri` and `sha256`; it reconstructs that public object field by field. Before constructing the Statement, it checks that the receipt URI and digest and package digest match the canonical, reviewed public asset index under the approved URI prefix. The URI in a future public Rekor entry would therefore be the URI already disclosed by that public index. The owner must approve that exposure and the transport before the signing hold can be removed. If the URI itself must stay private, the v2 attestation contract needs a reviewed digest-only change before signing.

The [approved Stage 2a B-principal amendment](https://github.com/true-line/agent-bot-platform/pull/114) requires a dedicated bridge UID B for each Soul installation, separate from runner R and credential UID C. Protected T03 now needs signed admission and exact socket assignments, kernel peer checks, and an actual HQ broker HMAC bind from B before vendor launch. The current host-report format and pinned Machine verifier do not explicitly bind B's UID to its signed admission proof. The production release must wait for that reviewed trust contract and verifier change plus a new signed report from the designated physical host. A synthetic `T03: pass` field or this release producer's preflight cannot close that gap.

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
