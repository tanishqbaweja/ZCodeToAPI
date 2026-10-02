# ZCodeToAPI

This project lets you use your own ZCode account from a Python script or from your own app.

In simple terms:

```text
Your app or script -> ZCode Start Plan endpoint -> GLM-5.3 / GLM-5.3-Flash -> response
```

It does **not** open or control the ZCode desktop app for each message. It still uses ZCode's backend and a real ZCode account that is allowed to use Start Plan.

## What this can do

You can use this project to:

- test the ZCode Start Plan endpoint from Python
- send prompts to GLM-5.3 or GLM-5.3-Flash through the ZCode Start Plan backend
- use the endpoint inside your own local app
- run a local OpenAI-compatible proxy server for tools that support custom OpenAI base URLs
- run a local Claude/Anthropic-compatible proxy server for Claude Code
- launch real Codex or Claude Code sessions through isolated local proxy settings

This is **not** a raw Z.ai API key client. It is an unofficial interoperability client for the ZCode Start Plan endpoint.

## Important rules

- Use only accounts you own or are authorized to use.
- Do not commit real credentials, tokens, or `.env` files.
- Do not print or log tokens, API keys, CAPTCHA values, or decrypted credentials.
- Do not try to bypass account limits, entitlement checks, CAPTCHA, or anti-abuse checks.
- The endpoint can change at any time because this is not an official public API.

## Files in this folder

```text
README.md                         Beginner setup guide
INTEGRATION.md                    Notes for app developers
OPENAI_PROXY.md                   OpenAI-compatible proxy guide
CLAUDE_PROXY.md                   Claude Code / Anthropic Messages proxy guide
zcode_direct_client.py            Main Python client
openai_proxy.py                   Local OpenAI-compatible proxy server
claude_proxy.py                   Local Claude-compatible proxy server
start_plan_request_template.json  Request template copied from the working ZCode flow
.env.example                      Safe example environment file
examples/custom_system_prompt.txt Experimental prompt example
```

## What you need first

Before running this, make sure you have:

1. ZCode installed.
2. A ZCode account with Start Plan access.
3. Python installed on your computer.
4. The account signed in at least once in the ZCode desktop app.

The script uses the local ZCode login stored on the computer. That is how it knows which account to use.

## Step 1: open this folder in Command Prompt

Open Command Prompt or Terminal inside this project folder.

A simple way on Windows:

1. Open this folder in File Explorer.
2. Click the address bar.
3. Type `cmd`.
4. Press Enter.

A Command Prompt should open directly inside this folder.

## Step 2: install Python packages

Run this once:

```bat
py -m pip install requests cryptography
```

If that finishes without errors, continue.

## Step 3: test the direct client

Run:

```bat
py zcode_direct_client.py "Hello how are you"
```

Expected result:

```text
HTTP 200 OK
Response:
...
```

If you see `HTTP 200 OK`, the basic direct client is working.

## Step 4: run the OpenAI-compatible proxy

The proxy lets OpenAI-compatible tools talk to this project through a local server.

Start it with:

```bat
py openai_proxy.py --host 127.0.0.1 --port 8787
```

It will print:

```text
ZCode OpenAI-compatible proxy listening on http://127.0.0.1:8787/v1
```

Keep this window open while another app uses the proxy.

Use these settings in apps that support a custom OpenAI-compatible API:

```text
Base URL: http://127.0.0.1:8787/v1
API key: anything, unless you set ZCODE_PROXY_API_KEY
Model: glm-5.3-flash
```

The proxy also exposes these aliases, all routed to the same backend GLM-5.3-Flash model:

```text
glm-5.3-flash
GLM-5.3-Flash
zcode-glm-5.3-flash
```

More details are in `OPENAI_PROXY.md`.

The proxy now has a Responses API function-call bridge for Codex-style agent loops. It converts JSON action requests from the model into OpenAI-style `function_call` response items.

For debugging, set `ZCODE_PROXY_DUMP_DIR` before starting the proxy. This saves each incoming request and generated response as JSON files.

For Claude Code-style clients, see `CLAUDE_PROXY.md`.

Both proxies support ZCode thinking levels:

```text
low, high, max
```

Both proxies expose current quota/balance:

```text
/v1/zcode/balance
```

## Claude Code launcher

On Windows, the easiest Claude Code path is:

```bat
zcode-cli-launcher.cmd
```

Choose Claude, then choose the real ZCode model and the initial thinking level.
The launcher keeps its credentials, patched compatibility runtime, and writable
Claude configuration isolated from your normal installed Claude Code setup.

Inside that Claude Code session:

- `/model glm-5.3` switches subsequent inference to real `GLM-5.3`.
- `/model glm-5.3-flash` switches subsequent inference to real `GLM-5.3-Flash`.
- `/effort` changes the ZCode thinking level used on subsequent requests.
- `/usage` is intercepted by the launcher and shows ZCode's real **daily**
  model buckets instead of Claude's unrelated 5-hour/weekly subscription labels.
- Ordinary local Claude Code commands continue through the real CLI normally.
- Commands that specifically require an Anthropic account, billing system,
  Claude cloud agent, Claude Design, or another provider setup are intercepted
  with an explicit ZCodeToAPI explanation instead of falling into login or
  subscription errors.

The proxy reads Claude Code's actual request model and
`output_config.effort`, so changing these controls in Claude changes the
upstream ZCode request rather than only changing the text shown in the TUI.

The compatibility sweep for Claude Code 2.1.287 also explicitly handles
Anthropic-only commands such as `/fast`, `/deep-research`, `/schedule`,
`/remote-env`, `/remote-control`, `/usage-credits`, `/extra-usage`,
Claude Design commands, hosted GitHub/cloud-plugin setup, `/login`, and
`/logout`. See `CLAUDE_PROXY.md` for the full grouped list.

## How to use a different ZCode account

The simplest method:

1. Open the official ZCode desktop app.
2. Sign out.
3. Sign in with the other ZCode account.
4. Confirm that account has Start Plan access.
5. Run this project again.

The script normally uses the ZCode account stored for the current Windows user.

For multiple accounts, the cleanest setup is one Windows user profile per ZCode account. That keeps each account's local login storage separate.

## Using this from your own app

The easiest option is to call the script as a subprocess:

```python
import subprocess

result = subprocess.run(
    ["py", "zcode_direct_client.py", "Hello how are you"],
    text=True,
    capture_output=True,
)

print(result.stdout)
```

For OpenAI-compatible tools, run the proxy instead:

```bat
py openai_proxy.py --host 127.0.0.1 --port 8787
```

Then configure your tool to use:

```text
http://127.0.0.1:8787/v1
```

## About system prompts

Current tested behavior:

```text
Stock ZCode template + normal user messages:
works

Replacing the core system prompt directly:
was rejected with code 3012 in tests

Keeping the stock template and steering behavior through user messages:
works
```

So for now, keep the behavioral contents of `start_plan_request_template.json`
unchanged. The checked-in template replaces the captured Windows user path
with a placeholder; `zcode_direct_client.py` restores the local workspace path
in memory at runtime so no machine-specific username is stored in Git.

If you want your app to give the model a custom identity or behavior, put that instruction in the message history instead of replacing the system prompt.

Example:

```text
User message 1:
You are Trebell Code from now.

User message 2:
Who are you?
```

That worked in testing while keeping the backend-accepted request shape.

## Common errors

### `HTTP 200 OK`

Good. The request worked.

### `code 3012` or `request has been blocked due to unusual activity`

The backend rejected the request.

Common causes:

- the system prompt/template was changed too much
- the request shape changed too much
- the account/session/token has an issue
- the backend anti-abuse check was triggered

Try again with the stock template and a simple prompt:

```bat
py zcode_direct_client.py "Reply only OK."
```

### Python says a package is missing

Install dependencies again:

```bat
py -m pip install requests cryptography
```

### It uses the wrong account

The script normally uses the account signed in to ZCode on the current Windows user.

To switch accounts, sign out/in inside the official ZCode desktop app first, then run the script again.

## Quick start

```bat
py -m pip install requests cryptography
py zcode_direct_client.py "Hello how are you"
```

Then, for OpenAI-compatible apps:

```bat
py openai_proxy.py --host 127.0.0.1 --port 8787
```

Or, for Claude Code / Anthropic-compatible apps:

```bat
py claude_proxy.py --host 127.0.0.1 --port 8788
```
