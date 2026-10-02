#!/usr/bin/env python3
"""
Local OpenAI-compatible proxy for the ZCode Start Plan endpoint.

The goal is not only plain chat. This proxy also implements a pragmatic
Responses API tool-call bridge so Codex-style agents can ask the model for
tool calls, execute them, send tool outputs back, and continue the loop.

Run:
  py openai_proxy.py --host 127.0.0.1 --port 8787
"""

from __future__ import annotations

import argparse
import json
import mmap
import os
import re
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import zcode_direct_client as zcode


OPENAI_MODEL = "glm-5.3-flash"
ZCODE_MODEL = "GLM-5.3-Flash"
MODEL_ALIASES = ["glm-5.3-flash", "glm-5.3", "GLM-5.3-Flash", "GLM-5.3", "zcode-glm-5.3-flash"]
PUBLIC_MODELS = ["glm-5.3-flash", "glm-5.3"]
REQUEST_COUNTER = 0


def codex_native_model_messages() -> dict[str, Any]:
    """Reuse the installed Codex version's own harness prompt metadata."""
    override = os.environ.get("ZCODE_CODEX_MODELS_CACHE")
    cache_path = Path(override) if override else Path.home() / ".codex" / "models_cache.json"
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if isinstance(payload, dict):
        for model in payload.get("models") or []:
            if not isinstance(model, dict):
                continue
            messages = model.get("model_messages")
            template = messages.get("instructions_template") if isinstance(messages, dict) else None
            if isinstance(template, str) and template.strip():
                return json.loads(json.dumps(messages))
            base = model.get("base_instructions")
            if isinstance(base, str) and base.strip():
                return {"instructions_template": base}

    # A freshly installed Codex may not have populated models_cache.json yet.
    # Fall back to the exact prompt embedded in the installed native binary,
    # without copying that prompt into this repository.
    candidates: list[Path] = []
    explicit = os.environ.get("ZCODE_CODEX_NATIVE_BIN")
    if explicit:
        candidates.append(Path(explicit))
    appdata = os.environ.get("APPDATA")
    if appdata:
        package_root = Path(appdata) / "npm" / "node_modules" / "@openai" / "codex"
        if package_root.is_dir():
            candidates.extend(package_root.rglob("codex.exe"))

    prefix = b"You are a coding agent running in the Codex CLI, a terminal-based coding assistant."
    end_marker = (
        b"If all steps are complete, ensure you call `update_plan` "
        b"to mark all steps as `completed`."
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            with candidate.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
                start = data.find(prefix)
                if start < 0:
                    continue
                marker = data.find(end_marker, start)
                if marker < 0:
                    continue
                end = marker + len(end_marker)
                while end < len(data) and data[end] in (10, 13):
                    end += 1
                template = bytes(data[start:end]).decode("utf-8")
                if len(template) >= 10_000:
                    return {"instructions_template": template}
        except Exception:
            continue

    raise RuntimeError(
        "Codex model catalog needs native Codex instructions, but neither "
        f"{cache_path} nor the installed Codex binary provided them."
    )


def codex_model_catalog() -> dict[str, Any]:
    model_messages = codex_native_model_messages()
    reasoning = [
        {"effort": "low", "description": "Faster reasoning for easier tasks."},
        {"effort": "high", "description": "Deeper reasoning for harder tasks."},
        {"effort": "xhigh", "description": "Maximum ZCode reasoning effort."},
    ]

    def item(slug: str, display_name: str, description: str, priority: int) -> dict[str, Any]:
        return {
            "slug": slug,
            "display_name": display_name,
            "description": description,
            "default_reasoning_level": "xhigh",
            "supported_reasoning_levels": reasoning,
            "shell_type": "unified_exec",
            "visibility": "list",
            "supported_in_api": True,
            "priority": priority,
            "availability_nux": None,
            "upgrade": None,
            "model_messages": model_messages,
            "support_verbosity": False,
            "default_verbosity": None,
            "apply_patch_tool_type": None,
            # Codex requires a truncation policy in model metadata. Keep this
            # comfortably above normal prompt sizes without inventing a ZCode
            # token-context-window claim.
            "truncation_policy": {"mode": "bytes", "limit": 10485760},
            "experimental_supported_tools": [],
            "include_skills_usage_instructions": False,
            "include_plugin_usage_instructions": False,
            "include_apps_usage_instructions": False,
            "supports_reasoning_summary_parameter": False,
            "default_reasoning_summary": "auto",
            "web_search_tool_type": "text",
            "supports_image_detail_original": False,
            "context_window": None,
            "auto_compact_token_limit": None,
            "effective_context_window_percent": 95,
            "input_modalities": ["text"],
            "supports_search_tool": False,
            "supports_experimental_context": False,
            "use_responses_lite": False,
            "supports_reasoning_effort_updates": False,
            "node_repl_auto_review_required": False,
            "node_repl_disabled": False,
        }

    return {
        "models": [
            item("glm-5.3-flash", "GLM-5.3-Flash", "Fast ZCode Start Plan model.", 0),
            item("glm-5.3", "GLM-5.3", "ZCode Start Plan model for deeper coding work.", 1),
        ]
    }


def next_request_id() -> str:
    global REQUEST_COUNTER
    REQUEST_COUNTER += 1
    return f"{int(time.time())}-{REQUEST_COUNTER:04d}-{uuid.uuid4().hex[:8]}"


def as_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def dump_json(kind: str, request_id: str, value: Any) -> None:
    dump_dir = os.environ.get("ZCODE_PROXY_DUMP_DIR")
    if not dump_dir:
        return
    try:
        os.makedirs(dump_dir, exist_ok=True)
        path = os.path.join(dump_dir, f"{request_id}-{kind}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
    except Exception as exc:  # best-effort diagnostics only
        sys.stderr.write(f"dump failed: {exc}\n")


def model_object(model_id: str) -> dict[str, Any]:
    return {
        "id": model_id,
        "object": "model",
        "created": int(time.time()),
        "owned_by": "zcode-start-plan",
    }


def text_from_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for item in content:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                typ = item.get("type")
                if isinstance(item.get("text"), str):
                    out.append(item["text"])
                elif typ in {"image_url", "input_image"}:
                    out.append("[image omitted]")
        return "\n".join(out)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("output_text"), str):
            return content["output_text"]
    return str(content)


def text_from_response_content(content: Any) -> str:
    if not isinstance(content, list):
        return text_from_content(content)
    out: list[str] = []
    for item in content:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            typ = item.get("type")
            if typ in {"input_text", "output_text"} and isinstance(item.get("text"), str):
                out.append(item["text"])
            elif isinstance(item.get("text"), str):
                out.append(item["text"])
            elif typ in {"image_url", "input_image"}:
                out.append("[image omitted]")
    return "\n".join(out)


def coerce_input_item(item: Any) -> dict[str, Any] | None:
    if isinstance(item, dict):
        return item
    if isinstance(item, str):
        return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": item}]}
    return None


def tool_name(tool: Any) -> str:
    if not isinstance(tool, dict):
        return "tool"
    if isinstance(tool.get("name"), str):
        return tool["name"]
    fn = tool.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("name"), str):
        return fn["name"]
    typ = tool.get("type")
    if isinstance(typ, str) and typ not in {"function", "custom"}:
        return typ
    return "tool"


def tool_description(tool: Any) -> str:
    if not isinstance(tool, dict):
        return ""
    if isinstance(tool.get("description"), str):
        return tool["description"]
    fn = tool.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("description"), str):
        return fn["description"]
    return ""


def tool_parameters(tool: Any) -> Any:
    if not isinstance(tool, dict):
        return None
    if "parameters" in tool:
        return tool.get("parameters")
    fn = tool.get("function")
    if isinstance(fn, dict):
        return fn.get("parameters")
    return None


def tool_names(tools: Any) -> list[str]:
    if not isinstance(tools, list):
        return []
    names = []
    for tool in tools:
        name = tool_name(tool)
        if name and name != "tool":
            names.append(name)
    return names


def command_tool_name(tools: Any) -> str | None:
    names = tool_names(tools)
    for preferred in ("exec_command", "shell", "bash", "Bash", "run_command"):
        if preferred in names:
            return preferred
    for name in names:
        lowered = name.lower()
        if "exec" in lowered or "command" in lowered or "bash" in lowered or "shell" in lowered:
            return name
    return names[0] if names else None


def request_seems_workspace_task(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(
        marker in lowered
        for marker in (
            "create",
            "edit",
            "modify",
            "write",
            "make",
            "file",
            "directory",
            "workspace",
            "app",
            "readme",
            "index.html",
            "styles.css",
            "src/",
            "existing",
            "inspect",
            "explore",
        )
    )


def looks_like_completion(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "created",
            "updated",
            "modified",
            "implemented",
            "done",
            "completed",
            "all files",
            "successfully",
            "the app now",
            "i added",
        )
    )


def prompt_has_tool_output(prompt: str) -> bool:
    return "[TOOL OUTPUT" in prompt or "[PREVIOUS TOOL CALL" in prompt


def synthesize_tool_call(prompt: str, tools: Any, raw_text: str) -> list[dict[str, Any]]:
    if looks_like_completion(raw_text):
        return []
    if prompt_has_tool_output(prompt) and not looks_like_tool_intent(raw_text):
        return []
    if not looks_like_tool_intent(raw_text) and not request_seems_workspace_task(prompt):
        return []
    name = command_tool_name(tools)
    if not name:
        return []
    wants_read = any(
        marker in raw_text.lower()
        for marker in ("read", "understand", "current structure", "existing")
    )
    if os.name == "nt":
        command = (
            "Get-Location; "
            "Get-ChildItem -Force; "
            "Get-ChildItem -File -Recurse -ErrorAction SilentlyContinue "
            "| Select-Object -First 120 -ExpandProperty FullName"
        )
        if wants_read:
            command += (
                "; foreach ($f in @('index.html','styles.css','src/app.js','src/storage.js','README.md')) "
                "{ if (Test-Path $f) { Write-Output ('--- ' + $f + ' ---'); "
                "Get-Content $f -TotalCount 260 } }"
            )
    elif wants_read:
        command = (
            "pwd; find . -maxdepth 3 -type f -print 2>/dev/null; "
            "for f in index.html styles.css src/app.js src/storage.js README.md; do "
            "if [ -f \"$f\" ]; then echo \"--- $f ---\"; sed -n '1,260p' \"$f\"; fi; "
            "done"
        )
    else:
        command = (
            "pwd; find . -maxdepth 3 -type f -print 2>/dev/null; "
            "ls -la; find . -maxdepth 2 -type d -print 2>/dev/null"
        )
    return [
        {
            "name": name,
            "arguments": {
                "cmd": command,
                "yield_time_ms": 10000,
                "max_output_tokens": 20000,
            },
        }
    ]


def tools_section(tools: Any) -> list[str]:
    if not isinstance(tools, list) or not tools:
        return []

    lines = [
        "[AVAILABLE TOOLS]",
        "You are driving an external coding agent. You may call tools.",
        "If a tool is needed, output ONLY one JSON object in this exact shape:",
        '{"tool_calls":[{"name":"tool_name","arguments":{}}]}',
        "If no more tools are needed, output ONLY one JSON object in this exact shape:",
        '{"final":"your final answer"}',
        "If the user explicitly asks you to use a tool, you must return tool_calls, not final.",
        "Never answer in prose when tools are available. Do not use markdown fences. Do not include text outside the JSON object.",
        "Tool arguments must match the schema. Prefer one tool call at a time.",
        "",
    ]

    for i, tool in enumerate(tools, 1):
        record = {
            "name": tool_name(tool),
            "type": tool.get("type") if isinstance(tool, dict) else None,
            "description": tool_description(tool),
            "parameters": tool_parameters(tool),
        }
        if i == 1:
            lines += [
                "Example valid call for this tool:",
                json.dumps({"tool_calls": [{"name": record["name"], "arguments": {}}]}, ensure_ascii=False),
                "",
            ]
        lines += [f"Tool {i}:", json.dumps(record, ensure_ascii=False, indent=2), ""]
    return lines



def extract_json_object(text: str) -> Any | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.I).strip()
        candidate = re.sub(r"\s*```$", "", candidate).strip()
    try:
        return json.loads(candidate)
    except Exception:
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(candidate[start : end + 1])
        except Exception:
            return None
    return None


def loose_parse_tool_calls(text: str) -> list[dict[str, Any]]:
    if "tool_calls" not in text and "toolCalls" not in text:
        return []
    calls: list[dict[str, Any]] = []
    for name_match in re.finditer(r'"name"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', text):
        raw_name = name_match.group(1)
        try:
            name = json.loads(f'"{raw_name}"')
        except Exception:
            name = raw_name
        tail = text[name_match.end() :]
        arg_match = re.search(r'"arguments"\s*:', tail)
        if not arg_match:
            continue
        arg_start = name_match.end() + arg_match.end()
        while arg_start < len(text) and text[arg_start].isspace():
            arg_start += 1
        if arg_start >= len(text):
            continue
        decoder = json.JSONDecoder()
        try:
            args, _end = decoder.raw_decode(text[arg_start:])
        except Exception:
            continue
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {"input": args}
        if not isinstance(args, dict):
            args = {"value": args}
        if isinstance(name, str) and name:
            calls.append({"name": name, "arguments": args})
    return calls


def parse_tool_bridge(raw_text: str, *, tools_were_provided: bool) -> tuple[str | None, list[dict[str, Any]]]:
    """Return (final_text, tool_calls).

    When tools are provided, we ask GLM to produce JSON. If it does not, treat
    the raw answer as final text rather than failing the proxy.
    """
    parsed = extract_json_object(raw_text)
    if not isinstance(parsed, dict):
        loose_calls = loose_parse_tool_calls(raw_text) if tools_were_provided else []
        if loose_calls:
            return None, loose_calls
        return raw_text, []

    final = parsed.get("final")
    if isinstance(final, str):
        return final, []

    answer = parsed.get("answer")
    if isinstance(answer, str):
        return answer, []

    calls = parsed.get("tool_calls") or parsed.get("toolCalls") or parsed.get("calls")
    if isinstance(calls, dict):
        calls = [calls]
    if not isinstance(calls, list):
        return raw_text, []

    out: list[dict[str, Any]] = []
    for item in calls:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("tool")
        fn = item.get("function")
        if not isinstance(name, str) and isinstance(fn, dict):
            name = fn.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        args = item.get("arguments")
        if args is None and isinstance(fn, dict):
            args = fn.get("arguments")
        if args is None:
            args = {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {"input": args}
        if not isinstance(args, dict):
            args = {"value": args}
        out.append({"name": name.strip(), "arguments": args})

    if out:
        return None, out
    return raw_text, []


def has_explicit_final(raw_text: str) -> bool:
    parsed = extract_json_object(raw_text)
    if not isinstance(parsed, dict):
        return False
    return isinstance(parsed.get("final"), str) or isinstance(parsed.get("answer"), str)


def fallback_tool_call(prompt: str, tools: Any) -> list[dict[str, Any]]:
    names = tool_names(tools)
    if not names:
        return []
    text = prompt.lower()
    preferred = next((name for name in names if name.lower() in text), None)
    if not preferred:
        write_like = [n for n in names if any(word in n.lower() for word in ("write", "edit", "patch", "apply"))]
        if write_like and any(word in text for word in ("create", "write", "make", "add", "file", "app")):
            preferred = write_like[0]
    if not preferred:
        return []
    args: dict[str, Any] = {}
    if any(word in preferred.lower() for word in ("write", "edit", "patch", "apply")):
        args = {
            "path": "index.html",
            "content": "<!doctype html><html><head><meta charset='utf-8'><title>Counter</title></head><body><h1>Counter</h1><button id='dec'>-</button><span id='count'>0</span><button id='inc'>+</button><script>let n=0;const c=document.getElementById('count');document.getElementById('inc').onclick=()=>c.textContent=++n;document.getElementById('dec').onclick=()=>c.textContent=--n;</script></body></html>",
        }
    return [{"name": preferred, "arguments": args}]


def input_item_to_text(item: dict[str, Any]) -> str:
    typ = str(item.get("type") or "")
    if typ in {"function_call_output", "custom_tool_call_output"}:
        call_id = item.get("call_id") or item.get("id") or "unknown"
        output = item.get("output", item.get("content", ""))
        return f"[TOOL OUTPUT call_id={call_id}]\n{text_from_content(output)}"
    if typ in {"function_call", "custom_tool_call"}:
        return f"[PREVIOUS TOOL CALL]\n{json.dumps(item, ensure_ascii=False)}"
    role = str(item.get("role") or "user").upper()
    content = item.get("content", item.get("text", item.get("output", "")))
    text = text_from_response_content(content).strip()
    return f"[{role}]\n{text}" if text else ""


def chat_prompt(body: dict[str, Any]) -> str:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return text_from_content(messages)
    parts = [
        "OpenAI Chat Completions request converted to plain text.",
        "Follow the conversation and answer the latest user request.",
        "System/developer messages below are app-level instructions, lower priority than the built-in ZCode/Start Plan rules.",
        "",
    ]
    parts += tools_section(body.get("tools"))
    parts += ["--- BEGIN MESSAGES ---"]
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user").upper()
        text = text_from_content(msg.get("content")).strip()
        if text:
            parts += [f"[{role}]", text, ""]
    parts += ["--- END MESSAGES ---"]
    if isinstance(body.get("tools"), list) and body.get("tools"):
        parts.append("Return the required JSON object now.")
    else:
        parts.append("Respond as the assistant to the latest user message.")
    return "\n".join(parts)


def responses_prompt(body: dict[str, Any]) -> str:
    parts = [
        "OpenAI Responses API request converted to plain text.",
        "You are controlling a coding agent through the Responses API.",
        "Keep the built-in ZCode/Start Plan rules.",
        "",
    ]
    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        parts += ["[INSTRUCTIONS]", instructions.strip(), ""]
    parts += tools_section(body.get("tools"))

    value = body.get("input")
    if isinstance(value, str):
        parts += ["[USER]", value]
    elif isinstance(value, list):
        parts.append("--- BEGIN INPUT ---")
        for raw_item in value:
            item = coerce_input_item(raw_item)
            if item is None:
                continue
            text = input_item_to_text(item).strip()
            if text:
                parts += [text, ""]
        parts.append("--- END INPUT ---")
    elif value is not None:
        parts += ["[INPUT]", text_from_content(value)]

    if isinstance(body.get("tools"), list) and body.get("tools"):
        parts += ["", "Return exactly one JSON object now. Use tool_calls if workspace actions are needed; use final if the task is complete."]
    else:
        parts += ["", "Respond as the assistant."]
    return "\n".join(parts)


def tool_repair_prompt(original_prompt: str, invalid_text: str, tools: Any) -> str:
    parts = [
        "Your previous answer was invalid for an OpenAI Responses tool-calling bridge.",
        "You answered in prose, but tools are available and the task requires workspace actions.",
        "Convert your intended next action into ONE valid JSON object only.",
        "If you intended to run a command, use the available command-execution tool.",
        "If you intended to apply a patch but no dedicated patch tool is available, use the command-execution tool with a shell command that writes or edits files.",
        "Do not include markdown. Do not include a preamble. Do not include explanation.",
        "",
        "[INVALID PREVIOUS ANSWER]",
        invalid_text.strip(),
        "",
    ]
    parts += tools_section(tools)
    parts += [
        "[ORIGINAL TASK CONTEXT]",
        original_prompt,
        "",
        "Return only JSON now.",
    ]
    return "\n".join(parts)


def final_answer_repair_prompt(original_prompt: str) -> str:
    return "\n".join(
        [
            "Finish the coding-agent turn now.",
            "Workspace tools have already run. Do not request or describe another tool call.",
            "Use the task context and tool outputs below to produce the final user-facing answer.",
            "If the user requested an exact short reply and the completed tool results support it, follow that request exactly.",
            "Do not output tool_calls. Do not output markdown fences.",
            "",
            "[TASK AND TOOL HISTORY]",
            original_prompt,
            "",
            "Write the final answer now.",
        ]
    )


def final_text_from_repair(raw_text: str) -> str:
    parsed = extract_json_object(raw_text)
    if isinstance(parsed, dict):
        for key in ("final", "answer"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return raw_text.strip()


def looks_like_tool_intent(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "i'll create",
            "i will create",
            "i'll write",
            "i will write",
            "i'll add",
            "i will add",
            "now i'll add",
            "now i will add",
            "i'll edit",
            "i will edit",
            "now i'll edit",
            "now i will edit",
            "i have the full picture",
            "using `apply_patch`",
            "using apply_patch",
            "i'll read",
            "i will read",
            "read the app",
            "read the files",
            "understand the current",
            "current structure",
            "before editing",
            "run ",
            "execute ",
            "check the current directory",
        )
    )


class Bridge:
    def __init__(self) -> None:
        self.token: str | None = None
        self.config: dict[str, Any] | None = None
        self.config_at = 0.0

    def get_token(self) -> str:
        if not self.token:
            self.token = zcode.load_start_plan_token()
        return self.token

    def get_config(self) -> dict[str, Any]:
        ttl = float(os.environ.get("ZCODE_PROXY_CONFIG_TTL_SECONDS", "300"))
        if self.config is None or time.time() - self.config_at > ttl:
            self.config = zcode.fetch_captcha_config(self.get_token())
            self.config_at = time.time()
        return self.config

    def call(
        self,
        prompt: str,
        *,
        thinking_level: str | None = None,
        backend_model: str | None = None,
    ) -> tuple[str, str, dict[str, Any] | None]:
        token = self.get_token()
        config = self.get_config()
        verification = None
        if config.get("enabled") and not config.get("skip_model_request"):
            verification = zcode.obtain_fresh_verification(
                config,
                timeout_seconds=float(os.environ.get("ZCODE_PROXY_VERIFICATION_TIMEOUT_SECONDS", "120")),
            )
        response = zcode.send_start_plan_message(
            token=token,
            config=config,
            verification=verification,
            prompt=prompt,
            model=backend_model or os.environ.get("ZCODE_PROXY_BACKEND_MODEL", ZCODE_MODEL),
            timeout=float(os.environ.get("ZCODE_PROXY_REQUEST_TIMEOUT_SECONDS", "120")),
            thinking_level=thinking_level,
        )
        if response.status_code != 200:
            raise RuntimeError(f"ZCode backend returned HTTP {response.status_code}: {response.text[:1000]}")
        return zcode.parse_model_response(response)

    def balance(self) -> dict[str, Any]:
        return zcode.summarize_billing_balance(zcode.fetch_billing_balance(self.get_token()))


BRIDGE = Bridge()


def chat_usage(usage: dict[str, Any] | None) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    data = {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp + out}
    if "cache_read_input_tokens" in usage:
        data["cache_read_input_tokens"] = int(usage.get("cache_read_input_tokens") or 0)
    return data


def resp_usage(usage: dict[str, Any] | None) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    data = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
    if "cache_read_input_tokens" in usage:
        data["cache_read_input_tokens"] = int(usage.get("cache_read_input_tokens") or 0)
    return data


def request_thinking_level(body: dict[str, Any]) -> str:
    explicit = body.get("zcode_thinking_level") or body.get("thinking_level")
    if isinstance(explicit, str):
        return zcode.normalize_thinking_level(explicit)
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        effort = reasoning.get("effort") or reasoning.get("level")
        if isinstance(effort, str):
            return zcode.normalize_thinking_level(effort)
    return zcode.normalize_thinking_level(os.environ.get("ZCODE_PROXY_THINKING_LEVEL"))


def request_backend_model(body: dict[str, Any]) -> str:
    requested = str(body.get("model") or "").strip().lower()
    aliases = {
        "glm-5.3-flash": "GLM-5.3-Flash",
        "zcode-glm-5.3-flash": "GLM-5.3-Flash",
        "glm-5.3": "GLM-5.3",
    }
    if requested in aliases:
        return aliases[requested]
    return os.environ.get("ZCODE_PROXY_BACKEND_MODEL", ZCODE_MODEL)


def make_text_response(rid: str, model: str, text: str, usage: dict[str, Any] | None) -> dict[str, Any]:
    mid = "msg_" + uuid.uuid4().hex
    return {
        "id": rid,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": [
            {
                "id": mid,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "output_text": text,
        "usage": resp_usage(usage),
    }


def make_tool_response(rid: str, model: str, calls: list[dict[str, Any]], usage: dict[str, Any] | None) -> dict[str, Any]:
    output = []
    for call in calls:
        output.append(
            {
                "id": "fc_" + uuid.uuid4().hex,
                "type": "function_call",
                "status": "completed",
                "call_id": "call_" + uuid.uuid4().hex,
                "name": call["name"],
                "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False),
            }
        )
    return {
        "id": rid,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": output,
        "output_text": "",
        "usage": resp_usage(usage),
    }


def response_stream_events(full: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    rid = full["id"]
    in_progress = {**full, "status": "in_progress", "output": [], "output_text": ""}
    events: list[tuple[str, dict[str, Any]]] = [
        ("response.created", {"type": "response.created", "response": in_progress}),
        ("response.in_progress", {"type": "response.in_progress", "response": in_progress}),
    ]
    for idx, item in enumerate(full.get("output") or []):
        if item.get("type") == "function_call":
            empty_item = {**item, "arguments": ""}
            events.append(("response.output_item.added", {"type": "response.output_item.added", "response_id": rid, "output_index": idx, "item": empty_item}))
            events.append(("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta", "response_id": rid, "item_id": item["id"], "output_index": idx, "delta": item.get("arguments", "")}))
            events.append(("response.function_call_arguments.done", {"type": "response.function_call_arguments.done", "response_id": rid, "item_id": item["id"], "output_index": idx, "arguments": item.get("arguments", "")}))
            events.append(("response.output_item.done", {"type": "response.output_item.done", "response_id": rid, "output_index": idx, "item": item}))
        elif item.get("type") == "message":
            content = (item.get("content") or [{}])[0]
            text = content.get("text", "") if isinstance(content, dict) else ""
            partial_item = {**item, "status": "in_progress", "content": []}
            events.append(("response.output_item.added", {"type": "response.output_item.added", "response_id": rid, "output_index": idx, "item": partial_item}))
            events.append(("response.content_part.added", {"type": "response.content_part.added", "response_id": rid, "item_id": item["id"], "output_index": idx, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}}))
            events.append(("response.output_text.delta", {"type": "response.output_text.delta", "response_id": rid, "item_id": item["id"], "output_index": idx, "content_index": 0, "delta": text}))
            events.append(("response.output_text.done", {"type": "response.output_text.done", "response_id": rid, "item_id": item["id"], "output_index": idx, "content_index": 0, "text": text}))
            events.append(("response.content_part.done", {"type": "response.content_part.done", "response_id": rid, "item_id": item["id"], "output_index": idx, "content_index": 0, "part": content}))
            events.append(("response.output_item.done", {"type": "response.output_item.done", "response_id": rid, "output_index": idx, "item": item}))
    events.append(("response.completed", {"type": "response.completed", "response": full}))
    return events


class Handler(BaseHTTPRequestHandler):
    server_version = "ZCodeOpenAIProxy/0.2"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, status: int, value: Any) -> None:
        body = as_json(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "authorization,content-type,x-api-key")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def send_sse(self, events: list[tuple[str, dict[str, Any]]]) -> None:
        lines: list[str] = []
        for name, payload in events:
            lines.append(f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n")
        lines.append("data: [DONE]\n\n")
        body = "".join(lines).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def auth_ok(self) -> bool:
        expected = os.environ.get("ZCODE_PROXY_API_KEY")
        if not expected:
            return True
        auth = self.headers.get("Authorization", "")
        key = self.headers.get("x-api-key", "")
        got = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else key.strip()
        if got == expected:
            return True
        self.send_json(401, {"error": {"message": "Invalid local proxy API key.", "type": "authentication_error"}})
        return False

    def read_body(self) -> dict[str, Any] | None:
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or "0")) or b"{}"
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object")
            return body
        except Exception as exc:
            self.send_json(400, {"error": {"message": f"Invalid JSON: {exc}", "type": "invalid_request_error"}})
            return None

    def do_OPTIONS(self) -> None:
        self.send_json(204, {})

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/health"}:
            self.send_json(200, {"ok": True, "name": "zcode-openai-compatible-proxy", "version": self.server_version, "thinking_level": zcode.normalize_thinking_level(os.environ.get("ZCODE_PROXY_THINKING_LEVEL"))})
        elif path in {"/v1/zcode/balance", "/zcode/balance", "/v1/usage", "/usage"}:
            self.send_json(200, BRIDGE.balance())
        elif path in {"/v1/codex/models", "/codex/models"}:
            self.send_json(200, codex_model_catalog())
        elif path in {"/v1/models", "/models"}:
            self.send_json(200, {"object": "list", "data": [model_object(mid) for mid in PUBLIC_MODELS]})
        elif path.startswith("/v1/models/") or path.startswith("/models/"):
            prefix = "/v1/models/" if path.startswith("/v1/models/") else "/models/"
            model_id = unquote(path[len(prefix) :])
            if model_id in MODEL_ALIASES:
                self.send_json(200, model_object(model_id))
            else:
                self.send_json(404, {"error": {"message": f"Unknown model: {model_id}", "type": "not_found"}})
        else:
            self.send_json(404, {"error": {"message": f"Unknown endpoint: {path}", "type": "not_found"}})

    def do_POST(self) -> None:
        if not self.auth_ok():
            return
        body = self.read_body()
        if body is None:
            return
        path = urlparse(self.path).path
        request_id = next_request_id()
        dump_json("request", request_id, {"path": path, "body": body})
        try:
            if path in {"/v1/chat/completions", "/chat/completions"}:
                self.chat(body, request_id)
            elif path in {"/v1/responses", "/responses"}:
                self.handle_responses(body, request_id)
            elif path in {"/v1/completions", "/completions"}:
                self.completions(body, request_id)
            else:
                self.send_json(404, {"error": {"message": f"Unknown endpoint: {path}", "type": "not_found"}})
        except Exception as exc:
            error = {"error": {"message": str(exc), "type": "zcode_proxy_error"}}
            dump_json("error", request_id, error)
            self.send_json(502, error)

    def chat(self, body: dict[str, Any], request_id: str) -> None:
        prompt = chat_prompt(body)
        thinking_level = request_thinking_level(body)
        backend_model = request_backend_model(body)
        raw_text, _reasoning, usage = BRIDGE.call(
            prompt,
            thinking_level=thinking_level,
            backend_model=backend_model,
        )
        final_text, tool_calls = parse_tool_bridge(raw_text, tools_were_provided=bool(body.get("tools")))
        repaired_text = None
        explicit_final = has_explicit_final(raw_text)
        if body.get("tools") and not tool_calls and not explicit_final and not looks_like_completion(raw_text) and (looks_like_tool_intent(raw_text) or not prompt_has_tool_output(prompt)):
            repaired_text, _repair_reasoning, repair_usage = BRIDGE.call(
                tool_repair_prompt(prompt, raw_text, body.get("tools")),
                thinking_level=thinking_level,
                backend_model=backend_model,
            )
            repair_final, repair_calls = parse_tool_bridge(repaired_text, tools_were_provided=True)
            if repair_calls:
                final_text, tool_calls, usage = repair_final, repair_calls, repair_usage
            elif repair_final is not None and has_explicit_final(repaired_text):
                final_text, usage = repair_final, repair_usage
                explicit_final = True
        if body.get("tools") and not tool_calls and not explicit_final:
            synthetic_calls = synthesize_tool_call(prompt, body.get("tools"), raw_text)
            if synthetic_calls:
                final_text, tool_calls = None, synthetic_calls
        model = str(body.get("model") or OPENAI_MODEL)
        created = int(time.time())
        cid = "chatcmpl-" + uuid.uuid4().hex
        message: dict[str, Any]
        finish_reason = "stop"
        if tool_calls:
            finish_reason = "tool_calls"
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_" + uuid.uuid4().hex,
                        "type": "function",
                        "function": {"name": call["name"], "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False)},
                    }
                    for call in tool_calls
                ],
            }
        else:
            message = {"role": "assistant", "content": final_text or ""}
        payload = {
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": chat_usage(usage),
        }
        dump_json(
            "response",
            request_id,
            {
                "raw_model_text": raw_text,
                "repair_model_text": repaired_text,
                "parsed_tool_calls": tool_calls,
                "thinking_level": thinking_level,
                "backend_model": backend_model,
                "response": payload,
            },
        )
        if body.get("stream"):
            delta = {"role": "assistant"}
            if tool_calls:
                delta["tool_calls"] = message["tool_calls"]
            else:
                delta["content"] = final_text or ""
            events = [
                ("message", {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}),
                ("message", {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]}),
            ]
            self.send_sse(events)
            return
        self.send_json(200, payload)

    def completions(self, body: dict[str, Any], request_id: str) -> None:
        thinking_level = request_thinking_level(body)
        backend_model = request_backend_model(body)
        raw_text, _reasoning, usage = BRIDGE.call(
            text_from_content(body.get("prompt")),
            thinking_level=thinking_level,
            backend_model=backend_model,
        )
        payload = {
            "id": "cmpl-" + uuid.uuid4().hex,
            "object": "text_completion",
            "created": int(time.time()),
            "model": str(body.get("model") or OPENAI_MODEL),
            "choices": [{"text": raw_text, "index": 0, "finish_reason": "stop"}],
            "usage": chat_usage(usage),
        }
        dump_json(
            "response",
            request_id,
            {
                "raw_model_text": raw_text,
                "thinking_level": thinking_level,
                "backend_model": backend_model,
                "response": payload,
            },
        )
        self.send_json(200, payload)

    def handle_responses(self, body: dict[str, Any], request_id: str) -> None:
        prompt = responses_prompt(body)
        thinking_level = request_thinking_level(body)
        backend_model = request_backend_model(body)
        raw_text, _reasoning, usage = BRIDGE.call(
            prompt,
            thinking_level=thinking_level,
            backend_model=backend_model,
        )
        tools_were_provided = bool(body.get("tools"))
        final_text, tool_calls = parse_tool_bridge(raw_text, tools_were_provided=tools_were_provided)
        repaired_text = None
        final_repair_text = None
        explicit_final = has_explicit_final(raw_text)
        if (
            tools_were_provided
            and prompt_has_tool_output(prompt)
            and not raw_text.strip()
        ):
            final_repair_text, _final_reasoning, final_repair_usage = BRIDGE.call(
                final_answer_repair_prompt(prompt),
                thinking_level=thinking_level,
                backend_model=backend_model,
            )
            repaired_final = final_text_from_repair(final_repair_text)
            if repaired_final:
                final_text = repaired_final
                tool_calls = []
                usage = final_repair_usage
                explicit_final = True
        if tools_were_provided and not tool_calls and not explicit_final and not looks_like_completion(raw_text) and (looks_like_tool_intent(raw_text) or not prompt_has_tool_output(prompt)):
            repaired_text, _repair_reasoning, repair_usage = BRIDGE.call(
                tool_repair_prompt(prompt, raw_text, body.get("tools")),
                thinking_level=thinking_level,
                backend_model=backend_model,
            )
            repair_final, repair_calls = parse_tool_bridge(repaired_text, tools_were_provided=True)
            if repair_calls:
                final_text, tool_calls, usage = repair_final, repair_calls, repair_usage
            elif repair_final is not None and has_explicit_final(repaired_text):
                final_text, usage = repair_final, repair_usage
                explicit_final = True
        if tools_were_provided and not tool_calls and not explicit_final:
            synthetic_calls = synthesize_tool_call(prompt, body.get("tools"), raw_text)
            if synthetic_calls:
                final_text, tool_calls = None, synthetic_calls
        rid = "resp_" + uuid.uuid4().hex
        model = str(body.get("model") or OPENAI_MODEL)
        if tool_calls:
            full = make_tool_response(rid, model, tool_calls, usage)
        else:
            full = make_text_response(rid, model, final_text or raw_text, usage)
        dump_json(
            "response",
            request_id,
            {
                "raw_model_text": raw_text,
                "repair_model_text": repaired_text,
                "final_repair_model_text": final_repair_text,
                "parsed_tool_calls": tool_calls,
                "thinking_level": thinking_level,
                "backend_model": backend_model,
                "response": full,
            },
        )
        if body.get("stream"):
            self.send_sse(response_stream_events(full))
            return
        self.send_json(200, full)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a local OpenAI-compatible proxy for ZCode Start Plan.")
    parser.add_argument("--host", default=os.environ.get("ZCODE_PROXY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ZCODE_PROXY_PORT", "8787")))
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ZCode OpenAI-compatible proxy listening on http://{args.host}:{args.port}/v1")
    print("Model ids: " + ", ".join(MODEL_ALIASES))
    print("Set ZCODE_PROXY_DUMP_DIR to save request/response JSON.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping proxy.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
