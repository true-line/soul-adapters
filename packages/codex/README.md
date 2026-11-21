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
