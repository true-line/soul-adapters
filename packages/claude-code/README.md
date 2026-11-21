# claude-code adapter

ACP adapter, render plugin, and qualification manifest. See `../../docs/`.

Status: not started.

The Darwin arm64 [package smoke workflow](../../.github/workflows/claude-code-initialize.yml)
checks the public [claude-agent-acp v0.109.0 release](https://github.com/agentclientprotocol/claude-agent-acp/releases/tag/v0.109.0).
It pins the archive and both executables and checks an ACP v1 `initialize`
over stdio. A second process uses fresh private state to check signed-out
`session/new`. Both checks verify the adapter identity and exit zero after EOF. The
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

For an already staged copy of the release, run the same probe as the intended
non-root account:

```sh
python3 -B packages/claude-code/session.py --staged-bundle /absolute/path/to/versioned/bundle
```

Keep the release files named `claude-agent-acp` and `claude` in the same
directory. Both must be readable and executable by the account. The probe
checks both pinned file hashes before invoking the adapter. It leaves the
bundle in place and creates the working directory, home, caches, and temporary
state under fresh private scratch. The same offline profile applies. The
result measures signed-out ACP startup at the staged path. The test profile
permits reads of macOS ICU data files named `/usr/share/icu/icudt<major>l.dat`.
It keeps other writes confined to private scratch and denies network access.

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

Add `--direct-control` for a stock CLI direct-only comparison followed by its
ordinary assigned-Unix-broker turn. A fixture-owned loopback listener confirms
it is reachable before and after the direct invocation. Under the same pinned
Machine profile, the direct invocation has a short timeout and zero configured
retries, must report a connection error, and must produce no listener connection.
The brokered invocation must complete its streamed turn. The client error is
not evidence of a particular OS errno; Machine's separate
[`darwin-harness-egress.py`](https://github.com/true-line/agent-bot-machine/blob/e2537d299cc677a2c29a6e7719036d103048dcc2/tests/fixtures/darwin-harness-egress.py)
checks the profile's permission refusal. This is a disposable component check,
not a Machine-supervised vendor launch or a protected T03 qualification.

Add `--read-fixture` to exercise a tool round trip in each stock client. The
fixture creates one private text file and configures only the built-in `Read`
tool, with consent for that exact file. The first synthetic Messages response
requests that read. The next request must carry the assistant tool call, the
matching user tool-result ID and the random file contents. Only then does the
server send the incremental text reply. The receipt records the observed tool,
transcript and usage shapes as counts and booleans. Keep this optional check
inside the same disposable guest.

### Check native CLI session persistence

Run the same command with `--resume-fixture` in the disposable guest. This
starts a fresh stock CLI session with ordinary persistence enabled. The test
requires a native transcript in the assigned member home with the unique
prompt and reply. A second CLI process uses `--resume` and sends a different
prompt. The Unix fixture checks that this request carries the first turn,
and the test checks that the second turn is saved under the same session ID.
The receipt reports these observations without printing transcript contents.
This check exercises the native CLI's local history. The ACP and Read checks
keep their existing behavior.

### Reload a session in a fresh ACP process

Run the same command with `--acp-load-fixture`. The stock ACP adapter
advertises both `loadSession` and `sessionCapabilities.resume` during
initialization. The first process finishes a turn and saves the native
transcript in the fresh member home. A second process uses the original
session ID, workspace, and state. It calls `session/load`, checks both
replayed messages, and sends another prompt. The same synthetic Unix server
requires the prior turn in the new Messages request and the fixture confirms
that the transcript now contains the second turn. This check exercises
`session/load`, which replays history. The separate `session/resume` method
is advertised and is outside this fixture.

### Optional bound HQ draft check

The Read fixture can also run against HQ draft [#208](https://github.com/true-line/agent-bot-hq/pull/208), commit `b3c4a6aadfe20abcac547ba17cd4538d3b00a68c`.
On a connected preparation host, obtain the public checkout and verify its
pinned contract release. The checkout must stay at this revision, without
tracked modifications. Copy the checkout and verified release tree into the
disposable guest; no network is needed in the guest.

```sh
git clone https://github.com/true-line/agent-bot-hq.git hq-checkout
git -C hq-checkout checkout b3c4a6aadfe20abcac547ba17cd4538d3b00a68c
(cd hq-checkout && node scripts/soul-contracts.mjs verify)
mkdir -p hq-checkout/vendor/soul-contracts
cp -R hq-checkout/.cache/soul-contracts/b96239cc47571237f7064ddbcbd0edec8e365c6b/{errors,fixtures,schemas} hq-checkout/vendor/soul-contracts/
```

Put Node.js 24.19.0 on the guest's PATH, then run:

```sh
python3 packages/claude-code/unix_messages.py claude-agent-acp-darwin-arm64.zip harness-egress.sb --read-fixture --hq-source /path/to/hq-checkout
```

A separate fixture shim accepts the clients' Unix HTTP requests, binds a
fixture member to HQ's reference broker with the real keyed challenge, and
translates the modeled turn into two `inference.responses` operations. The
fixture provider compares the entire carried system, ordered messages, Read
schema and output cap against the shim input; it asserts the tool request and
file result, exact synthetic usage and two completed zero-cost ledger entries.
The shim forwards text deltas as they arrive. The provider waits for the stock
client to observe the first text chunk before emitting the second. For a Read,
the shim collects the tool-call fragments and forwards them once HQ validates
the full arguments and returns the `tool_use` terminal result.
The shim renames `max_tokens` and omits `context_management`, `metadata` and
`thinking`; ACP's separate auxiliary requests are refused by the fixture.
Stock Claude does not authenticate to HQ in this check. The shim, fixture
provider and authorization are test-only, and this is not a production transport
or a protected launch qualification.
