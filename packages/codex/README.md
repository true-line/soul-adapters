# Codex adapter

## Signed-out session check on macOS

From the repository root, run the following as an ordinary user on Apple Silicon
with Python 3 and the standalone Node.js distribution and npm on your PATH.
The check was exercised with Node.js 24.19.0.

```sh
python3 packages/codex/check/session.py
```

The setup downloads the npm packages specified in the check's lockfile and
verifies their integrity in a temporary installation. It uses codex-acp 2.8.0
and codex-cli 0.170.0. The runtime check starts with empty private home,
configuration, and working directories. Before starting the adapter, it checks
that the macOS sandbox denies a network connection and a read outside those
directories.

For each of ACP v1 and v2, the adapter must identify itself, advertise
`api-key` and `chat-gpt`, and refuse a signed-out `session/new` with
`-32000 Authentication required`. Closing stdin must yield a clean exit
without an unexpected response or a surviving child process. The check sends an empty MCP server list and ends before
login or a model prompt. Platform integration and profile qualification remain
pending.

## Local provider session check in a disposable macOS guest

Use a stopped, credential-free Apple Silicon macOS baseline with Python 3,
a standard test account and standalone Node.js 24 with npm. On an Apple
Silicon machine with registry access, populate a new cache using this check's
lockfile. From the repository root:

```sh
setup=$(mktemp -d)
cp packages/codex/check/package*.json "$setup/"
(cd "$setup" && npm ci --cache "$setup/cache" --include=optional --ignore-scripts --no-audit --no-fund)
```

Clone the stopped baseline and start the clone with host-only networking.
Copy the check directory, the cache's `_cacache` directory inside an empty
`cache/` directory, and Machine's
[pinned two-socket profile](https://github.com/true-line/agent-bot-machine/blob/e2537d299cc677a2c29a6e7719036d103048dcc2/internal/members/darwin/harness-egress.sb)
into the clone. As the standard guest user, run:

```sh
python3 check/vm_session.py /path/to/harness-egress.sb /path/to/cache
```

The check verifies the profile hash, installs the locked packages offline,
and verifies that direct loopback TCP is denied. For each ACP wire version,
the default provider must refuse a signed-out session. A local provider
configured in Codex and an explicitly selected ACP custom gateway must each
return a session ID and exit cleanly on EOF. For the explicit ACP gateway,
`providers/list` must report the configured fixture URL in the OpenAI provider slot.
For the TOML provider, the check observes only session creation. Both use a loopback URL and fresh private
state without credentials. The check starts no provider or socket
listener and sends no model prompt. Stop the clone afterward.

This exercises local session creation under Machine's socket policy. HQ launch
authorization, member authentication, traffic through the broker and model
execution still require separate verification.

## Cooperative loopback turn in a disposable guest

To exercise a model turn, use the same disposable guest and offline npm cache
as above. Copy `check/`, the cache into `cache/`, and standalone Node.js and
npm into `runtime/bin/` within a fresh private working directory. From that
directory, as the standard guest user, run:

```sh
python3 check/loopback_turn.py
```

The check installs the locked packages offline and starts a synthetic provider
on guest loopback. A separate cooperative sandbox denies non-loopback network
access, checked with a connection canary. The fixture sends delayed Responses
SSE events. The check verifies an actual CLI agent message and completed turn,
ACP gateway model discovery, incremental ACP agent message chunks before the
final response event, and `end_turn`. It prints request shapes without message
contents. It uses fresh state and no real credentials. This test does not run
the protected profile or qualify HQ launch, broker routing, or vendor access.

## Cooperative Soul front-door turn

A separate guest check runs the actual Soul ACP front door with stock Codex,
using the same standalone Node/npm runtime and offline cache as the loopback
check. Prepare these exact inputs on an authorized workstation:

- The Darwin interim HQ archive at commit `f07a9fb57cc0548a5293bff967ed7e0f0c3224e3`, SHA-256 `746281207ad2c853257b70d5a2d2b99e23e88c14604589fcab6473e5e609f84c`.
- A `git archive --format=tar` of Soul runtime commit `126fff6516785b887a0ceff9cf4f0b5b4bc4bf2a`, SHA-256 `13799b84de4d5099a51490553f9b5e8b829f499a7b128ac6666371e33ec93927`.
- A `git archive --format=tar` of soul-conformance commit `d70bda70505e7165e10a09b232bcf67e7c5bc14c`, SHA-256 `c9689026339930ee722e690af66fd4e74ece0ef4eaacd4f146c3f7e2c3059f03`.

Copy these archives, `check/`, the offline npm cache, and the standalone Node
runtime into a fresh disposable Apple Silicon guest. As its standard account:

```sh
python3 check/soul_turn.py hq.tar souls.tar conformance.tar cache runtime
```

The check verifies each archive digest, uses the conformance enrollment fixture
and the packaged HQ's production-pinned broker, and starts the Soul's ordinary
`run` entry point. Its cooperative sandbox permits loopback TCP and the exact
guest-local admission, broker, and member sockets. The synthetic provider
confirms that the literal test prompt reached the stock Codex Responses request;
the client must receive assistant text and `end_turn`. Admission, Equip, Ready,
and cooperative launch-refusal receipts must each exist and every HQ receipt
must name `managed-cooperative`.

This is an isolated cooperative integration check. It does not enroll a device,
use a real vendor credential, run the protected profile, or qualify a host.
