# OpenAI-Compatible Proxy

This file explains how to run the local proxy server.

The proxy gives you a local OpenAI-style API that forwards requests to the ZCode Start Plan backend.

```text
OpenAI-compatible app -> local proxy -> ZCode Start Plan backend -> GLM-5.3-Flash
```

## Start the proxy

Open Command Prompt inside this folder and run:

```bat
py openai_proxy.py --host 127.0.0.1 --port 8787
```

Keep that window open.

The base URL is:

```text
http://127.0.0.1:8787/v1
```

The model names are aliases for the same backend model:

```text
glm-5.3-flash
GLM-5.3-Flash
zcode-glm-5.3-flash
```

## Settings for OpenAI-compatible apps

Use:

```text
Base URL: http://127.0.0.1:8787/v1
API key: anything
Model: glm-5.3-flash
```

The API key is ignored by default because the proxy is local.

If you want to require a local proxy key, set:

```bat
set ZCODE_PROXY_API_KEY=your-local-proxy-key
py openai_proxy.py --host 127.0.0.1 --port 8787
```

Then clients must send:

```text
Authorization: Bearer your-local-proxy-key
```

Do not confuse this proxy key with your real ZCode token. The proxy key is only for protecting your local proxy server.

## Test with PowerShell

Start the proxy first, then open a second terminal and run:

```powershell
$body = @{
  model = "glm-5.3-flash"
  messages = @(
    @{ role = "user"; content = "Reply only OK." }
  )
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8787/v1/chat/completions" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

Expected result:

```text
choices[0].message.content = OK
```

## Test the Responses API

Some tools use `/v1/responses` instead of `/v1/chat/completions`.

The proxy also supports Responses `function_call` output items. When a request includes `tools`, the proxy asks GLM to return a JSON tool-call plan and converts it to OpenAI-style response output.

```powershell
$body = @{
  model = "glm-5.3-flash"
  input = "Reply only OK."
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8787/v1/responses" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

## Supported endpoints

```text
GET  /v1/models
GET  /v1/models/{model}
GET  /v1/zcode/balance
GET  /v1/zcode/claim-preview
GET  /v1/usage
POST /v1/chat/completions
POST /v1/responses
POST /v1/completions
POST /v1/zcode/claim
GET  /health
```

## Start Plan claim API

`GET /v1/zcode/claim-preview` returns the plans currently claimable for the
signed-in ZCode account. This is the same read-only preview used by the ZCode
desktop client before it shows a Claim action.

`POST /v1/zcode/claim` accepts:

```json
{
  "plan_id": "optional exact preview plan id",
  "captcha_verify_param": "optional fresh Aliyun verification value",
  "captcha_region": "optional region override",
  "interactive_verification": false,
  "verification_timeout": 120
}
```

When `plan_id` is omitted, ZCodeToAPI picks the largest currently claimable
GLM-5.3-Flash grant from the live preview. When the CAPTCHA value is omitted
and `interactive_verification` is false, the proxy returns HTTP 428 with
`captcha_required: true`. On a local desktop, setting
`interactive_verification: true` runs the same official Aliyun SDK flow used
by ZCode in a small local browser page. The verifier attempts Aliyun's
traceless flow automatically; user interaction is only needed if Aliyun
escalates that attempt to a challenge. The ZCode desktop app does not need to
be opened.

The short-lived CAPTCHA verification value is redacted from proxy request
dumps.

## Thinking level

The proxy can route ZCode's GLM thinking level to the backend. Accepted values are:

```text
low
high
max
```

Set the default for all requests:

```bat
set ZCODE_PROXY_THINKING_LEVEL=max
py openai_proxy.py --host 127.0.0.1 --port 8787
```

Per-request overrides are also supported:

```json
{
  "zcode_thinking_level": "high",
  "reasoning": { "effort": "high" }
}
```

Unknown values fall back to `max`.

## Usage / quota

The proxy exposes ZCode quota at:

```text
http://127.0.0.1:8787/v1/zcode/balance
```

This includes all active buckets, including promotional Trust Build buckets such as a 100,000,000 token GLM-5.3-Flash bucket, plus the normal Start Plan GLM-5.3 and GLM-5.3-Flash buckets. Each balance includes `total_units`, `used_units`, `remaining_units`, `percentage_remaining`, `plan_id`, `period`, and `expires_at`.

## Streaming

The proxy accepts `stream: true`.

For now, it does not stream tokens live from ZCode one-by-one. It waits for the ZCode response, then sends the answer back in OpenAI-style streaming format.

That is enough for many tools, but not a perfect OpenAI streaming clone.

## Codex-style setup

Codex-style tools usually need a custom OpenAI-compatible base URL and a Responses API wire mode.

When Codex is launched through `zcode-cli-launcher.cmd`, the proxy also serves
a Codex model catalog. This lets Codex keep its own native `/model` picker
while showing only the real ZCode models:

```text
glm-5.3-flash
glm-5.3
```

The catalog exposes Codex reasoning levels `low`, `high`, and `xhigh`;
`xhigh` maps to ZCode `max`. The proxy reads the model and
`reasoning.effort` from every request, so a model/effort change made inside
Codex changes the actual ZCode backend request rather than just the UI.

The Codex catalog reuses the installed Codex version's own model instruction
metadata (or, when needed, its embedded fallback coding instructions) at
runtime. Those instructions are not copied into this repository.

When the launcher owns the interactive TUI, its exact `/usage` command shows
these ZCode buckets directly rather than opening Codex's ChatGPT-account usage
screen. Normal Start Plan entitlements are shown as daily; a one-time promo is
shown as one-time with an expiry timestamp.

For Codex CLI config, add something like this to your Codex config file:

```toml
[model_providers.zcode-glm]
name = "ZCode GLM"
base_url = "http://127.0.0.1:8787/v1"
model_catalog_url = "http://127.0.0.1:8787/v1/codex/models"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
requires_openai_auth = false

[profiles.zcode-glm]
model_provider = "zcode-glm"
model = "glm-5.3-flash"
```

Then run Codex with that profile.

A copy of this config is also included here:

```text
examples/codex-config-example.toml
```

For tools that use OpenAI-compatible environment variables instead, use values like:

```bat
set OPENAI_BASE_URL=http://127.0.0.1:8787/v1
set OPENAI_API_KEY=local
set OPENAI_MODEL=glm-5.3-flash
```

Some tools use different variable names or config files. The important values are:

```text
base_url = http://127.0.0.1:8787/v1
api_key = local
model = glm-5.3-flash
```

If you set `ZCODE_PROXY_API_KEY`, then `OPENAI_API_KEY` must match that local proxy key.

## Debug dumps

To save every proxy request and generated response for debugging:

```bat
set ZCODE_PROXY_DUMP_DIR=proxy-dumps
py openai_proxy.py --host 127.0.0.1 --port 8787
```

Each request creates files like:

```text
<request-id>-request.json
<request-id>-response.json
```

## Current limitations

- This is an unofficial compatibility layer.
- It uses the current machine's signed-in ZCode account.
- It keeps the stock ZCode request template because that is what the backend currently accepts.
- It maps OpenAI messages into a plain-text prompt before sending them to ZCode.
- It supports practical Responses API `function_call` bridging for Codex-style tools, including repair/fallback for planning text.
- OpenAI's server-side Responses `web_search` tool is not implemented. The
  ZCodeToAPI Codex launcher therefore rejects native `--search`; use local,
  browser, or MCP search tools instead.
- True token-by-token streaming is not implemented yet.
- Function calling is a prompt-to-JSON bridge rather than native model-side tool calling.
- If ZCode changes its backend validation, this proxy may need updates.

## Safety

- Keep the proxy bound to `127.0.0.1` unless you know exactly what you are doing.
- Do not expose it publicly.
- Do not log real ZCode tokens or credential files.
- Use only accounts you own or are authorized to use.
