# soul-adapters

One package per harness. Each package ships three things: an ACP adapter the Soul entry point drives, a render plugin for `soul build`, and a signed qualification manifest that states which enforcement profile and which primitive classes the adapter is qualified for. Adapters version independently of the runtime and qualify individually (draft 0.3, Section 2).

This is the only repository where harness, model, and provider vendor names are allowed in code (R10). Contracts stay vendor-neutral; configuration and adapters name vendors.

## Packages

| Package | Role in the pilot configuration (draft 0.3, Section 13) |
| --- | --- |
| `packages/claude-code` | Lead and specialist adapter over ACP |
| `packages/codex` | Lead and specialist adapter over ACP |
| `packages/local-model` | Locally hosted model as the offline inference route; brokered for budget and receipts |

## Depends on

`soul-contracts` (schemas, fixtures) and `soul-package` (render-plugin interface). Never on the runtime or HQ.

## Qualification

See `docs/QUALIFICATION.md`. An adapter without a passing qualification run for a profile may be used only under the cooperative profile, and every receipt says so (R8).

## Work package

WP3 Soul runtime — runtime implementer, with `soul-conformance` running the qualification suite.
