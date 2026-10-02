# ZCodeToAPI

ZCodeToAPI is an **unofficial reverse-engineered interoperability client** for
using a signed-in ZCode Start Plan account from scripts, local applications,
OpenAI-compatible clients, Codex, and Claude Code.

```text
Your app / Codex / Claude Code
            |
            v
      ZCodeToAPI
   local compatibility layer
            |
            v
   ZCode Start Plan backend
            |
            v
   GLM-5.3 / GLM-5.3-Flash
```

It does not emulate a Z.ai API key and it does not bypass ZCode account,
entitlement, quota, CAPTCHA, or anti-abuse checks. It uses the credentials and
entitlements of a ZCode account you already own or are authorized to use.

## Current verified state

The current implementation has been verified on Windows with:

| Component | Verified version / state |
| --- | --- |
| ZCode desktop client inspected | 3.14.4 |
| Codex CLI | 0.160.0 |
| Claude Code | 2.1.287 |
| ZCode models | GLM-5.3, GLM-5.3-Flash |
| ZCode thinking levels | low, high, max |
| OpenAI-compatible proxy | Chat Completions, Responses, Completions, models, tools |
| Anthropic-compatible proxy | Messages, token counting, tools, models |
| Codex interactive TUI | WinPTY relay, native model picker, dynamic reasoning |
| Claude interactive TUI | isolated runtime/auth, model + effort switching |
| ZCode quota | live balance buckets and entitlement periods |
| Promotional grants | live preview + claim flow with official Aliyun verification |

The repository is intentionally provider-specific at the backend: all inference
still goes to the ZCode Start Plan service.

## What works

### Direct ZCode client

`zcode_direct_client.py` can:

- load the local ZCode Start Plan credential
- call the ZCode Start Plan model endpoint directly
- use GLM-5.3 or GLM-5.3-Flash
- use low, high, or max thinking
- fetch live balance/quota information
- discover currently claimable Start Plan promotions
- claim an eligible promotion through ZCode's real claim endpoint
- run the official Aliyun CAPTCHA flow when ZCode requires verification

### OpenAI-compatible API

`openai_proxy.py` exposes a local OpenAI-compatible API suitable for custom
apps and Codex-style clients.

Supported model IDs include:

```text
glm-5.3-flash
glm-5.3
GLM-5.3-Flash
GLM-5.3
zcode-glm-5.3-flash
```

The proxy supports:

- `/v1/chat/completions`
- `/v1/responses`
- `/v1/completions`
- OpenAI-style function/tool calls
- per-request model switching
- per-request reasoning effort
- live ZCode balance
- promotional claim preview and claim endpoints
- Codex's native provider model catalog

Codex `xhigh` reasoning maps to ZCode `max`.

### Anthropic / Claude-compatible API

`claude_proxy.py` exposes a local Anthropic-compatible Messages API.

It supports:

- `/v1/messages`
- `/v1/messages/count_tokens`
- Claude-style tool use / tool results
- GLM-5.3 and GLM-5.3-Flash
- dynamic `/model` changes from Claude Code
- dynamic `/effort` changes from Claude Code
- live ZCode quota
- promotional claim preview and claim endpoints
- isolated local OAuth/profile compatibility for Claude Code

## Codex integration

The easiest Windows entry point is:

```bat
zcode-cli-launcher.cmd
```

Choose **Codex**, then select the ZCode model and initial thinking level.

The launcher:

- runs the real Codex CLI through a Windows PTY
- keeps Codex's ZCode configuration isolated from normal OpenAI auth/session state
- uses a short project-drive `CODEX_HOME`
- preserves the ZCodeToAPI-owned Codex settings/plugin state across launches
- supplies a ZCode-specific native model catalog
- keeps local/provider-independent Codex features native

### Native `/model`

Codex's own model picker is preserved, but the provider catalog contains only:

```text
GLM-5.3-Flash
GLM-5.3
```

Reasoning choices map as:

```text
Low        -> ZCode low
High       -> ZCode high
Extra high -> ZCode max
```

This is not cosmetic. A real TUI test changed:

```text
GLM-5.3-Flash / xhigh
        ->
GLM-5.3 / high
```

and the next upstream request was verified as `GLM-5.3` with ZCode thinking
`high`.

### Codex `/usage`

Codex's OpenAI-account usage is unrelated to ZCode quota. The launcher
therefore owns `/usage` and renders ZCode's real buckets, including:

- model
- plan
- entitlement period
- used units
- remaining units
- total units
- reset or expiry time

### Codex commands

Local/provider-independent commands remain native. Verified examples include
`/permissions`, `/status`, `/mcp`, `/plugins`, `/skills`, `/hooks`,
`/memories`, `/agents`, `/experimental`, reviews, worktrees, sessions,
sandboxing, resume/fork, and local diagnostics.

OpenAI/ChatGPT-hosted slash commands that cannot truthfully work through ZCode
are intercepted with an explicit explanation. The verified Codex 0.160.0 set
includes:

```text
/daybreak
/apps
/voice
/app
/logout
/feedback
```

Hosted/account top-level commands such as `login`, `logout`, `cloud`,
`cloud-tasks`, `app`, and `remote-control` are likewise rejected when
passed through the launcher rather than silently escaping to OpenAI.

Provider escape routes such as unsupported model IDs, native OpenAI search,
OSS/local-provider routing, and provider-defining config overrides are also
blocked in launcher-managed Codex sessions.

## Claude Code integration

Run:

```bat
zcode-cli-launcher.cmd
```

and choose **Claude**.

The launcher uses the real installed Claude Code CLI but keeps ZCodeToAPI's
compatibility runtime and credentials isolated from the user's normal Claude
Code login.

Verified behavior:

- `/model glm-5.3` switches subsequent inference to ZCode GLM-5.3
- `/model glm-5.3-flash` switches back to ZCode GLM-5.3-Flash
- `/effort` changes the actual ZCode thinking level
- `/usage` shows ZCode's real quota instead of Claude subscription windows
- local Claude Code tools and workflows continue through the real CLI
- tool-use loops and multi-file edits work through the compatibility proxy

Claude's built-in account/cloud surfaces are not emulated. Commands that
specifically depend on Anthropic billing, hosted agents, Claude Design,
provider setup, or Anthropic account services are intercepted with a clear
ZCodeToAPI explanation.

The compatibility sweep covers the relevant Claude Code 2.1.287 commands and
aliases, including account/cloud features such as `/fast`, `/schedule`,
`/teleport`, `/remote-control`, `/usage-credits`, `/extra-usage`,
Claude Design, hosted integrations, `/login`, and `/logout`.

See `CLAUDE_PROXY.md` for the grouped command list and implementation notes.

## ZCode usage / quota

Both proxies expose:

```text
GET /v1/zcode/balance
GET /v1/usage
```

The launcher also provides:

```bat
zcode-cli-launcher.cmd usage
zcode-cli-launcher.cmd usage --json
```

The usage renderer preserves each bucket's actual ZCode entitlement period.
For example, normal recurring limits and one-time promotional grants are shown
as separate buckets rather than being merged into Claude/OpenAI subscription
windows.

## Trust Build / promotional grant claiming

ZCodeToAPI now reproduces the manual Start Plan claim flow used by the ZCode
desktop client without requiring the ZCode application itself to be open.

The ZCode 3.14.4 client uses:

```text
GET  https://zcode.z.ai/api/v1/zcode-plan/billing/preview
POST https://zcode.z.ai/api/v1/zcode-plan/billing/claim
```

### Dynamic plan discovery

The plan ID is **not hardcoded**.

ZCodeToAPI fetches the live preview and selects the current eligible
GLM-5.3-Flash promotion. It prefers a Trust Build grant of at least 100M
tokens, then falls back to the highest-value/highest-priority eligible Flash
grant.

During the latest live verification, preview returned a 100,000,000-token
one-time GLM-5.3-Flash Trust Build offer. Future campaign IDs can change
without requiring a code update.

### API

Both compatibility proxies expose:

```text
GET  /v1/zcode/claim-preview
GET  /v1/zcode/claim/preview
POST /v1/zcode/claim
```

Example local claim request:

```json
{
  "auto_verify": true
}
```

Optional fields:

```json
{
  "plan_id": "an exact plan id returned by preview",
  "captcha_verify_param": "a fresh Aliyun verification value",
  "captcha_region": "region override",
  "auto_verify": false,
  "verification_timeout": 120
}
```

The legacy `interactive_verification` field is accepted as an alias for
`auto_verify`.

If verification is required and no fresh value is supplied, the API returns
HTTP `428` with `captcha_required: true`.

Automatic browser verification is intentionally **loopback-only**. Remote
callers must provide their own fresh `captcha_verify_param`.

### CAPTCHA behavior

The project does not bypass CAPTCHA.

It uses the same official Aliyun SDK flow as ZCode:

1. request the current CAPTCHA config from ZCode
2. attempt Aliyun's traceless verification
3. if Aliyun escalates the request, show the interactive challenge
4. submit the fresh verification value to ZCode's real claim endpoint

The ZCode desktop app does not need to be opened for this flow.

Short-lived CAPTCHA values are redacted from proxy request dumps.

### Launcher commands

```bat
zcode-cli-launcher.cmd claim-preview
zcode-cli-launcher.cmd claim
```

Useful forms:

```bat
zcode-cli-launcher.cmd claim-preview --json
zcode-cli-launcher.cmd claim --plan-id <plan-id>
zcode-cli-launcher.cmd claim --captcha-verify-param "<fresh-value>"
zcode-cli-launcher.cmd claim --no-browser --json
```

Claim command exit codes:

```text
0 = claim succeeded
1 = claim rejected or another error occurred
3 = eligible claim exists but fresh CAPTCHA verification is required
```

## Quick start

### Requirements

- Windows (current tested launcher platform)
- Python 3
- ZCode installed and signed in at least once
- a ZCode account with Start Plan access
- Codex installed if you want Codex integration
- Claude Code installed if you want Claude integration

Install Python dependencies:

```bat
py -m pip install requests cryptography pywinpty
```

### Direct request

```bat
py zcode_direct_client.py "Reply only OK." --model GLM-5.3-Flash --thinking-level max
```

Useful direct commands:

```bat
py zcode_direct_client.py --balance
py zcode_direct_client.py --claim-preview
py zcode_direct_client.py --claim
py zcode_direct_client.py --claim --claim-no-browser
```

### OpenAI-compatible proxy

```bat
py openai_proxy.py --host 127.0.0.1 --port 8787
```

Configure the client with:

```text
Base URL: http://127.0.0.1:8787/v1
Model:    glm-5.3-flash
API key:  any local value unless ZCODE_PROXY_API_KEY is configured
```

### Claude-compatible proxy

```bat
py claude_proxy.py --host 127.0.0.1 --port 8788
```

### Interactive launcher

```bat
zcode-cli-launcher.cmd
```

Other useful launcher commands:

```bat
zcode-cli-launcher.cmd models
zcode-cli-launcher.cmd usage
zcode-cli-launcher.cmd claim-preview
zcode-cli-launcher.cmd run codex
zcode-cli-launcher.cmd run claude
```

## API surface

### OpenAI-compatible proxy

```text
GET  /health
GET  /v1/models
GET  /v1/models/{model}
GET  /v1/codex/models
GET  /v1/zcode/balance
GET  /v1/usage
GET  /v1/zcode/claim-preview
GET  /v1/zcode/claim/preview
POST /v1/chat/completions
POST /v1/responses
POST /v1/completions
POST /v1/zcode/claim
```

### Claude-compatible proxy

```text
GET  /health
HEAD /api/hello
GET  /v1/models
GET  /v1/zcode/balance
GET  /v1/usage
GET  /v1/zcode/claim-preview
GET  /v1/zcode/claim/preview
POST /v1/messages
POST /v1/messages/count_tokens
POST /v1/zcode/claim
```

See `OPENAI_PROXY.md` and `CLAUDE_PROXY.md` for protocol-specific details.

## Credentials and isolation

By default, the project reads the local ZCode credential store:

```text
%USERPROFILE%\.zcode\v2\credentials.json
```

Process-only overrides are also supported:

```text
ZCODE_START_PLAN_TOKEN
ZCODE_JWT_TOKEN
```

Optional local-proxy protection:

```text
ZCODE_PROXY_API_KEY
ZCODE_CLAUDE_PROXY_API_KEY
```

See `.env.example` for the supported environment variables.

The Codex and Claude launchers deliberately isolate their compatibility
credentials/settings rather than rewriting the user's normal OpenAI or
Anthropic login configuration.

## System prompt constraint

The tested ZCode Start Plan route expects the stock ZCode request shape and
system template.

Verified behavior:

```text
Stock ZCode template + normal user messages:
works

Replacing the backend system prompt directly:
can be rejected with code 3012

Keeping the stock template and steering behavior through the conversation:
works
```

For that reason, `start_plan_request_template.json` keeps the backend-compatible
template. Machine-specific paths are represented by placeholders in Git and
filled locally in memory at runtime.

## Security / safety notes

- Use only ZCode accounts you own or are authorized to use.
- Never commit real credentials, JWTs, API keys, CAPTCHA values, or `.env`
  files.
- Proxy diagnostics can contain prompts and tool results; review them before
  sharing.
- Claim CAPTCHA values are short-lived and redacted from normal request dumps.
- The claim API preserves ZCode eligibility, CAPTCHA, quota, and anti-abuse
  checks.
- This is not an official public ZCode API and upstream behavior may change.

## Known limitations

- The interactive launcher is currently tested primarily on Windows.
- ZCode's internal endpoints and client request shape are unofficial and can
  change between ZCode releases.
- Hosted OpenAI/Anthropic account features are intentionally not emulated.
- Some hosted CLI commands are intercepted because they have no truthful ZCode
  equivalent.
- A claim may still require an interactive Aliyun challenge even though the
  ZCode desktop app itself is not needed.
- Direct replacement of ZCode's core system prompt can trigger upstream
  unusual-activity rejection.

## Project layout

```text
README.md                         Current overview and quick start
INTEGRATION.md                    App integration notes
OPENAI_PROXY.md                   OpenAI/Codex-compatible proxy details
CLAUDE_PROXY.md                   Claude/Anthropic-compatible proxy details
CLI_LAUNCHER.md                   Codex/Claude launcher behavior
zcode_direct_client.py            Direct ZCode Start Plan client
openai_proxy.py                   OpenAI-compatible local proxy
claude_proxy.py                   Anthropic-compatible local proxy
zcode_cli_launcher.py             Isolated Codex/Claude launcher
zcode-cli-launcher.cmd            Windows launcher entry point
start_plan_request_template.json  Backend-compatible request template
.env.example                      Safe environment-variable examples
examples/                         Small examples
tests/                            Repository tests
```

## Verification performed

Recent live verification includes:

- direct GLM-5.3 and GLM-5.3-Flash requests
- low/high/max thinking
- OpenAI Responses tool loops
- real Codex coding/tool execution
- Codex native model + reasoning switching
- Codex local command smoke tests
- Codex hosted/account command interception
- Claude Code tool loops and multi-file edits
- Claude model and effort switching
- Claude account/cloud command compatibility sweep
- live ZCode quota retrieval
- live promotional claim preview
- claim request-contract tests without consuming the live promotion
- OpenAI and Claude claim-handler routing tests
- live OpenAI-compatible claim-preview endpoint

The claim implementation was tested non-destructively: preview and request
construction were verified without consuming the available promotional grant.

## More documentation

- `INTEGRATION.md` — embedding ZCodeToAPI in another application
- `OPENAI_PROXY.md` — OpenAI/Codex-compatible API details
- `CLAUDE_PROXY.md` — Claude Code / Anthropic compatibility
- `CLI_LAUNCHER.md` — launcher isolation and CLI behavior
