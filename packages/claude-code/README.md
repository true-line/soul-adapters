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

To repeat the session check with Machine's two-socket policy, copy the
[pinned Machine profile](https://github.com/true-line/agent-bot-machine/blob/e2537d299cc677a2c29a6e7719036d103048dcc2/internal/members/darwin/harness-egress.sb)
into the disposable guest, then run:

```sh
python3 packages/claude-code/vm_session.py /path/to/claude-agent-acp-darwin-arm64.zip --machine-profile /path/to/harness-egress.sb
```

This mode verifies the profile digest and assigns a private fixture path to
each socket. It checks signed-out session creation with direct TCP denied.
During shutdown the adapter can still deliver authentication status and the
new session’s available commands. The probe rejects additional responses,
repeated notifications, and command lists for a different session. It starts
no socket listeners and sends no model request. HQ launch approval, member
authentication and broker traffic still require separate verification.

To check the two halves of startup in the same disposable member launch, pass
`--member-cli /absolute/path/to/node /absolute/path/to/agent-bot-souls.mjs`
together with `--machine-profile`. The fixture must launch the check as a
standard guest user with its own Souls member token, member socket and HQ
broker socket. Supply the installed public Souls CLI and the verified Node
runtime from that fixture. The check uses the assigned socket paths in the
pinned profile and calls `agent-bot-souls member runtime` before running the
stock ACP startup. Check HQ's `hq.bind` receipt for the admitted member and
runtime. The CLI closes its authenticated connection when it exits. The stock
adapter receives no member token, opens its signed-out ACP session, and exits
on EOF. Model discovery, streaming and a persistent vendor broker transport
remain outside this check.

### Reproduce the cooperative member check

In the same offline disposable guest, use Node 24 and a standard account. The
driver stages two verified archives under a fresh private directory. Supply the
Darwin HQ interim tar built from `f07a9fb57cc0548a5293bff967ed7e0f0c3224e3`
using the [HQ artifact build procedure](https://github.com/true-line/agent-bot-hq/blob/f07a9fb57cc0548a5293bff967ed7e0f0c3224e3/docs/interim-release-runbook.md).
It includes the pinned production Comms broker and contracts. Prepare the
second archive from the public Souls checkout and copy it into the guest:

```sh
git archive --format=tar --prefix=souls/ 126fff6516785b887a0ceff9cf4f0b5b4bc4bf2a > souls.tar
```

With the verified HQ, Souls, upstream Claude and Machine policy files, run:

```sh
node packages/claude-code/member_launch_check.mjs hq.tar souls.tar claude-agent-acp-darwin-arm64.zip harness-egress.sb
```

The real Souls entry point admits one fixture member through an isolated HQ
test host with simulated enrollment and launches a local ACP test driver.
The driver consumes the outer test turn locally. Only ACP initialization and
session creation reach the stock adapter. The check measures HQ's existing
cooperative preflight bind before the member starts, then requires exactly
one additional HMAC bind for the same admitted Soul, member and runtime.
It compares the public member CLI's epoch and lease generation to Souls'
admission status and requires a ready member. This exercises one cooperative
account and shared broker. Dedicated-account launch approval and a credentialed
provider turn remain separate gates.

### Stream a synthetic Messages turn over the assigned Unix socket

Use the same fresh, credential-free Apple Silicon macOS guest and standard test
account. Copy the pinned Claude release archive, this package directory and the
[pinned Machine profile](https://github.com/true-line/agent-bot-machine/blob/e2537d299cc677a2c29a6e7719036d103048dcc2/internal/members/darwin/harness-egress.sb)
into the clone. In that account run:

```sh
python3 packages/claude-code/unix_messages.py claude-agent-acp-darwin-arm64.zip harness-egress.sb
```

This verifies both stock executables, then runs Claude Code directly and through
ACP v1 in native inherited-environment mode. Each gets a fresh private home and
the same pinned two-socket profile. A synthetic HTTP Messages server listens on
the test broker socket. The member socket path is unused. The child receives
`ANTHROPIC_UNIX_SOCKET`, an inert base URL and the literal `ssh-placeholder` as
its only credential-shaped input. [Anthropic documents Unix socket support for local proxies](https://github.com/anthropics/claude-code/releases/tag/v2.1.271).

Each pass requires one streamed `POST /v1/messages` carrying the exact fixture
prompt, the expected model and placeholder header, two distinct response
deltas, and clean exit. The server waits for the client to observe the first
delta before sending the second. The receipt counts unrelated Messages requests
and policy checks without printing bodies or header values. The fixture starts
no TCP listener. Use `tart stop "$guest"` on the host when finished.

This check covers stock Messages transport and ACP prompt delivery. The fixture
doesn't exercise HQ's broker protocol, member binding, provider authorization,
catalog discovery or production accounting. ACP's separate `providers/set`
catalog client is outside this native-mode check.

Add `--read-fixture` to exercise a tool round trip in each stock client. The
fixture creates one private text file and configures only the built-in `Read`
tool, with consent for that exact file. The first synthetic Messages response
requests that read. The next request must carry the assistant tool call, the
matching user tool-result ID and the random file contents. Only then does the
server send the incremental text reply. The receipt records the observed tool,
transcript and usage shapes as counts and booleans. Keep this optional check
inside the same disposable guest.
