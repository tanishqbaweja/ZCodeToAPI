# Codex + Claude Code Launcher

`zcode_cli_launcher.py` is a separate wrapper for running Codex CLI and Claude Code through the local ZCode proxy stack.

It does **not** patch or rewrite the Codex or Claude Code installations on the machine. It only:

- starts `openai_proxy.py` and/or `claude_proxy.py` as child processes;
- sets child-process environment variables such as `OPENAI_BASE_URL` or `ANTHROPIC_BASE_URL`;
- creates a cached project-local copy of Claude Code's native executable when Claude's hard-coded OAuth base must be redirected to the localhost proxy;
- sets proxy thinking environment variables;
- saves command logs, proxy dumps, and before/after usage snapshots under `zcode-recovery-logs\cli-launcher`;
- shuts the proxies down when the run finishes unless `--keep-proxy` is used.

From the project root:

```bat
zcode-cli-launcher.cmd models
zcode-cli-launcher.cmd usage
zcode-cli-launcher.cmd interactive
```

The normal workflow is to **double-click `zcode-cli-launcher.cmd`**. It asks which real CLI to open, the actual ZCode backend model, and the thinking level. It then starts the corresponding proxy and hands the terminal over to the installed interactive Codex or Claude Code CLI. There is no launcher-level prompt box in this mode.

For Claude Code, the launcher creates a **per-run isolated secure-storage directory** containing a local-only subscription-shaped OAuth credential. The access token is just `zcode-local-oauth`, is accepted only by the localhost proxy, and is never sent to Anthropic. You do **not** need an Anthropic login or a real Anthropic API key. Your normal `~/.claude` credentials are not modified.

Claude Code currently hard-codes its subscription OAuth/admin base to Anthropic and restricts `CLAUDE_CODE_CUSTOM_OAUTH_URL` to a small Anthropic-owned allowlist. To make native `/usage` local without altering the global installation, the launcher creates a **project-local compatibility runtime** under `zcode-direct-client\.runtime`. In that copy only, one allowlisted OAuth base string is replaced in-place by the local ZCode OAuth bridge URL. The installed `claude.exe`, npm wrapper, and normal Claude configuration remain untouched. The cached copy is automatically rebuilt when the installed Claude executable changes.

Before opening Claude Code, the launcher runs `claude auth status` against that isolated credential store and refuses to launch unless Claude reports `loggedIn: true`, `authMethod: claude.ai`, and `subscriptionType: max`. This prevents the TUI from silently falling back to the `Not logged in / Run /login` state.

The launcher deliberately does **not** use Claude Code's `--bare` mode. Normal Claude Code session behavior stays enabled, including its complete model-facing system instructions, CLAUDE.md discovery, installed plugins/hooks, memory, LSP integration, and normal tool catalog. The proxy carries Claude Code's complete ordered system text into the GLM conversation while leaving ZCode's validated stock system prompt untouched.

The launcher also passes the chosen values to Claude Code itself with `--model` and `--effort`, so its header/status reflects the selected GLM model and thinking level instead of showing stale defaults.

### Claude `/usage`

The local OAuth bridge still provides Claude's internal profile/usage state,
but Claude's built-in usage page labels its windows with Anthropic-specific
5-hour/week terminology. ZCode's actual Start Plan limits are daily. The
launcher therefore intercepts the exact `/usage` command and renders the real
ZCode buckets itself, including used, remaining, total, and reset times.

### Codex interactive compatibility

On Windows, Codex 0.160.0 is relayed through WinPTY so its real interactive TUI
works even when the parent shell is not a usable terminal.

Codex uses a short isolated `CODEX_HOME` on the same drive as the project.
This avoids Windows app-server socket path limits and avoids filling the
system drive. Normal OpenAI auth, global state, saved sessions, sockets, and
the heavyweight global plugin cache are not copied into that isolated home.
Lightweight local capabilities such as AGENTS.md, skills, and rules are seeded
when available.

The isolated `config.toml` is created once and then preserved. Codex-owned
changes such as installed plugins therefore survive later launcher sessions.
The ZCode provider and initial model are enforced per invocation with
command-line overrides, while the requested initial reasoning effort is reset
in the isolated config immediately before launch. The native `/model` picker
can still change model/effort for the active session, and preserving the rest
of the local config does not allow a stale provider selection to bypass ZCode.

For `/plugins`, the launcher mirrors Codex's existing curated plugin sources
into one shared ZCodeToAPI-owned marketplace on the project drive. Every
isolated Codex home points to that shared marketplace instead of copying the
large global plugin runtime/cache. In live testing Codex showed 49 available
plugins, and a `superpowers` add/list/remove cycle persisted correctly across
separate launcher invocations.

Codex keeps its native `/model` UI. The local provider catalog exposes only:

```text
GLM-5.3-Flash
GLM-5.3
```

Reasoning choices map to ZCode as:

```text
Low        -> low
High       -> high
Extra high -> max
```

The proxy reads Codex's request model and `reasoning.effort` on every turn. A
real TUI test switched `GLM-5.3-Flash · xhigh` to `GLM-5.3 · high`; the
following upstream request was verified as backend `GLM-5.3` with ZCode
thinking `high`.

Codex `/usage` is also launcher-owned because OpenAI account usage is unrelated
to ZCode quota. It displays the real ZCode buckets and their actual periods:
normal Start Plan entitlements are daily, while the Trust Build promotional
entitlement is one-time and is shown with its expiration timestamp.

The Codex 0.160.0 service-only slash commands currently intercepted are:

```text
/daybreak
/apps
/voice
/app
/logout
/feedback
```

Normal local Codex commands remain native, including `/model`,
`/permissions`, `/review`, `/agents`, `/subagents`, `/worktree`,
`/mcp`, `/plugins`, `/skills`, `/hooks`, `/memories`, `/import`,
`/resume`, `/fork`, `/status`, `/diff`, `/compact`, and `/plan`.

When explicit top-level commands are supplied through `--codex-args`, the
launcher blocks OpenAI-hosted/account commands before starting the proxy:
`login`, `logout`, `cloud`/ `cloud-tasks`, `app`, and
`remote-control`. The local `exec-server` remains usable; only remote
registration forms are blocked. Local commands such as `mcp`, `plugin`,
`doctor`, `sandbox`, `resume`, `fork`, `exec`, and `review` remain
available.

Advanced arguments are also checked so a launcher-managed session cannot
silently leave the ZCode route. Native OpenAI `--search`, `--oss` /
`--local-provider`, remote app-server routing, unsupported model IDs, and
provider-defining `-c/--config` overrides are rejected. Supported
`--model glm-5.3` and `--model glm-5.3-flash` overrides remain valid, as do
provider-independent Codex config overrides.

Available backend models currently verified through the Start Plan endpoint:

```text
GLM-5.3-Flash
GLM-5.3
```

The internal OpenAI/Anthropic compatibility model names are intentionally hidden from the interactive menu.

Run Claude Code through GLM-5.3-Flash:

```bat
zcode-cli-launcher.cmd run claude --prompt "Reply only OK." --claude-thinking low
```

Run Codex through GLM-5.3-Flash:

```bat
zcode-cli-launcher.cmd run codex --prompt "Reply only OK." --codex-thinking low
```

Run both, sequentially, through separate proxies:

```bat
zcode-cli-launcher.cmd run both --prompt "Implement the requested change." --thinking low
```

If a CLI is not on `PATH`, pass its exact executable path:

```bat
zcode-cli-launcher.cmd run codex --codex-bin "C:\path\to\codex.cmd" --prompt "Reply only OK."
zcode-cli-launcher.cmd run claude --claude-bin "C:\path\to\claude.cmd" --prompt "Reply only OK."
```

To override the default CLI arguments completely:

```bat
zcode-cli-launcher.cmd run codex --prompt "ignored if your args provide their own prompt" --codex-args "exec --model glm-5.3-flash Your prompt here"
zcode-cli-launcher.cmd run claude --prompt "Prompt via stdin" --claude-args "--bare --verbose --print --output-format stream-json --permission-mode bypassPermissions"
```

## Model selector

The interactive selector shows actual backend models, not protocol aliases. Both `GLM-5.3-Flash` and `GLM-5.3` have been verified directly against the current Start Plan endpoint.

Compatibility names required by Codex/OpenAI or Claude/Anthropic are handled internally by the launcher/proxies.

Before starting a proxy, the launcher checks its port for a stale proxy left by an interrupted earlier session. It only terminates a listener when it can verify that the process belongs to this project's corresponding proxy script; unrelated processes are never killed automatically.

## Thinking mapping

Accepted launcher thinking levels:

```text
low
high
max
```

Codex route:

```text
ZCODE_PROXY_THINKING_LEVEL=low|high|max
```

Claude route:

```text
ZCODE_CLAUDE_THINKING_LEVEL=low|high|max
```

Claude Code's own `thinking.budget_tokens` is ignored by default so it cannot silently override the selected level. Use `--respect-client-thinking` only when you intentionally want Claude Code's client-side budget to influence the backend level.

## Usage mapping

The launcher stores:

```text
<run-dir>\codex-usage-before.json
<run-dir>\codex-usage-after.json
<run-dir>\claude-usage-before.json
<run-dir>\claude-usage-after.json
```

Interactive mode also prints the current quota before the CLI opens and the updated quota after the CLI exits. Promotional buckets such as the 100M Trust Build GLM-5.3-Flash grant remain separate from normal Start Plan balances and retain their own entitlement period.

### Claude Code cache behavior

The launcher/proxy keeps Claude Code's stable system instructions and tool schemas at the front of the ZCode prompt so ZCode's own prefix cache can reuse them. The volatile Claude billing telemetry header is preserved but moved after that stable prefix. In fresh-process testing this improved cross-session cache-read from roughly 45% on the warm-up request to about 95.4-95.7% on subsequent fresh Claude Code processes; later turns inside one session have reached about 98.7%.

The proxy maps ZCode backend usage into OpenAI/Anthropic-compatible response fields. The raw ZCode buckets are still available through:

```bat
zcode-cli-launcher.cmd usage --json
```

## Claiming promotional Start Plan grants

The launcher can query and claim ZCode's manual Start Plan grants without
opening the ZCode desktop application:

```bat
zcode-cli-launcher.cmd claim-preview
zcode-cli-launcher.cmd claim
```

`claim-preview` is read-only and shows the live plan IDs and grant amounts
returned by `/api/v1/zcode-plan/billing/preview`.

`claim` prefers the current Trust Build GLM-5.3-Flash grant of at least 100M
tokens and uses ZCode's real `/api/v1/zcode-plan/billing/claim` endpoint.
The plan ID is discovered from the live preview instead of being hardcoded.
Claims require a fresh Aliyun CAPTCHA verification. The launcher uses the
official Aliyun SDK and attempts traceless verification first; if Aliyun
upgrades the attempt to an interactive challenge, only the verifier page needs
to be completed.

For automation that already has a fresh verification value:

```bat
zcode-cli-launcher.cmd claim --captcha-verify-param "<value>"
```

To inspect eligibility without opening a verifier:

```bat
zcode-cli-launcher.cmd claim --no-browser --json
```

## Smoke-tested

Validated locally:

```text
py -m py_compile zcode-direct-client\zcode_cli_launcher.py
zcode-cli-launcher.cmd models
zcode-cli-launcher.cmd usage
zcode-cli-launcher.cmd run claude --prompt "Reply only OK." --claude-thinking low
```

Codex 0.160.0 is also verified in the real interactive TUI. Tested paths
include the native GLM model/reasoning picker, dynamic model+effort switching,
the ZCode `/usage` overlay, the complete service-only slash-command sweep,
hosted/account top-level interception, protected provider/search routes, and a
real forwarded local `features list` command.
