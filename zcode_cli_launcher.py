#!/usr/bin/env python3
"""Isolated launcher for interactive Codex CLI and Claude Code through ZCode.

This wrapper does not patch or reconfigure the globally installed CLIs. It starts
local proxy subprocesses, passes temporary environment variables only to child
processes, records usage snapshots, and shuts the proxies down by default.
"""

from __future__ import annotations

import argparse
import json
import mmap
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
RUN_ROOT = PROJECT_ROOT / "zcode-recovery-logs" / "cli-launcher"

HOST = "127.0.0.1"
CODEX_PORT = 8787
CLAUDE_PORT = 8788

# Actual backend models verified against ZCode Start Plan.
BACKEND_MODELS = ["GLM-5.3-Flash", "GLM-5.3"]
THINKING = ["low", "high", "max"]
CLAUDE_OAUTH_ALLOWLIST_SOURCE = b"https://beacon.claude-ai.staging.ant.dev"

# These slash commands are features of Anthropic's own account/billing/cloud
# services, not of the Messages API. Letting them continue inside a ZCode
# gateway session produces confusing login, subscription, or remote-service
# errors. Intercept them explicitly while leaving normal local Claude Code
# commands untouched.
CLAUDE_UNSUPPORTED_SLASH_COMMANDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "/fast": (
        "Anthropic Fast Mode is not available through ZCodeToAPI.",
        (
            "ZCode has no equivalent Opus Fast Mode entitlement.",
            "For a faster ZCode session, use /model glm-5.3-flash and choose a lower /effort.",
        ),
    ),
    "/usage-credits": (
        "Anthropic usage credits are not used by ZCodeToAPI.",
        ("Use /usage to see the real ZCode daily model limits.",),
    ),
    "/extra-usage": (
        "Anthropic extra-usage billing is not used by ZCodeToAPI.",
        ("Use /usage to see the real ZCode daily model limits.",),
    ),
    "/upgrade": (
        "Anthropic plan upgrades are not used by ZCodeToAPI.",
        ("ZCode access and quota come from the signed-in ZCode account.",),
    ),
    "/rate-limit-options": (
        "Anthropic rate-limit upgrade options are not used by ZCodeToAPI.",
        ("Use /usage to see the real ZCode daily model limits.",),
    ),
    "/limit-reset": (
        "Anthropic subscription limit resets are not used by ZCodeToAPI.",
        ("ZCode's daily limits and reset times are shown by /usage.",),
    ),
    "/passes": (
        "Anthropic usage passes are not used by ZCodeToAPI.",
        ("ZCode access and quota come from the signed-in ZCode account.",),
    ),
    "/pro-trial-expired": (
        "Anthropic trial/account upgrade flows are not used by ZCodeToAPI.",
        ("ZCode access and quota come from the signed-in ZCode account.",),
    ),
    "/privacy-settings": (
        "Anthropic account privacy settings are not managed by ZCodeToAPI.",
        ("Change Anthropic account settings outside this isolated gateway session.",),
    ),
    "/schedule": (
        "Claude cloud schedules are not available through ZCodeToAPI.",
        ("This command requires Anthropic's hosted cloud-agent infrastructure.",),
    ),
    "/routines": (
        "Claude cloud schedules are not available through ZCodeToAPI.",
        ("This alias requires Anthropic's hosted cloud-agent infrastructure.",),
    ),
    "/autofix-pr": (
        "Claude's hosted PR autofix workflow is not available through ZCodeToAPI.",
        ("This feature launches Anthropic cloud-agent work rather than a local model request.",),
    ),
    "/artifacts": (
        "Claude's published/shared Artifacts browser is not available through ZCodeToAPI.",
        ("This command depends on Claude's account-backed artifacts service.",),
    ),
    "/desktop": (
        "Claude Desktop session handoff is not available through ZCodeToAPI.",
        ("This command transfers the current Claude session into Anthropic's Desktop account surface.",),
    ),
    "/app": (
        "Claude Desktop session handoff is not available through ZCodeToAPI.",
        ("This alias transfers the current Claude session into Anthropic's Desktop account surface.",),
    ),
    "/advisor": (
        "Claude's stronger-model Advisor is not available through ZCodeToAPI.",
        ("ZCodeToAPI can only route the ZCode models exposed by the signed-in Start Plan account.",),
    ),
    "/teleport": (
        "Claude cloud session teleport is not available through ZCodeToAPI.",
        ("This command transfers the session to Anthropic's hosted claude.ai infrastructure.",),
    ),
    "/tp": (
        "Claude cloud session teleport is not available through ZCodeToAPI.",
        ("This alias transfers the session to Anthropic's hosted claude.ai infrastructure.",),
    ),
    "/remote-env": (
        "Claude remote environments are not available through ZCodeToAPI.",
        ("This command requires Anthropic's hosted cloud-agent infrastructure.",),
    ),
    "/remote-control": (
        "Claude remote control is not available through ZCodeToAPI.",
        ("Phone/claude.ai control requires Anthropic's hosted session infrastructure.",),
    ),
    "/session": (
        "Claude cloud-session sharing is not available through ZCodeToAPI.",
        ("This command exposes an Anthropic-hosted cloud-session URL/QR code.",),
    ),
    "/remote": (
        "Claude cloud-session sharing is not available through ZCodeToAPI.",
        ("This alias exposes an Anthropic-hosted cloud-session URL/QR code.",),
    ),
    "/rc": (
        "Claude remote control is not available through ZCodeToAPI.",
        ("This alias requires Anthropic's hosted session infrastructure.",),
    ),
    "/chrome": (
        "Claude in Chrome account integration is not available through ZCodeToAPI.",
        ("Use a normal Claude Code session to configure the claude.ai browser extension.",),
    ),
    "/__remote-workflow": (
        "Claude remote workflows are not available through ZCodeToAPI.",
        ("This internal command requires Anthropic's hosted cloud-agent infrastructure.",),
    ),
    "/workflow-launch-exec": (
        "Claude remote workflow execution is not available through ZCodeToAPI.",
        ("This internal command requires Anthropic's hosted cloud-agent infrastructure.",),
    ),
    "/design": (
        "Claude Design is not available through ZCodeToAPI.",
        ("This command connects to claude.ai/design and requires Anthropic account authorization.",),
    ),
    "/design-sync": (
        "Claude Design Sync is not available through ZCodeToAPI.",
        ("This command uploads or downloads design-system data through claude.ai/design.",),
    ),
    "/design-consent": (
        "Claude Design authorization is not available through ZCodeToAPI.",
        ("This command grants access against a real claude.ai account.",),
    ),
    "/design-revoke": (
        "Claude Design authorization is not available through ZCodeToAPI.",
        ("This command revokes access against a real claude.ai account.",),
    ),
    "/design-login": (
        "Claude Design login is not available through ZCodeToAPI.",
        ("This command authorizes design-system access against a real claude.ai account.",),
    ),
    "/cloud-plugins": (
        "Claude cloud plugins are not available through ZCodeToAPI.",
        ("Local Claude Code plugins continue to work; this command targets Anthropic cloud services.",),
    ),
    "/ultrareview": (
        "Anthropic stronger-model review is not available through ZCodeToAPI.",
        ("ZCodeToAPI can only route the ZCode models exposed by the signed-in Start Plan account.",),
    ),
    "/ultraplan": (
        "Anthropic cloud planning is not available through ZCodeToAPI.",
        ("This command hands a plan to an Anthropic-hosted cloud session for browser review.",),
    ),
    "/install-github-app": (
        "Anthropic's hosted GitHub App setup is not available through ZCodeToAPI.",
        ("Local git, gh, and Claude Code repository tools remain available.",),
    ),
    "/install-slack-app": (
        "Claude Tag / Slack app installation is not available through ZCodeToAPI.",
        ("This setup is tied to a real Anthropic/Claude account and hosted Slack integration.",),
    ),
    "/voice": (
        "Claude voice mode is not available through ZCodeToAPI.",
        ("This command is a claude.ai-only service surface in Claude Code.",),
    ),
    "/setup-bedrock": (
        "Provider setup is disabled inside ZCodeToAPI gateway sessions.",
        ("This session is intentionally routed to ZCode; configure Bedrock in a normal Claude Code session.",),
    ),
    "/setup-vertex": (
        "Provider setup is disabled inside ZCodeToAPI gateway sessions.",
        ("This session is intentionally routed to ZCode; configure Vertex in a normal Claude Code session.",),
    ),
    "/web-setup": (
        "Anthropic web setup is not available through ZCodeToAPI.",
        ("This isolated gateway session does not attach itself to Anthropic's hosted web session.",),
    ),
    "/feedback": (
        "Anthropic product feedback submission is disabled in ZCodeToAPI sessions.",
        ("This prevents an isolated gateway session from trying to submit data to an Anthropic account.",),
    ),
    "/bug": (
        "Anthropic bug-report submission is disabled in ZCodeToAPI sessions.",
        ("This prevents an isolated gateway session from trying to submit data to Anthropic.",),
    ),
    "/share": (
        "Anthropic conversation sharing is disabled in ZCodeToAPI sessions.",
        ("This /bug alias would submit or share the conversation through Anthropic.",),
    ),
    "/login": (
        "Anthropic login is intentionally disabled in ZCodeToAPI sessions.",
        ("The launcher already provides isolated local gateway authentication.",),
    ),
    "/logout": (
        "Anthropic logout is intentionally disabled in ZCodeToAPI sessions.",
        ("The launcher does not modify your normal Claude Code login.",),
    ),
}


def claude_launcher_intercept(typed: str) -> tuple[str, str, str, tuple[str, ...]] | None:
    """Return launcher-owned handling for an exact submitted Claude command."""
    stripped = typed.strip()
    if stripped == "/usage":
        return ("usage", "/usage", "", ())
    if not stripped.startswith("/"):
        return None
    command = stripped.split(None, 1)[0].lower()
    unsupported = CLAUDE_UNSUPPORTED_SLASH_COMMANDS.get(command)
    if unsupported is None:
        return None
    title, detail = unsupported
    return ("unsupported", command, title, detail)


def client_model_id(backend_model: str) -> str:
    return backend_model.lower()


def zcode_client():
    sys.path.insert(0, str(ROOT))
    import zcode_direct_client as zcode  # type: ignore

    return zcode


def norm_thinking(value: str | None) -> str:
    raw = (value or "low").strip().lower()
    raw = {"minimal": "low", "medium": "high", "deep": "max", "maximum": "max"}.get(raw, raw)
    if raw not in THINKING:
        raise SystemExit(f"Bad thinking level {value!r}; use low, high, or max")
    return raw


def usage_mapping() -> dict[str, Any]:
    return {
        "backend": {
            "available_models": BACKEND_MODELS,
            "thinking_body": "thinking={type: enabled}; output_config.effort=low|high|max",
            "usage_fields": ["input_tokens", "output_tokens", "cache_read_input_tokens"],
        },
        "codex_openai_proxy": {
            "base_url_env": "OPENAI_BASE_URL",
            "model_env": "OPENAI_MODEL",
            "thinking_env": "ZCODE_PROXY_THINKING_LEVEL",
            "request_overrides": ["zcode_thinking_level", "thinking_level", "reasoning.effort"],
            "usage_shape": "OpenAI-style prompt_tokens/completion_tokens/total_tokens plus cache_read_input_tokens when present",
        },
        "claude_code_proxy": {
            "base_url_env": "ANTHROPIC_BASE_URL",
            "model_env": "ANTHROPIC_MODEL",
            "thinking_env": "ZCODE_CLAUDE_THINKING_LEVEL",
            "request_overrides": ["zcode_thinking_level", "thinking_level"],
            "client_budget_behavior": "Claude Code thinking.budget_tokens is ignored unless ZCODE_CLAUDE_RESPECT_CLIENT_THINKING=1",
            "usage_shape": "Anthropic-style input_tokens/output_tokens mapped from ZCode usage",
        },
    }


def usage_snapshot() -> dict[str, Any] | None:
    try:
        zcode = zcode_client()
        token = zcode.load_start_plan_token()
        if not token:
            return None
        return zcode.summarize_billing_balance(zcode.fetch_billing_balance(token))
    except Exception as exc:  # noqa: BLE001 - diagnostic tool
        return {"error": str(exc)}


def backend_models_from_usage(data: dict[str, Any] | None) -> list[str]:
    models: list[str] = []
    if isinstance(data, dict):
        for bucket in data.get("balances", []) or []:
            for cap in bucket.get("capabilities", []) or []:
                if isinstance(cap, str) and cap.startswith("model:"):
                    raw = cap.split(":", 1)[1].lower()
                    if raw == "glm-5.3-flash":
                        models.append("GLM-5.3-Flash")
                    elif raw == "glm-5.3":
                        models.append("GLM-5.3")
    for known in BACKEND_MODELS:
        if known not in models:
            models.append(known)
    out: list[str] = []
    seen: set[str] = set()
    for model in models:
        key = model.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(model)
    return out


def cmd_models(args: argparse.Namespace) -> int:
    usage = usage_snapshot()
    data = {
        "backend_models": backend_models_from_usage(usage),
        "thinking_levels": THINKING,
        "usage_mapping": usage_mapping(),
        "usage_balance": usage,
    }
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    print("Available ZCode backend models:")
    for model in data["backend_models"]:
        print(f"  - {model}")
    print("\nThinking levels: " + ", ".join(THINKING))
    print("\nUsage mapping:")
    print(json.dumps(usage_mapping(), indent=2, ensure_ascii=False))
    return 0


def cmd_usage(args: argparse.Namespace) -> int:
    data = usage_snapshot()
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    return print_usage_human(data)


def print_usage_human(data: dict[str, Any] | None) -> int:
    if not data:
        print("No usage data available. Keep local ZCode credentials or set ZCODE_START_PLAN_TOKEN.")
        return 1
    if "error" in data:
        print("Unable to fetch usage: " + str(data["error"]))
        return 1
    print("Current ZCode Start Plan usage buckets:")
    for b in data.get("balances", []) or []:
        caps = ", ".join(b.get("capabilities") or [])
        print(
            f"- {b.get('show_name') or b.get('plan_id')} | {caps} | "
            f"used={b.get('used_units')} remaining={b.get('remaining_units')} "
            f"total={b.get('total_units')} ({b.get('percentage_remaining')}% remaining)"
        )
    return 0


def _format_units(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except Exception:
        return str(value or 0)


def _format_reset(value: Any) -> str:
    try:
        ts = float(value)
        dt = datetime.fromtimestamp(ts).astimezone()
        now = datetime.now().astimezone()
        if dt.date() == now.date():
            return dt.strftime("%-I:%M %p")
        return dt.strftime("%b %-d, %-I:%M %p")
    except Exception:
        try:
            # Windows' strftime does not support %-I/%-d.
            dt = datetime.fromtimestamp(float(value)).astimezone()
            now = datetime.now().astimezone()
            clock = dt.strftime("%I:%M %p").lstrip("0")
            return clock if dt.date() == now.date() else f"{dt.strftime('%b')} {dt.day}, {clock}"
        except Exception:
            return "unknown"


def zcode_usage_screen(data: dict[str, Any] | None, selected_model: str | None = None) -> str:
    """Render exact ZCode quotas without Claude's misleading weekly labels."""
    lines = [
        "\x1b[2J\x1b[H",
        "ZCode Usage",
        "===========",
        "",
        "Daily model limits",
        "",
    ]
    if not data:
        lines += ["Usage data is unavailable.", "", "R refresh   Esc return to Claude Code"]
        return "\r\n".join(lines)
    if "error" in data:
        lines += [f"Unable to fetch ZCode quota: {data['error']}", "", "R refresh   Esc return to Claude Code"]
        return "\r\n".join(lines)

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in data.get("balances", []) or []:
        if not isinstance(row, dict):
            continue
        model = str(row.get("show_name") or "Unknown model")
        groups.setdefault(model, []).append(row)

    preferred = [model for model in BACKEND_MODELS if model in groups]
    preferred += [model for model in groups if model not in preferred]
    for model in preferred:
        lines.append(model)
        rows = sorted(
            groups[model],
            key=lambda row: (int(row.get("plan_priority") or 0), int(row.get("priority") or 0)),
            reverse=True,
        )
        for row in rows:
            plan_id = str(row.get("plan_id") or "")
            plan = "Trust Build Promo" if "trust" in plan_id.lower() else "Start Plan"
            total = int(row.get("total_units") or 0)
            used = int(row.get("used_units") or 0)
            remaining = int(row.get("remaining_units") or 0)
            percent = (100.0 * used / total) if total else 0.0
            reset = _format_reset(row.get("period_end") or row.get("expires_at"))
            bar_width = 32
            filled = min(bar_width, max(0, round(bar_width * percent / 100.0)))
            bar = "#" * filled + "-" * (bar_width - filled)
            lines.append(f"  {plan} — Daily")
            lines.append(f"  [{bar}] {percent:5.1f}% used")
            lines.append(
                f"  {_format_units(used)} / {_format_units(total)} used"
                f"   ·   {_format_units(remaining)} remaining"
            )
            lines.append(f"  Resets {reset}")
            lines.append("")

    lines += [
        "These are ZCode's real model buckets; they are not Claude's 5-hour/weekly limits.",
        "",
        "R refresh   Esc return to Claude Code",
    ]
    return "\r\n".join(lines)


def http_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=2) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_health(url: str, timeout: float = 20.0) -> None:
    end = time.time() + timeout
    last = ""
    while time.time() < end:
        try:
            http_json(url)
            return
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
            time.sleep(0.25)
    raise RuntimeError(f"Proxy did not become healthy at {url}: {last}")


def _listener_pid_windows(port: int) -> int | None:
    script = (
        f"$c=Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | "
        "Select-Object -First 1; if($c){$c.OwningProcess}"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=8,
    )
    raw = result.stdout.strip()
    return int(raw) if raw.isdigit() else None


def _process_command_windows(pid: int) -> str:
    script = (
        f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' -ErrorAction SilentlyContinue; "
        "if($p){$p.CommandLine}"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=8,
    )
    return result.stdout.strip()


def clear_stale_proxy(kind: str, host: str, port: int) -> None:
    """Remove a stale launcher-owned proxy, but never kill an unrelated listener."""
    try:
        health = http_json(f"http://{host}:{port}/health")
    except Exception:
        return
    expected_name = "zcode-openai-compatible-proxy" if kind == "codex" else "zcode-claude-compatible-proxy"
    if not isinstance(health, dict) or health.get("name") != expected_name:
        raise RuntimeError(f"Port {port} is already in use by a non-ZCode service; refusing to replace it.")
    if os.name != "nt":
        raise RuntimeError(f"A stale ZCode proxy is already listening on port {port}; stop it before launching again.")
    pid = _listener_pid_windows(port)
    if not pid:
        raise RuntimeError(f"A stale ZCode proxy is listening on port {port}, but its PID could not be resolved.")
    command = _process_command_windows(pid).lower()
    expected_script = "openai_proxy.py" if kind == "codex" else "claude_proxy.py"
    if expected_script not in command or str(ROOT).lower() not in command:
        raise RuntimeError(f"Port {port} belongs to PID {pid}, but it is not this project's {expected_script}; refusing to kill it.")
    print(f"[{kind}] replacing stale launcher proxy PID {pid} on port {port}")
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, timeout=8)
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            http_json(f"http://{host}:{port}/health")
        except Exception:
            return
        time.sleep(0.2)
    raise RuntimeError(f"Stale proxy PID {pid} did not release port {port}.")


def save_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def resolve_claude_native_binary(requested: str) -> Path:
    resolved = shutil.which(requested)
    if not resolved:
        candidate = Path(requested)
        if candidate.exists():
            resolved = str(candidate.resolve())
        else:
            raise RuntimeError(f"Claude CLI not found: {requested}")
    path = Path(resolved)
    if path.suffix.lower() == ".exe":
        return path
    # npm's Windows launcher lives next to node_modules. Prefer the native Bun
    # executable so our isolated compatibility patch never touches claude.cmd.
    candidate = path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    if candidate.exists():
        return candidate
    raise RuntimeError(f"Could not locate Claude Code native executable from {path}")


def claude_oauth_bridge_base(host: str, port: int) -> str:
    if host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Claude OAuth compatibility runtime currently requires a loopback launcher host.")
    if not 1000 <= int(port) <= 9999:
        raise RuntimeError("Claude OAuth compatibility runtime currently requires a four-digit local port.")
    # The bundled allowlist string is replaced in-place, so the replacement
    # must be exactly the same byte length. Use 127.0.0.1 even when the proxy
    # was bound as localhost; both resolve to the same local listener here.
    value = f"http://127.0.0.1:{int(port)}/zcode-oauth-bridge"
    if len(value.encode("ascii")) != len(CLAUDE_OAUTH_ALLOWLIST_SOURCE):
        raise RuntimeError(f"Internal OAuth bridge URL length mismatch: {value}")
    return value


def prepare_claude_compat_runtime(args: argparse.Namespace, out_dir: Path) -> tuple[Path, str]:
    """Create/reuse a project-local Claude binary with localhost OAuth allowed.

    Claude Code intentionally restricts CLAUDE_CODE_CUSTOM_OAUTH_URL to an
    Anthropic allowlist. We patch one allowlisted URL in a COPY of the native
    executable, never the user's installed binary. This lets native /usage and
    /api/oauth/profile route to the same localhost proxy as inference.
    """
    source = resolve_claude_native_binary(args.claude_bin)
    oauth_base = claude_oauth_bridge_base(args.host, args.claude_port)
    replacement = oauth_base.encode("ascii")
    runtime_dir = ROOT / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    stat = source.stat()
    version = "unknown"
    try:
        result = subprocess.run([str(source), "--version"], capture_output=True, text=True, timeout=10)
        version = (result.stdout or result.stderr or "unknown").strip().split()[0]
    except Exception:
        pass
    safe_version = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in version)
    target = runtime_dir / f"claude-zcode-{safe_version}-p{args.claude_port}.exe"
    marker = target.with_suffix(".json")
    expected_marker = {
        "source": str(source),
        "source_size": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "oauth_base": oauth_base,
        "patch_source": CLAUDE_OAUTH_ALLOWLIST_SOURCE.decode("ascii"),
    }
    reuse = False
    if target.exists() and marker.exists():
        try:
            reuse = json.loads(marker.read_text(encoding="utf-8")) == expected_marker and target.stat().st_size == stat.st_size
        except Exception:
            reuse = False
    if not reuse:
        print(f"[claude] preparing isolated compatibility runtime for Claude Code {version}...")
        shutil.copy2(source, target)
        patched = 0
        with target.open("r+b") as handle:
            with mmap.mmap(handle.fileno(), 0) as mm:
                cursor = 0
                while True:
                    index = mm.find(CLAUDE_OAUTH_ALLOWLIST_SOURCE, cursor)
                    if index < 0:
                        break
                    mm[index : index + len(replacement)] = replacement
                    patched += 1
                    cursor = index + len(replacement)
                mm.flush()
        if patched < 1:
            target.unlink(missing_ok=True)
            raise RuntimeError("Claude Code OAuth allowlist signature was not found; the installed version may have changed.")
        save_json(marker, expected_marker | {"patched_occurrences": patched, "claude_version": version})
    save_json(out_dir / "claude-runtime.json", expected_marker | {"runtime": str(target), "claude_version": version})
    return target, oauth_base


def run_dir(label: str) -> Path:
    path = RUN_ROOT / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def start_proxy(kind: str, host: str, port: int, thinking: str, backend_model: str, out_dir: Path) -> subprocess.Popen[str]:
    clear_stale_proxy(kind, host, port)
    env = os.environ.copy()
    if kind == "codex":
        script = "openai_proxy.py"
        env["ZCODE_PROXY_THINKING_LEVEL"] = thinking
        env["ZCODE_PROXY_BACKEND_MODEL"] = backend_model
        env["ZCODE_PROXY_DUMP_DIR"] = str(out_dir / "codex-proxy-dumps")
    else:
        script = "claude_proxy.py"
        env["ZCODE_CLAUDE_THINKING_LEVEL"] = thinking
        env["ZCODE_CLAUDE_BACKEND_MODEL"] = backend_model
        env["ZCODE_CLAUDE_RESPECT_CLIENT_THINKING"] = env.get("ZCODE_CLAUDE_RESPECT_CLIENT_THINKING", "0")
        env["ZCODE_CLAUDE_PROXY_DUMP_DIR"] = str(out_dir / "claude-proxy-dumps")
        env["ZCODE_CLAUDE_PROXY_API_KEY"] = "zcode-local-oauth"
    log = (out_dir / f"{kind}-proxy.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-u", str(ROOT / script), "--host", host, "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    wait_health(f"http://{host}:{port}/health")
    return proc


def stop(proc: subprocess.Popen[str] | None) -> None:
    if not proc or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def read_prompt(args: argparse.Namespace) -> str | None:
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8")
    return args.prompt


def child_env(args: argparse.Namespace, kind: str, out_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["ZCODE_LAUNCHER_ISOLATED"] = "1"
    env["ZCODE_LAUNCHER_RUN_DIR"] = str(out_dir)
    if kind == "codex":
        env["OPENAI_BASE_URL"] = f"http://{args.host}:{args.codex_port}/v1"
        env["OPENAI_API_KEY"] = args.proxy_api_key
        env["OPENAI_MODEL"] = client_model_id(args.codex_model)
        env["ZCODE_PROXY_BACKEND_MODEL"] = args.codex_model
        env["ZCODE_PROXY_THINKING_LEVEL"] = args.codex_thinking
        env["ZCODE_PROXY_DUMP_DIR"] = str(out_dir / "codex-proxy-dumps")
    else:
        env["ANTHROPIC_BASE_URL"] = f"http://{args.host}:{args.claude_port}"
        env["CLAUDE_CODE_CUSTOM_OAUTH_URL"] = args.claude_oauth_base
        env.pop("USER_TYPE", None)
        env.pop("USE_LOCAL_OAUTH", None)
        env.pop("CLAUDE_LOCAL_OAUTH_API_BASE", None)
        env.pop("CLAUDE_LOCAL_OAUTH_APPS_BASE", None)
        env.pop("CLAUDE_LOCAL_OAUTH_CONSOLE_BASE", None)
        # Use a launcher-only isolated secure-storage directory so Claude Code
        # sees a subscription-shaped claude.ai session without touching the
        # user's real ~/.claude credentials. The contained access token is only
        # accepted by our localhost proxy and is never sent to Anthropic.
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        auth_dir = out_dir / "claude-secure-storage"
        auth_dir.mkdir(parents=True, exist_ok=True)
        local_credentials = {
            "claudeAiOauth": {
                "accessToken": "zcode-local-oauth",
                "refreshToken": "",
                "expiresAt": 4102444800000,
                "scopes": ["user:inference", "user:profile", "user:sessions:claude_code"],
                "subscriptionType": "max",
                "rateLimitTier": "default_claude_max_20x",
            }
        }
        # Production mode uses .credentials.json; local OAuth mode appends its
        # own suffix. Write both to make auth preflight/version transitions
        # deterministic without falling back to the user's real credential store.
        save_json(auth_dir / ".credentials.json", local_credentials)
        save_json(auth_dir / ".credentials-local-oauth.json", local_credentials)
        env["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = str(auth_dir)
        env["CLAUDE_CONFIG_DIR"] = str(
            prepare_claude_config_dir(out_dir, Path(args.workdir).resolve())
        )
        # The initial model is passed with Claude's --model flag. Do not also
        # pin ANTHROPIC_MODEL: Claude treats that environment variable as an
        # override and /model then cannot become the real current selection.
        env.pop("ANTHROPIC_MODEL", None)
        env["ZCODE_CLAUDE_BACKEND_MODEL"] = args.claude_model
        env["ZCODE_CLAUDE_THINKING_LEVEL"] = args.claude_thinking
        env["ZCODE_CLAUDE_RESPECT_CLIENT_THINKING"] = "1" if args.respect_client_thinking else "0"
        env["ZCODE_CLAUDE_PROXY_DUMP_DIR"] = str(out_dir / "claude-proxy-dumps")
        env["DISABLE_LOGIN_COMMAND"] = "1"
        env["DISABLE_LOGOUT_COMMAND"] = "1"
        env["IS_DEMO"] = "1"
        env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
        env["CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"] = "1"
        env["CLAUDE_CODE_ATTRIBUTION_HEADER"] = "0"
    return env


def split_extra(value: str | None) -> list[str]:
    return shlex.split(value or "", posix=False)


def _claude_project_dir_name(workdir: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", str(workdir))


def prepare_claude_config_dir(out_dir: Path, workdir: Path) -> Path:
    """Clone the useful parts of ~/.claude into a per-run writable config.

    This preserves normal settings/plugins/session context while ensuring
    commands such as /model, /effort, and /config never modify the user's
    permanent Claude Code configuration.
    """
    source = Path.home() / ".claude"
    target = out_dir / "claude-config"
    target.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        return target

    for name in (
        "settings.json",
        "keybindings.json",
        "CLAUDE.md",
        "history.jsonl",
        "stats-cache.json",
    ):
        src = source / name
        if src.is_file():
            shutil.copy2(src, target / name)

    for name in ("plugins", "commands", "agents", "skills", "sessions"):
        src = source / name
        if src.is_dir():
            shutil.copytree(src, target / name, dirs_exist_ok=True)

    project_name = _claude_project_dir_name(workdir)
    project_src = source / "projects" / project_name
    if project_src.is_dir():
        shutil.copytree(
            project_src,
            target / "projects" / project_name,
            dirs_exist_ok=True,
        )
    return target


def codex_command(args: argparse.Namespace, prompt: str | None) -> list[str]:
    if args.codex_args:
        return [args.codex_bin] + split_extra(args.codex_args)
    if args.interactive_cli:
        return [args.codex_bin]
    cmd = [args.codex_bin, "exec", "--model", client_model_id(args.codex_model)]
    if prompt:
        cmd.append(prompt)
    return cmd


def claude_command(args: argparse.Namespace) -> list[str]:
    if args.claude_args:
        return [args.claude_bin] + split_extra(args.claude_args)
    if args.interactive_cli:
        cmd = [
            str(args.claude_runtime_bin),
            "--model",
            client_model_id(args.claude_model),
            "--effort",
            args.claude_thinking,
            "--permission-mode",
            "bypassPermissions",
        ]
        return cmd
    cmd = [
        str(args.claude_runtime_bin),
        "--model",
        client_model_id(args.claude_model),
        "--effort",
        args.claude_thinking,
        "--verbose",
        "--print",
        "--output-format",
        "stream-json",
        "--permission-mode",
        "bypassPermissions",
    ]
    return cmd


def run_child(name: str, cmd: list[str], env: dict[str, str], cwd: Path, stdin_text: str | None, out_dir: Path) -> int:
    resolved = shutil.which(cmd[0])
    if not resolved:
        print(f"[{name}] CLI not found: {cmd[0]}. Pass --{name}-bin or add it to PATH.")
        return 127
    if resolved.lower().endswith((".cmd", ".bat")):
        cmd = [os.environ.get("ComSpec", "cmd.exe"), "/c", resolved] + cmd[1:]
    else:
        cmd = [resolved] + cmd[1:]
    save_json(out_dir / f"{name}.command.json", {"cmd": cmd, "cwd": str(cwd), "stdin": bool(stdin_text)})
    print(f"[{name}] cwd={cwd}")
    print(f"[{name}] command={' '.join(cmd)}")
    with (out_dir / f"{name}.log").open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE if stdin_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if stdin_text is not None and proc.stdin:
            proc.stdin.write(stdin_text)
            if not stdin_text.endswith("\n"):
                proc.stdin.write("\n")
            proc.stdin.close()
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            log.write(line)
        code = proc.wait()
    (out_dir / f"{name}-exit.txt").write_text(f"EXIT:{code}\n", encoding="utf-8")
    return int(code)


def _windows_extended_key_sequence(code: str) -> str:
    return {
        "H": "\x1b[A",  # up
        "P": "\x1b[B",  # down
        "M": "\x1b[C",  # right
        "K": "\x1b[D",  # left
        "G": "\x1b[H",  # home
        "O": "\x1b[F",  # end
        "I": "\x1b[5~",  # page up
        "Q": "\x1b[6~",  # page down
        "R": "\x1b[2~",  # insert
        "S": "\x1b[3~",  # delete
    }.get(code, "")


def run_claude_interactive_pty(cmd: list[str], env: dict[str, str], cwd: Path, out_dir: Path) -> int:
    """Relay the real Claude TUI while owning launcher-only commands.

    Claude's built-in /usage labels its quota windows as 5-hour/week. ZCode's
    entitlements are daily, so forwarding /usage would be actively misleading.
    The launcher renders live ZCode model buckets itself and also catches
    Anthropic-account/cloud-only commands that cannot work against a local
    ZCode gateway. All ordinary local Claude Code input is relayed unchanged.
    """
    import msvcrt
    from winpty import PtyProcess

    # Claude's TUI uses Unicode spinners/symbols. Python can inherit a legacy
    # Windows code page when the launcher itself is hosted inside another PTY,
    # which would otherwise crash the relay on characters such as ✳.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    size = shutil.get_terminal_size((140, 40))
    rows, columns = max(20, size.lines), max(80, size.columns)
    proc = PtyProcess.spawn(cmd, cwd=str(cwd), env=env, dimensions=(rows, columns))
    overlay = threading.Event()
    overlay_kind = ""
    finished = threading.Event()
    output_lock = threading.Lock()
    log_path = out_dir / "claude-tui.log"
    log = log_path.open("w", encoding="utf-8", errors="replace")

    def emit(value: str) -> None:
        with output_lock:
            sys.stdout.write(value)
            sys.stdout.flush()

    def reader() -> None:
        try:
            while proc.isalive():
                try:
                    value = proc.read(1024)
                except Exception:
                    break
                if not value:
                    continue
                log.write(value)
                log.flush()
                if not overlay.is_set():
                    emit(value)
        finally:
            finished.set()

    thread = threading.Thread(target=reader, name="claude-zcode-pty-reader", daemon=True)
    thread.start()

    line: list[str] = []
    line_is_simple = True

    def show_usage() -> None:
        nonlocal overlay_kind
        overlay_kind = "usage"
        overlay.set()
        screen = zcode_usage_screen(usage_snapshot(), env.get("ZCODE_CLAUDE_BACKEND_MODEL"))
        log.write("\n\n[LAUNCHER /usage OVERLAY]\n" + screen + "\n[END OVERLAY]\n")
        log.flush()
        emit(screen)

    def show_unsupported(command: str, title: str, detail: tuple[str, ...]) -> None:
        nonlocal overlay_kind
        overlay_kind = "unsupported"
        overlay.set()
        lines = [
            "\x1b[2J\x1b[H",
            "ZCodeToAPI",
            "==========",
            "",
            title,
            "",
            *detail,
            "",
            f"Command: {command}",
            "",
            "Esc return to Claude Code",
        ]
        screen = "\r\n".join(lines)
        log.write(
            f"\n\n[LAUNCHER UNSUPPORTED COMMAND {command}]\n"
            + screen
            + "\n[END OVERLAY]\n"
        )
        log.flush()
        emit(screen)

    def leave_overlay() -> None:
        nonlocal overlay_kind
        overlay.clear()
        overlay_kind = ""
        emit("\x1b[2J\x1b[H")
        try:
            proc.setwinsize(rows, columns)
        except Exception:
            pass
        # Claude redraws its React/Ink TUI on Ctrl+L. This only refreshes the
        # terminal view; it does not alter the conversation/session.
        try:
            proc.write("\x0c")
        except Exception:
            pass

    try:
        while proc.isalive() and not finished.is_set():
            if not msvcrt.kbhit():
                time.sleep(0.02)
                continue
            ch = msvcrt.getwch()

            if overlay.is_set():
                if ch in {"\x1b", "q", "Q"}:
                    leave_overlay()
                elif overlay_kind == "usage" and ch in {"r", "R"}:
                    show_usage()
                continue

            if ch in {"\x00", "\xe0"}:
                code = msvcrt.getwch()
                sequence = _windows_extended_key_sequence(code)
                if sequence:
                    proc.write(sequence)
                line_is_simple = False
                continue

            if ch in {"\r", "\n"}:
                typed = "".join(line).strip() if line_is_simple else ""
                intercept = claude_launcher_intercept(typed)
                if intercept and intercept[0] == "usage":
                    # Remove the command from Claude's composer before it can
                    # enter Claude's own misleading weekly usage screen.
                    proc.write("\x15")
                    time.sleep(0.05)
                    line.clear()
                    line_is_simple = True
                    show_usage()
                    continue
                if intercept and intercept[0] == "unsupported":
                    proc.write("\x15")
                    time.sleep(0.05)
                    line.clear()
                    line_is_simple = True
                    show_unsupported(intercept[1], intercept[2], intercept[3])
                    continue
                proc.write("\r")
                line.clear()
                line_is_simple = True
                continue

            if ch in {"\x08", "\x7f"}:
                proc.write(ch)
                if line_is_simple and line:
                    line.pop()
                continue

            if ch == "\x15":  # Ctrl+U
                proc.write(ch)
                line.clear()
                line_is_simple = True
                continue

            proc.write(ch)
            if ch == "\x03":  # Ctrl+C
                line.clear()
                line_is_simple = True
            elif ord(ch) >= 32 and line_is_simple:
                line.append(ch)
            elif ord(ch) < 32:
                line_is_simple = False
    except KeyboardInterrupt:
        try:
            proc.write("\x03")
        except Exception:
            pass
    finally:
        if overlay.is_set():
            overlay.clear()
            emit("\x1b[2J\x1b[H")
        if proc.isalive():
            try:
                proc.terminate(force=True)
            except Exception:
                pass
        thread.join(timeout=2)
        log.close()

    exit_status = getattr(proc, "exitstatus", 0)
    return int(exit_status) if isinstance(exit_status, int) else 0


def run_child_interactive(name: str, cmd: list[str], env: dict[str, str], cwd: Path, out_dir: Path) -> int:
    resolved = shutil.which(cmd[0])
    if not resolved:
        print(f"[{name}] CLI not found: {cmd[0]}. Pass --{name}-bin or add it to PATH.")
        return 127
    if resolved.lower().endswith((".cmd", ".bat")):
        cmd = [os.environ.get("ComSpec", "cmd.exe"), "/c", resolved] + cmd[1:]
    else:
        cmd = [resolved] + cmd[1:]
    save_json(out_dir / f"{name}.command.json", {"cmd": cmd, "cwd": str(cwd), "interactive": True})
    print(f"\nOpening the real {name} CLI in: {cwd}")
    if name == "claude":
        print(
            "Claude Code is using a launcher-only local proxy credential; no Anthropic login "
            "or real Anthropic API key is required."
        )
    print("Exit the CLI normally to return to this launcher.\n")
    if name == "claude" and os.name == "nt":
        code = run_claude_interactive_pty(cmd, env, cwd, out_dir)
    else:
        code = subprocess.call(cmd, cwd=str(cwd), env=env)
    (out_dir / f"{name}-exit.txt").write_text(f"EXIT:{code}\n", encoding="utf-8")
    return int(code)


def validate_claude_gateway_auth(args: argparse.Namespace, env: dict[str, str], cwd: Path, out_dir: Path) -> None:
    """Fail before opening the TUI unless Claude sees the isolated local subscription."""
    resolved = str(args.claude_runtime_bin)
    cmd = [resolved, "auth", "status"]
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    raw = (result.stdout or "").strip()
    (out_dir / "claude-auth-status.txt").write_text(raw + "\n" + (result.stderr or ""), encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"Claude local gateway auth preflight failed with exit {result.returncode}: {raw or result.stderr}")
    try:
        status = json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"Claude auth preflight returned invalid JSON: {exc}: {raw[:500]}") from exc
    if not status.get("loggedIn") or status.get("authMethod") != "claude.ai" or status.get("subscriptionType") != "max":
        raise RuntimeError(f"Claude did not accept the isolated ZCode subscription auth: {status}")
    print("[claude] local gateway auth: logged in (isolated subscription mode)")


def prepare_codex_home(args: argparse.Namespace, out_dir: Path, env: dict[str, str]) -> None:
    home = out_dir / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    model_id = client_model_id(args.codex_model)
    config = (
        f'model = "{model_id}"\n'
        'model_provider = "zcode-glm"\n\n'
        '[model_providers.zcode-glm]\n'
        'name = "ZCode GLM"\n'
        f'base_url = "http://{args.host}:{args.codex_port}/v1"\n'
        'wire_api = "responses"\n'
        'requires_openai_auth = false\n'
    )
    (home / "config.toml").write_text(config, encoding="utf-8")
    env["CODEX_HOME"] = str(home)


def run_one(kind: str, args: argparse.Namespace, out_dir: Path, prompt: str | None) -> int:
    proc: subprocess.Popen[str] | None = None
    thinking = args.codex_thinking if kind == "codex" else args.claude_thinking
    backend_model = args.codex_model if kind == "codex" else args.claude_model
    port = args.codex_port if kind == "codex" else args.claude_port
    before = usage_snapshot()
    save_json(out_dir / f"{kind}-usage-before.json", before)
    try:
        if kind == "claude":
            runtime, oauth_base = prepare_claude_compat_runtime(args, out_dir)
            args.claude_runtime_bin = runtime
            args.claude_oauth_base = oauth_base
        if not args.no_proxy:
            proc = start_proxy(kind, args.host, port, thinking, backend_model, out_dir)
        if args.proxy_only:
            print(f"[{kind}] proxy running. Ctrl+C to stop.")
            while True:
                time.sleep(3600)
        workdir = Path(args.workdir).resolve()
        env = child_env(args, kind, out_dir)
        if kind == "codex" and args.interactive_cli:
            prepare_codex_home(args, out_dir, env)
        if kind == "claude":
            validate_claude_gateway_auth(args, env, workdir, out_dir)
        if args.interactive_cli:
            cmd = codex_command(args, None) if kind == "codex" else claude_command(args)
            return run_child_interactive(kind, cmd, env, workdir, out_dir)
        if kind == "codex":
            code = run_child("codex", codex_command(args, prompt), env, workdir, None, out_dir)
        else:
            code = run_child("claude", claude_command(args), env, workdir, prompt, out_dir)
        return code
    finally:
        after = usage_snapshot()
        save_json(out_dir / f"{kind}-usage-after.json", after)
        if args.interactive_cli:
            print("\nZCode quota after session:")
            print_usage_human(after)
        if not args.keep_proxy:
            stop(proc)


def apply_shared_thinking(args: argparse.Namespace) -> argparse.Namespace:
    if args.thinking:
        args.codex_thinking = args.thinking
        args.claude_thinking = args.thinking
    args.codex_thinking = norm_thinking(args.codex_thinking)
    args.claude_thinking = norm_thinking(args.claude_thinking)
    return args


def cmd_run(args: argparse.Namespace) -> int:
    args = apply_shared_thinking(args)
    prompt = read_prompt(args)
    if not args.interactive_cli and args.target in {"codex", "both"} and not prompt and not args.codex_args and not args.proxy_only:
        print("Codex default command needs --prompt/--prompt-file, or pass --codex-args.")
        return 2
    if not args.interactive_cli and args.target in {"claude", "both"} and not prompt and not args.claude_args and not args.proxy_only:
        print("Claude default command needs --prompt/--prompt-file, or pass --claude-args.")
        return 2
    out_dir = run_dir(args.target)
    serializable_args = {k: v for k, v in vars(args).items() if k != "func"}
    save_json(out_dir / "launcher-config.json", serializable_args | {"usage_mapping": usage_mapping()})
    print(f"Run logs: {out_dir}")
    codes: list[int] = []
    if args.target in {"codex", "both"}:
        codes.append(run_one("codex", args, out_dir, prompt))
    if args.target in {"claude", "both"}:
        codes.append(run_one("claude", args, out_dir, prompt))
    return max(codes) if codes else 0


def choose(default: str, options: list[str], label: str) -> str:
    print(label)
    for i, option in enumerate(options, 1):
        suffix = " default" if option == default else ""
        print(f"  {i}. {option}{suffix}")
    raw = input(f"Select [{default}]: ").strip()
    if not raw:
        return default
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    if raw in options:
        return raw
    raise SystemExit(f"Invalid selection: {raw}")


def cmd_interactive(args: argparse.Namespace) -> int:
    print("ZCode interactive CLI launcher. Installed CLI settings are not modified.\n")
    args.target = choose("claude", ["codex", "claude"], "Open:")
    models = backend_models_from_usage(usage_snapshot())
    if args.target == "codex":
        args.codex_model = choose(args.codex_model, models, "ZCode model:")
        args.codex_thinking = choose(args.codex_thinking, THINKING, "Thinking level:")
    else:
        args.claude_model = choose(args.claude_model, models, "ZCode model:")
        args.claude_thinking = choose(args.claude_thinking, THINKING, "Thinking level:")
    args.interactive_cli = True
    print("\nCurrent ZCode quota:")
    print_usage_human(usage_snapshot())
    return cmd_run(args)


def add_run_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--prompt")
    p.add_argument("--prompt-file")
    p.add_argument("--workdir", default=str(PROJECT_ROOT))
    p.add_argument("--host", default=HOST)
    p.add_argument("--codex-port", type=int, default=CODEX_PORT)
    p.add_argument("--claude-port", type=int, default=CLAUDE_PORT)
    p.add_argument("--proxy-api-key", default="local")
    p.add_argument("--codex-model", default=BACKEND_MODELS[0], choices=BACKEND_MODELS)
    p.add_argument("--claude-model", default=BACKEND_MODELS[0], choices=BACKEND_MODELS)
    p.add_argument("--thinking", choices=THINKING, help="Set both Codex and Claude thinking levels")
    p.add_argument("--codex-thinking", default="low", choices=THINKING)
    p.add_argument("--claude-thinking", default="low", choices=THINKING)
    p.add_argument("--codex-bin", default="codex")
    p.add_argument("--claude-bin", default="claude")
    p.add_argument("--codex-args", help="Override Codex args after executable, quoted as one string")
    p.add_argument("--claude-args", help="Override Claude args after executable, quoted as one string")
    p.add_argument("--respect-client-thinking", action="store_true")
    p.add_argument("--no-proxy", action="store_true")
    p.add_argument("--proxy-only", action="store_true")
    p.add_argument("--keep-proxy", action="store_true")
    p.add_argument("--interactive-cli", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run Codex and/or Claude Code through isolated local ZCode GLM proxies.")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("models", help="Show model selector values, thinking levels, and usage mapping")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_models)
    u = sub.add_parser("usage", help="Show current ZCode usage/quota buckets")
    u.add_argument("--json", action="store_true")
    u.set_defaults(func=cmd_usage)
    r = sub.add_parser("run", help="Run codex, claude, or both")
    r.add_argument("target", choices=["codex", "claude", "both"])
    add_run_args(r)
    r.set_defaults(func=cmd_run)
    i = sub.add_parser("interactive", help="Interactive selector")
    add_run_args(i)
    i.set_defaults(func=cmd_interactive)
    return p


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    if not effective_argv:
        effective_argv = ["interactive"]
    args = build_parser().parse_args(effective_argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
