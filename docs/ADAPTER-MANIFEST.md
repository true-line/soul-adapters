# Adapter manifest

Fields every package declares in `manifest.json`:

- `adapter_id`, `version`, `harness` (name, version range, binary digest when vendored)
- `acp` — ACP protocol version supported
- `profiles_qualified` — list of (host profile, enforcement profile, qualification record reference)
- `renders` — primitive classes the plugin renders and their targets
- `render_plugin` — entry point implementing the `soul-package` interface
- `state_redirect` — whether native state honors the member's private root
- `cost_bound` — `exact`, `estimate`, or `none`
- `session_reuse` — route changes under which native session reuse is supported

## Qualification references

The current v1 contract requires `profiles_qualified: []`. For a future
protected claim, the entry references the signed qualification attestation.
Its subject digest identifies the exact `qualification.json` bytes for the
measured inputs and profile. The [qualification guide](QUALIFICATION.md)
describes the evidence, signers and separate local installation approval.
