# Claude Code Proxy

This proxy exposes a local Anthropic Messages API compatible endpoint and forwards model work to the ZCode Start Plan backend.

```text
Claude Code -> local Claude proxy -> ZCode Start Plan backend -> GLM-5.3-Flash
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

Launcher sessions also expose ZCode quota through Claude Code's native subscription usage path. The proxy returns `anthropic-ratelimit-unified-*` headers on model responses and implements local `/api/oauth/profile` and `/api/oauth/usage` compatibility endpoints, including the launcher's `/zcode-oauth-bridge/...` prefixed form. Claude's built-in `/usage` therefore reads ZCode utilization/reset state directly, including before the first inference request.

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

The model name is only an Anthropic-compatible alias. The actual backend model used by this project is `GLM-5.3-Flash` through ZCode Start Plan.

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

## Usage / quota

The proxy exposes ZCode quota at:

```text
http://127.0.0.1:8788/v1/zcode/balance
```

This includes all active buckets, including promotional Trust Build buckets such as a 100,000,000 token GLM-5.3-Flash bucket, plus the normal Start Plan GLM-5.3 and GLM-5.3-Flash buckets.

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
  node:22 bash -lc "export npm_config_prefix=/tmp/npm-global; export PATH=/tmp/npm-global/bin:`$PATH; npm install -g @anthropic-ai/claude-code >/tmp/npm-install.log 2>&1 && cd /work/app && claude --bare --verbose --print --output-format stream-json --permission-mode bypassPermissions < .create-prompt.txt"
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
--bare
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
