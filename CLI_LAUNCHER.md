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

The Claude proxy maps the selected model's live ZCode balance into Claude Code's native OAuth usage schema and unified rate-limit state. The launcher-local Claude runtime requests the local bridge directly:

```text
GET /zcode-oauth-bridge/api/oauth/profile
GET /zcode-oauth-bridge/api/oauth/usage
```

Claude's built-in `/usage` therefore shows the ZCode-backed utilization and reset times **even before the first model prompt**. This was verified in the actual interactive TUI, not only `--print` mode.

- For `GLM-5.3`, the native current-session meter maps to the 3M Start Plan bucket.
- For `GLM-5.3-Flash`, the bucket currently serving requests is the native current-session meter. The other Flash bucket is kept as the secondary native meter, so the normal 5M pool and promotional 100M Trust Build pool remain distinct.
- Claude Code's labels (`Current session`, `Current week`) are built into Claude's UI; the percentages and reset timestamps come from ZCode.
- `GET /v1/zcode/balance` remains available when exact raw units (`used_units`, `remaining_units`, `total_units`) are needed.

Claude Code's labels (`Current session`, `Current week`) remain Claude's own fixed UI labels. Their percentages and reset timestamps are populated from ZCode. The launcher never sends the local OAuth token or ZCode credentials to Anthropic for this feature.

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

Interactive mode also prints the current quota before the CLI opens and the updated quota after the CLI exits. Promotional buckets such as the 100M daily GLM-5.3-Flash grant remain separate from normal Start Plan balances.

### Claude Code cache behavior

The launcher/proxy keeps Claude Code's stable system instructions and tool schemas at the front of the ZCode prompt so ZCode's own prefix cache can reuse them. The volatile Claude billing telemetry header is preserved but moved after that stable prefix. In fresh-process testing this improved cross-session cache-read from roughly 45% on the warm-up request to about 95.4-95.7% on subsequent fresh Claude Code processes; later turns inside one session have reached about 98.7%.

The proxy maps ZCode backend usage into OpenAI/Anthropic-compatible response fields. The raw ZCode buckets are still available through:

```bat
zcode-cli-launcher.cmd usage --json
```

## Smoke-tested

Validated locally:

```text
py -m py_compile zcode-direct-client\zcode_cli_launcher.py
zcode-cli-launcher.cmd models
zcode-cli-launcher.cmd usage
zcode-cli-launcher.cmd run claude --prompt "Reply only OK." --claude-thinking low
```

Codex was not on `PATH` during this launcher smoke, so Codex mode is implemented and wired, but needs either a `codex` executable on `PATH` or `--codex-bin <path>` for a real run.
