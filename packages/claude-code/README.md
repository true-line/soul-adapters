# claude-code adapter

ACP adapter, render plugin, and qualification manifest. See `../../docs/`.

Status: not started.

The Darwin arm64 [package smoke workflow](../../.github/workflows/claude-code-initialize.yml)
checks the public [claude-agent-acp v0.109.0 release](https://github.com/agentclientprotocol/claude-agent-acp/releases/tag/v0.109.0).
It pins the archive and both executables, sends one ACP v1 `initialize` over stdio,
checks the adapter identity and waits for exit zero after closing stdin. The
agent and its background status probe inherit an isolated home and an offline
sandbox on a disposable runner. This evaluates the upstream package only.
Adapter integration, render support and profile qualification remain pending.

### Offline session probe

Run `python3 packages/claude-code/session.py <pinned-release.zip>` as an
ordinary user on a Darwin arm64 host. The probe verifies the same archive
and both executable hashes as the initialize check. It sends the entry
point's ACP v1 `initialize` and `session/new` requests with a private working
directory, an empty MCP server list, and the existing offline sandbox.
It never sends an authentication request or a prompt. A refusal or timeout
returns a nonzero exit code.

The default mode points [Claude Code's internal temp directory](https://code.claude.com/docs/en/env-vars)
at the same private directory as `TMPDIR`. Use `--temp-mode default` to
compare the standard environment without that vendor setting. The probe
only measures this unauthenticated offline exchange. Production sessions may
require an authorized broker exchange or authenticated vendor state.

### Ordinary-user control in a disposable macOS guest

This comparison gives the adapter ordinary guest filesystem and process access
while denying network operations. Use a stopped, credential-free Apple Silicon
macOS baseline with a standard test account and Python 3. On the host, set
`baseline` to the name of that stopped VM, then start a fresh Tart clone with
host-only networking:

```sh
guest="claude-acp-$(uuidgen)"
tart clone "$baseline" "$guest"
tart run "$guest" --net-host --no-clipboard --no-usb-accessories
```

Copy this repository's `packages/claude-code` directory and the pinned release
zip into the clone using the test account. In that account run:

```sh
python3 packages/claude-code/vm_session.py /path/to/claude-agent-acp-darwin-arm64.zip
```

The guest check verifies the archive, executables, blocked loopback connection,
and signed-out CLI state. It uses the same ACP requests and shutdown checks as
the regular probe. A pass confirms the package can create a local session
before authentication or a model prompt. Use `tart stop "$guest"` on the host
when finished. Profile qualification remains pending.
