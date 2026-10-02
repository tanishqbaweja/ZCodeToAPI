# Claude Code Proxy

This proxy exposes a local Anthropic Messages API compatible endpoint and forwards model work to the ZCode Start Plan backend.

```text
Claude Code -> local Claude proxy -> ZCode Start Plan backend -> GLM-5.3 / GLM-5.3-Flash
```

## Start the proxy

Open this folder in Command Prompt and run:

```bat
py claude_proxy.py --host 127.0.0.1 --port 8788
```

The Claude-compatible endpoint is:

```text
http://127.0.0.1:8788/v1/messages
```

## Claude Code settings

When Claude Code runs on the same machine, use:

```bat
set ANTHROPIC_BASE_URL=http://127.0.0.1:8788
set ANTHROPIC_API_KEY=local
set ANTHROPIC_MODEL=claude-sonnet-4-5-20250929
```

When using `zcode-cli-launcher.cmd`, you do not need to set any of these variables manually and you do not need a real Anthropic API key or Anthropic login. The launcher gives Claude Code a per-run isolated `claudeAiOauth` credential whose access token is valid only against the local proxy. The proxy authenticates upstream with the user's ZCode credentials. The user's normal Claude credential store is left unchanged.

The launcher also clones the useful parts of the user's Claude configuration into a per-run writable directory and sets `CLAUDE_CONFIG_DIR` to that copy. Normal settings, plugins, current-project sessions, and memory remain available, while slash commands such as `/model`, `/effort`, and `/config` cannot modify the permanent `~/.claude` configuration.

The proxy still returns `anthropic-ratelimit-unified-*` headers on model responses and implements local `/api/oauth/profile` and `/api/oauth/usage` compatibility endpoints, including the launcher's `/zcode-oauth-bridge/...` prefixed form. ZCode limits are daily while Claude's built-in Usage page labels its native windows as 5-hour/week, so the Windows launcher intercepts the exact `/usage` command and renders the real ZCode daily buckets instead of showing the wrong period labels.

Claude Code's production binary normally hard-codes the OAuth/admin base URL and does not route `/api/oauth/usage` through `ANTHROPIC_BASE_URL`. The launcher solves that without altering the installed CLI by building a cached project-local compatibility copy under `.runtime` and changing one OAuth allowlist string in that copy only. The global `claude.exe` / npm wrapper remain untouched.

## Prefix caching

ZCode's Start Plan endpoint performs prefix caching and reports it through `cache_read_input_tokens`. The Claude bridge is intentionally ordered to keep Claude Code's large, stable behavioral system prompt and tool schemas at the front of the bridged prompt.

Claude Code also sends an `x-anthropic-billing-header` system block whose `cc_version` suffix changes between processes. That block is transport/telemetry metadata rather than behavioral instructions. The bridge preserves it, but moves it after the stable system/tool prefix so it does not destroy cross-session cache reuse.

Observed with Claude Code 2.1.287, `GLM-5.3`, Max effort, same working directory:

```text
fresh warm-up process:  input=23973  cache_read=19968  ~45.44% cache-read
next fresh process:     input=1893   cache_read=42048  ~95.69% cache-read
third fresh process:    input=2023   cache_read=41920  ~95.40% cache-read
```

Within an existing multi-turn Claude Code session, previously observed turns reached roughly 98.7% cache-read because prior conversation prefixes were reusable too.

Cache reuse can drop when the backend model, working directory/runtime context, Claude Code version, tool catalog, system instructions, or other early-prefix content changes.

When Claude Code runs inside Docker on the same Windows host, use:

```text
ANTHROPIC_BASE_URL=http://host.docker.internal:8788
ANTHROPIC_API_KEY=local
ANTHROPIC_MODEL=claude-sonnet-4-5-20250929
```

For direct manual proxy configurations, compatibility aliases still exist. Launcher sessions expose and use the real ZCode model IDs `glm-5.3-flash` and `glm-5.3`.

## Supported endpoints

```text
GET  /health
HEAD /api/hello
GET  /v1/models
GET  /v1/zcode/balance
GET  /v1/usage
POST /v1/messages
POST /v1/messages/count_tokens
```

## Thinking level

The proxy can route ZCode's GLM thinking level to the backend. Accepted values are:

```text
low
high
max
```

Set the default:

```bat
set ZCODE_CLAUDE_THINKING_LEVEL=max
py claude_proxy.py --host 127.0.0.1 --port 8788
```

Per-request override:

```json
{
  "zcode_thinking_level": "high"
}
```

If Claude Code sends `thinking.budget_tokens`, the proxy maps large budgets to `max`, mid-size budgets to `high`, and small budgets to `low`.

In normal launcher sessions, Claude Code sends its `/effort` choice as
`output_config.effort`. The proxy gives that value priority over the launcher's
initial default, so changing `/effort` changes the real ZCode thinking level on
subsequent inference calls.

## Model switching

Claude Code's request `model` field is mapped per request:

```text
glm-5.3-flash -> GLM-5.3-Flash
glm-5.3       -> GLM-5.3
```

The launcher deliberately does not set `ANTHROPIC_MODEL`, because that
environment variable pins Claude Code's model and prevents `/model` from
becoming the real current selection. The initial selection is supplied with
Claude's `--model` flag instead.

## Usage / quota

The proxy exposes ZCode quota at:

```text
http://127.0.0.1:8788/v1/zcode/balance
```

This includes all active buckets, including promotional Trust Build buckets such as a 100,000,000 token GLM-5.3-Flash bucket, plus the normal Start Plan GLM-5.3 and GLM-5.3-Flash buckets.

When launched through `zcode-cli-launcher.cmd`, typing `/usage` shows those
buckets as daily limits with exact used, remaining, total, and reset values.

## Slash-command compatibility

Normal local Claude Code commands are still handled by the real Claude Code
binary. This includes local settings/context/plugin/MCP/session commands and
model-driven skills that do not require Anthropic-hosted account services.

The launcher owns three compatibility cases:

- `/usage` renders ZCode's real daily quota buckets.
- `/model` remains Claude-native, while the proxy maps the selected
  `glm-5.3` or `glm-5.3-flash` request to the corresponding real ZCode model.
- `/effort` remains Claude-native, while the proxy maps Claude's request effort
  to ZCode's `low`, `high`, or `max` thinking level.

Commands that cannot truthfully operate against the ZCode gateway are stopped
before Claude attempts a real Anthropic account/cloud call. They display a
short explanation and return to the normal TUI with Esc.

Current intercepted groups for Claude Code 2.1.287:

```text
Account / quota:
  /fast /usage-credits /extra-usage /upgrade /rate-limit-options
  /limit-reset /passes /powerup /pro-trial-expired /privacy-settings
  /login /logout

Anthropic cloud / remote:
  /schedule /autofix-pr /remote-env /remote-control /mobile
  /__remote-workflow /workflow-launch-exec /team-onboarding
  /cloud-plugins /install-github-app /install-slack-app /web-setup
  /ultraplan /ultrareview

Claude Design:
  /design /design-sync /design-consent /design-revoke /design-login

Provider switching inside the isolated ZCode session:
  /setup-bedrock /setup-vertex

Anthropic submission:
  /feedback /bug

Hosted research:
  /deep-research

External Anthropic product extras:
  /stickers
```

`/deep-research` is intercepted because Claude Code exposes it as a dynamic
workflow built around hosted web-search/fetch capabilities rather than a plain
Messages API model turn. ZCodeToAPI does not pretend that Anthropic-hosted
research infrastructure exists behind the ZCode gateway. Normal prompts can
still use local tools, MCP search providers, or other search integrations
configured by the user.

## Tool-call bridge

The proxy supports Anthropic tool use:

```text
tools[] from Claude Code
  -> prompt GLM to return strict JSON tool calls
  -> convert GLM JSON into Anthropic tool_use blocks
  -> Claude Code executes Bash/Edit/Read/etc.
  -> Claude Code sends tool_result blocks back
  -> proxy feeds tool results back to GLM
```

If GLM answers in prose instead of JSON, the proxy runs a repair pass asking it to convert the intended action into strict tool-call JSON.

## Debug dumps

Set this to save every request and response:

```bat
set ZCODE_CLAUDE_PROXY_DUMP_DIR=C:\path\to\logs
```

Each request produces files like:

```text
<request-id>-request.json
<request-id>-response.json
```

Do not share logs publicly unless you have reviewed them. Claude Code requests can include prompts, file contents, tool schemas, and tool outputs.

## Docker test pattern

The tested Docker setup uses the Debian-based `node:22` image, not `node:22-alpine`.

Do not use Alpine for this Claude Code test. In repeated runs, `node:22-alpine` under the non-root `node` user caused Claude Code's Bash tool to fail with `No suitable shell found`. Root mode is also not usable with `--dangerously-skip-permissions`, because Claude Code blocks that combination.

Use this pattern instead:

```powershell
docker run --rm --user node `
  -v "C:\path\to\app:/work/app" `
  -e ANTHROPIC_BASE_URL=http://host.docker.internal:8788 `
  -e ANTHROPIC_API_KEY=local `
  -e ANTHROPIC_MODEL=claude-sonnet-4-5-20250929 `
  -e SHELL=/bin/bash `
  node:22 bash -lc "export npm_config_prefix=/tmp/npm-global; export PATH=/tmp/npm-global/bin:`$PATH; npm install -g @anthropic-ai/claude-code >/tmp/npm-install.log 2>&1 && cd /work/app && claude --verbose --print --output-format stream-json --permission-mode bypassPermissions < .create-prompt.txt"
```

For the local proxy, `low` thinking is the recommended starting point for Claude Code stability:

```bat
set ZCODE_CLAUDE_THINKING_LEVEL=low
py claude_proxy.py --host 127.0.0.1 --port 8788
```

The heavier `high` and `max` modes are available, but they can make Claude Code integration tests much slower because Claude Code sends very large `thinking.budget_tokens` values. The proxy ignores client-provided Anthropic thinking budgets unless `ZCODE_CLAUDE_RESPECT_CLIENT_THINKING=1` is set.

The final validation run created a multi-file notes app, then edited it to add priority and search. Evidence is saved under:

```text
zcode-recovery-logs\claude-final-test
```

Validated outputs from that run:

```text
create-exit.txt = EXIT:0
edit-exit.txt   = EXIT:0
node --check app\src\app.js
node --check app\src\storage.js
```

Useful flags:

```text
--verbose
--print
--output-format stream-json
--permission-mode bypassPermissions
--dangerously-skip-permissions
```

Run Docker as a non-root user when using `--dangerously-skip-permissions`; Claude Code refuses that flag as root.

## Current limitations

- This is an unofficial compatibility layer.
- It depends on the current ZCode Start Plan request template staying accepted.
- Token accounting is mapped from ZCode usage and may not match Anthropic billing semantics.
- Streaming is Anthropic-style SSE, but the backend call itself is still buffered through the current ZCode client.
- Tool-call quality depends on GLM following the strict JSON instruction or succeeding in the repair pass.
- Simple Claude Code tool loops work; heavier multi-file Claude Code runs can still stall on provider/model output and need more hardening.
