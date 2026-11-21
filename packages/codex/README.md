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
return a session ID and exit cleanly on EOF. Both use a loopback URL and fresh private
state without credentials. The check starts no provider or socket
listener and sends no model prompt. Stop the clone afterward.

This exercises local session creation under Machine's socket policy. HQ launch
authorization, member authentication, traffic through the broker and model
execution still require separate verification.
