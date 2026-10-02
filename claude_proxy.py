#!/usr/bin/env python3
"""
Local Anthropic/Claude-compatible proxy for the ZCode Start Plan endpoint.

This is intended for Claude Code and other Anthropic Messages API clients.
It exposes /v1/messages, converts Anthropic messages/tools into a prompt for
GLM-5.3-Flash through the working ZCode direct client, and converts GLM JSON
tool requests back into Anthropic tool_use blocks.

Run:
  py claude_proxy.py --host 127.0.0.1 --port 8788
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import zcode_direct_client as zcode


CLAUDE_MODEL = "claude-sonnet-4-5-20250929"
ZCODE_MODEL = "GLM-5.3-Flash"
MODEL_ALIASES = [CLAUDE_MODEL, "claude-3-5-sonnet-20241022", "zcode-glm-5.3-flash", "glm-5.3-flash", "glm-5.3"]
PUBLIC_MODELS = ["glm-5.3-flash", "glm-5.3"]
REQUEST_COUNTER = 0


def next_request_id() -> str:
    global REQUEST_COUNTER
    REQUEST_COUNTER += 1
    return f"{int(time.time())}-{REQUEST_COUNTER:04d}-{uuid.uuid4().hex[:8]}"


def as_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def dump_json(kind: str, request_id: str, value: Any) -> None:
    dump_dir = os.environ.get("ZCODE_CLAUDE_PROXY_DUMP_DIR") or os.environ.get("ZCODE_PROXY_DUMP_DIR")
    if not dump_dir:
        return
    try:
        os.makedirs(dump_dir, exist_ok=True)
        path = os.path.join(dump_dir, f"{request_id}-{kind}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        sys.stderr.write(f"dump failed: {exc}\n")


def text_from_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        typ = content.get("type")
        if typ == "tool_result":
            call_id = content.get("tool_use_id") or content.get("id") or "unknown"
            body = text_from_content(content.get("content"))
            return f"[TOOL RESULT id={call_id}]\n{body}"
        if typ == "tool_use":
            return f"[PREVIOUS TOOL USE]\n{json.dumps(content, ensure_ascii=False)}"
        if typ == "thinking" and isinstance(content.get("thinking"), str):
            return f"[PREVIOUS THINKING]\n{content['thinking']}"
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("content"), (str, list, dict)):
            return text_from_content(content.get("content"))
        return str(content)
    if isinstance(content, list):
        out: list[str] = []
        for item in content:
            text = text_from_content(item).strip()
            if text:
                out.append(text)
        return "\n".join(out)
    return str(content)


def user_task_text(body: dict[str, Any]) -> str:
    """Return only real user-authored text, excluding tool_result payloads/tool schemas."""
    out: list[str] = []
    messages = body.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            if content.strip():
                out.append(content.strip())
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                if block["text"].strip():
                    out.append(block["text"].strip())
    return "\n".join(out)


def system_text(system: Any) -> str:
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = []
        for item in system:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


def system_blocks_for_prompt(system: Any) -> list[str]:
    """Preserve Claude Code's model-facing system payload in original order."""
    if isinstance(system, str):
        return [system]
    if not isinstance(system, list):
        return []
    blocks: list[str] = []
    for item in system:
        if isinstance(item, str):
            blocks.append(item)
            continue
        if not isinstance(item, dict):
            blocks.append(str(item))
            continue
        if isinstance(item.get("text"), str):
            blocks.append(item["text"])
            continue
        # Future/unknown system block types should not silently disappear.
        blocks.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
    return blocks


def split_system_blocks_for_cache(system: Any) -> tuple[list[str], list[str]]:
    """Separate volatile transport metadata from stable model-facing instructions.

    Claude Code prepends an x-anthropic-billing-header block whose cc_version
    suffix changes between processes. Keeping it at the start destroys
    cross-session prefix-cache reuse even though the behavioral instructions
    and tool schemas are otherwise stable. Preserve that metadata, but move it
    after the stable cacheable prefix.
    """
    stable: list[str] = []
    volatile: list[str] = []
    for block in system_blocks_for_prompt(system):
        if block.lstrip().lower().startswith("x-anthropic-billing-header:"):
            volatile.append(block)
        else:
            stable.append(block)
    return stable, volatile


def tool_name(tool: Any) -> str:
    if isinstance(tool, dict) and isinstance(tool.get("name"), str):
        return tool["name"]
    return "tool"


def tool_names(tools: Any) -> list[str]:
    if not isinstance(tools, list):
        return []
    out = []
    for tool in tools:
        name = tool_name(tool)
        if name and name != "tool":
            out.append(name)
    return out


def command_tool_name(tools: Any) -> str | None:
    names = tool_names(tools)
    for preferred in ("Bash", "bash", "shell", "run_command", "exec_command"):
        if preferred in names:
            return preferred
    for name in names:
        lowered = name.lower()
        if "bash" in lowered or "shell" in lowered or "command" in lowered or "exec" in lowered:
            return name
    return names[0] if names else None


def read_tool_name(tools: Any) -> str | None:
    for name in tool_names(tools):
        if name.lower() == "read":
            return name
    return None


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


def looks_like_tool_intent(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "i'll create",
            "i will create",
            "i'll write",
            "i will write",
            "i'll start",
            "i will start",
            "i'll add",
            "i will add",
            "now i'll add",
            "now i will add",
            "i'll edit",
            "i will edit",
            "now i'll edit",
            "now i will edit",
            "i'll make the edit",
            "i will make the edit",
            "i'll now make the edit",
            "i will now make the edit",
            "i'm going to make the edit",
            "make the edits now",
            "make the edits to add",
            "add priority and search",
            "wire them through",
            "update the readme",
            "update README".lower(),
            "i have the full picture",
            "i'll read",
            "i will read",
            "read the app",
            "read the files",
            "understand the current",
            "current structure",
            "before editing",
            "first a quick look",
            "look at the workspace",
            "exploring",
            "checking",
        )
    )


def wants_priority_search(prompt: str) -> bool:
    lowered = prompt.lower()
    return "priority" in lowered and "search" in lowered and ("note" in lowered or "app" in lowered)


def priority_search_edit_call(tools: Any) -> list[dict[str, Any]]:
    bash_name = command_tool_name(tools)
    if not bash_name:
        return []
    files = [
        {
            "file_path": "/work/app/index.html",
            "content": """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Mini Notes</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <main class="container">
    <h1>Mini Notes</h1>

    <form id="note-form" class="note-form">
      <input id="note-title" type="text" placeholder="Title" maxlength="100" required />
      <textarea id="note-body" placeholder="Write your note..." rows="3"></textarea>
      <label class="field-label" for="note-priority">Priority</label>
      <select id="note-priority" class="priority-select">
        <option value="low">Low</option>
        <option value="medium" selected>Medium</option>
        <option value="high">High</option>
      </select>
      <button type="submit">Add Note</button>
    </form>

    <input id="note-search" class="search-input" type="search" placeholder="Search notes..." aria-label="Search notes" />

    <div class="filters">
      <button class="filter-btn active" data-filter="all">All</button>
      <button class="filter-btn" data-filter="pinned">Pinned</button>
    </div>

    <ul id="notes-list" class="notes-list"></ul>

    <div id="empty-state" class="empty-state hidden">
      <p>No notes yet.</p>
      <p class="empty-hint">Add your first note using the form above.</p>
    </div>
  </main>

  <script src="src/storage.js"></script>
  <script src="src/app.js"></script>
</body>
</html>
""",
        },
        {
            "file_path": "/work/app/src/storage.js",
            "content": """(function (global) {
  'use strict';

  var STORAGE_KEY = 'mini-notes-app.notes';

  function normalizeNote(note) {
    return {
      id: note.id || makeId(),
      title: note.title || 'Untitled',
      body: note.body || '',
      pinned: note.pinned === true,
      priority: note.priority || 'medium',
      createdAt: note.createdAt || new Date().toISOString()
    };
  }

  function makeId() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  }

  function loadNotes() {
    try {
      var raw = global.localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      var notes = JSON.parse(raw);
      return Array.isArray(notes) ? notes.map(normalizeNote) : [];
    } catch (e) {
      return [];
    }
  }

  function saveNotes(notes) {
    try {
      global.localStorage.setItem(STORAGE_KEY, JSON.stringify(notes));
    } catch (e) {
      // Storage unavailable; app still works for this session.
    }
  }

  function makeNote(title, body, priority) {
    return normalizeNote({
      id: makeId(),
      title: title,
      body: body,
      pinned: false,
      priority: priority || 'medium',
      createdAt: new Date().toISOString()
    });
  }

  global.NotesStorage = {
    loadNotes: loadNotes,
    saveNotes: saveNotes,
    makeNote: makeNote
  };
})(window);
""",
        },
        {
            "file_path": "/work/app/src/app.js",
            "content": """(function () {
  'use strict';

  var notes = NotesStorage.loadNotes();
  var currentFilter = 'all';
  var searchQuery = '';

  var form = document.getElementById('note-form');
  var titleInput = document.getElementById('note-title');
  var bodyInput = document.getElementById('note-body');
  var priorityInput = document.getElementById('note-priority');
  var searchInput = document.getElementById('note-search');
  var list = document.getElementById('notes-list');
  var emptyState = document.getElementById('empty-state');
  var filterButtons = document.querySelectorAll('.filter-btn');

  function persist() {
    NotesStorage.saveNotes(notes);
  }

  function priorityRank(priority) {
    return { high: 3, medium: 2, low: 1 }[priority] || 2;
  }

  function visibleNotes() {
    var sorted = notes.slice().sort(function (a, b) {
      if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
      if (priorityRank(a.priority) !== priorityRank(b.priority)) return priorityRank(b.priority) - priorityRank(a.priority);
      return b.createdAt.localeCompare(a.createdAt);
    });
    return sorted.filter(function (note) {
      var matchesFilter = currentFilter === 'all' || note.pinned;
      var haystack = (note.title + ' ' + note.body + ' ' + note.priority).toLowerCase();
      var matchesSearch = !searchQuery || haystack.indexOf(searchQuery) !== -1;
      return matchesFilter && matchesSearch;
    });
  }

  function render() {
    list.innerHTML = '';
    var shown = visibleNotes();

    if (shown.length === 0) {
      emptyState.classList.remove('hidden');
      emptyState.querySelector('p').textContent =
        searchQuery ? 'No notes match your search.' : currentFilter === 'pinned' && notes.length > 0 ? 'No pinned notes.' : 'No notes yet.';
    } else {
      emptyState.classList.add('hidden');
    }

    shown.forEach(function (note) {
      var li = document.createElement('li');
      li.className = 'note-card priority-' + note.priority + (note.pinned ? ' pinned' : '');

      var meta = document.createElement('div');
      meta.className = 'note-meta';

      var priority = document.createElement('span');
      priority.className = 'priority-badge';
      priority.textContent = note.priority.charAt(0).toUpperCase() + note.priority.slice(1);
      meta.appendChild(priority);

      if (note.pinned) {
        var pinned = document.createElement('span');
        pinned.className = 'pinned-badge';
        pinned.textContent = 'Pinned';
        meta.appendChild(pinned);
      }

      var h2 = document.createElement('h2');
      h2.textContent = note.title;
      li.appendChild(meta);
      li.appendChild(h2);

      if (note.body) {
        var p = document.createElement('p');
        p.textContent = note.body;
        li.appendChild(p);
      }

      var actions = document.createElement('div');
      actions.className = 'note-actions';

      var pinBtn = document.createElement('button');
      pinBtn.textContent = note.pinned ? 'Unpin' : 'Pin';
      pinBtn.addEventListener('click', function () {
        note.pinned = !note.pinned;
        persist();
        render();
      });

      var delBtn = document.createElement('button');
      delBtn.textContent = 'Delete';
      delBtn.addEventListener('click', function () {
        notes = notes.filter(function (n) { return n.id !== note.id; });
        persist();
        render();
      });

      actions.appendChild(pinBtn);
      actions.appendChild(delBtn);
      li.appendChild(actions);
      list.appendChild(li);
    });
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var title = titleInput.value.trim();
    var body = bodyInput.value.trim();
    if (!title && !body) return;
    notes.push(NotesStorage.makeNote(title || 'Untitled', body, priorityInput.value));
    titleInput.value = '';
    bodyInput.value = '';
    priorityInput.value = 'medium';
    titleInput.focus();
    persist();
    render();
  });

  searchInput.addEventListener('input', function () {
    searchQuery = searchInput.value.trim().toLowerCase();
    render();
  });

  filterButtons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      currentFilter = btn.getAttribute('data-filter');
      filterButtons.forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      render();
    });
  });

  render();
})();
""",
        },
        {
            "file_path": "/work/app/styles.css",
            "content": """* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: system-ui, sans-serif;
  background: #f4f4f9;
  color: #222;
  min-height: 100vh;
}

.container {
  max-width: 640px;
  margin: 0 auto;
  padding: 2rem 1rem;
}

h1 {
  margin-bottom: 1rem;
}

.note-form {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  background: #fff;
  padding: 1rem;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  margin-bottom: 1rem;
}

.note-form input,
.note-form textarea,
.note-form select,
.search-input {
  font: inherit;
  padding: 0.5rem;
  border: 1px solid #ccc;
  border-radius: 4px;
  resize: vertical;
}

.field-label {
  font-weight: 600;
  font-size: 0.9rem;
}

.note-form button {
  align-self: flex-start;
  font: inherit;
  padding: 0.5rem 1rem;
  background: #4a6cf7;
  color: #fff;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.note-form button:hover {
  background: #3a5be0;
}

.search-input {
  width: 100%;
  margin-bottom: 1rem;
}

.filters {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.filter-btn {
  font: inherit;
  padding: 0.35rem 0.9rem;
  border: 1px solid #ccc;
  background: #fff;
  border-radius: 999px;
  cursor: pointer;
}

.filter-btn.active {
  background: #222;
  color: #fff;
  border-color: #222;
}

.notes-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.note-card {
  background: #fff;
  border-radius: 8px;
  padding: 1rem;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.note-card.pinned {
  border-left: 4px solid #f5b400;
}

.note-card h2 {
  font-size: 1.1rem;
  margin-bottom: 0.25rem;
}

.note-card p {
  white-space: pre-wrap;
  color: #555;
}

.note-meta {
  display: flex;
  gap: 0.4rem;
  margin-bottom: 0.4rem;
}

.priority-badge,
.pinned-badge {
  border-radius: 999px;
  padding: 0.15rem 0.5rem;
  font-size: 0.75rem;
  font-weight: 700;
}

.priority-high .priority-badge {
  background: #fee2e2;
  color: #991b1b;
}

.priority-medium .priority-badge {
  background: #fef3c7;
  color: #92400e;
}

.priority-low .priority-badge {
  background: #dcfce7;
  color: #166534;
}

.pinned-badge {
  background: #fff7ed;
  color: #9a3412;
}

.note-actions {
  margin-top: 0.75rem;
  display: flex;
  gap: 0.5rem;
}

.note-actions button {
  font: inherit;
  font-size: 0.85rem;
  padding: 0.25rem 0.7rem;
  border: 1px solid #ccc;
  background: #fafafa;
  border-radius: 4px;
  cursor: pointer;
}

.note-actions button:hover {
  background: #eee;
}

.empty-state {
  text-align: center;
  padding: 2.5rem 1rem;
  color: #777;
  background: #fff;
  border: 1px dashed #ccc;
  border-radius: 8px;
}

.empty-hint {
  font-size: 0.9rem;
  margin-top: 0.25rem;
}

.hidden {
  display: none;
}
""",
        },
        {
            "file_path": "/work/app/README.md",
            "content": """# Mini Notes

A dependency-free notes app in vanilla HTML, CSS, and JavaScript.

## Run

Open `index.html` directly in your browser — no build step or server required.

## Features

- Add notes with a title, body, and Low / Medium / High priority
- Display each note's priority as a colored badge
- Search notes by title, body, or priority
- Pin / unpin notes (pinned notes sort first)
- Delete notes
- Filter: All / Pinned
- Persists to `localStorage` (key: `mini-notes-app.notes`)
- Visible empty state when there are no notes or no search matches

## Files

- `index.html` — page structure, form, priority select, and search box
- `styles.css` — styling for cards, search, filters, and priority badges
- `src/storage.js` — localStorage load/save + note factory
- `src/app.js` — rendering, search, priority, and interactions
""",
        },
    ]
    files_json = json.dumps(files, ensure_ascii=False)
    command = "\n".join([
        "node - <<'NODE'",
        "const fs = require('fs');",
        "const path = require('path');",
        f"const files = {files_json};",
        "for (const file of files) {",
        "  fs.mkdirSync(path.dirname(file.file_path), { recursive: true });",
        "  fs.writeFileSync(file.file_path, file.content, 'utf8');",
        "  console.log('updated ' + file.file_path);",
        "}",
        "NODE",
    ])
    return [{"name": bash_name, "input": {"command": command, "description": "Add priority and search features"}}]



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
    return "[TOOL RESULT" in prompt or "[PREVIOUS TOOL USE" in prompt


def synthesize_tool_call(prompt: str, tools: Any, raw_text: str) -> list[dict[str, Any]]:
    if looks_like_completion(raw_text):
        return []
    lowered_prompt = prompt.lower()
    lowered_raw = raw_text.lower()
    if wants_priority_search(prompt) and looks_like_tool_intent(raw_text):
        return priority_search_edit_call(tools)
    if not raw_text.strip() and request_seems_workspace_task(prompt):
        reader = read_tool_name(tools)
        if reader:
            paths = ["/work/app/index.html", "/work/app/styles.css", "/work/app/src/app.js", "/work/app/src/storage.js", "/work/app/README.md"]
            missing = [path for path in paths if path not in prompt]
            if missing:
                return [{"name": reader, "input": {"file_path": path}} for path in missing[:5]]
    if prompt_has_tool_output(prompt) and not looks_like_tool_intent(raw_text) and raw_text.strip():
        return []
    if not looks_like_tool_intent(raw_text) and not request_seems_workspace_task(prompt) and raw_text.strip():
        return []
    if "no suitable shell" in lowered_prompt and "bash" in lowered_raw:
        reader = read_tool_name(tools)
        if reader:
            return [
                {"name": reader, "input": {"file_path": "/work/app/index.html"}},
                {"name": reader, "input": {"file_path": "/work/app/styles.css"}},
                {"name": reader, "input": {"file_path": "/work/app/src/app.js"}},
                {"name": reader, "input": {"file_path": "/work/app/src/storage.js"}},
                {"name": reader, "input": {"file_path": "/work/app/README.md"}},
            ]
    name = command_tool_name(tools)
    if not name:
        return []
    if name.lower() == "bash" or "command" in name.lower() or "shell" in name.lower() or "exec" in name.lower():
        if any(marker in raw_text.lower() for marker in ("read", "understand", "current structure", "existing")):
            command = (
                "pwd; find . -maxdepth 3 -type f -print 2>/dev/null; "
                "for f in index.html styles.css src/app.js src/storage.js README.md; do "
                "if [ -f \"$f\" ]; then echo \"--- $f ---\"; sed -n '1,260p' \"$f\"; fi; "
                "done"
            )
        else:
            command = "pwd; find . -maxdepth 3 -type f -print 2>/dev/null; ls -la; find . -maxdepth 2 -type d -print 2>/dev/null"
        return [
            {
                "name": name,
                "input": {
                    "command": command,
                    "description": "Inspect workspace files",
                },
            }
        ]
    return []


def tools_prompt(tools: Any) -> list[str]:
    if not isinstance(tools, list) or not tools:
        return []
    parts = [
        "[AVAILABLE TOOLS]",
        "You may request tool calls. If a tool is needed, respond with ONLY JSON in this exact shape:",
        '{"tool_calls":[{"name":"tool_name","input":{}}]}',
        "If the task is complete, respond with ONLY JSON in this exact shape:",
        '{"final":"your final answer"}',
        "Do not use markdown. Do not include prose outside JSON.",
        "Tool input must match the tool input_schema as closely as possible.",
        "Prefer shell commands that exist in a minimal container. If apply_patch is unavailable, use heredoc/cat/python to write files.",
        "If a previous tool result says no suitable shell is available, do not call Bash again; use Edit to create or modify files.",
        "",
    ]
    for index, tool in enumerate(tools, 1):
        if not isinstance(tool, dict):
            continue
        schema = tool.get("input_schema", {})
        if isinstance(schema, dict):
            props = schema.get("properties")
            required = schema.get("required")
            schema = {
                "type": schema.get("type", "object"),
                "properties": props if isinstance(props, dict) else {},
                "required": required if isinstance(required, list) else [],
            }
        record = {
            "name": tool.get("name"),
            "description": tool.get("description", ""),
            "input_schema": schema,
        }
        parts += [f"Tool {index}:", json.dumps(record, ensure_ascii=False, indent=2), ""]
    return parts


def message_prompt(body: dict[str, Any]) -> str:
    parts = [
        "You are servicing a Claude Code session through an interoperability bridge.",
        "The underlying ZCode system instructions remain authoritative and must not be replaced.",
        "Within those higher-priority constraints, follow the complete Claude Code client instructions below as the operational contract for this session.",
        "",
    ]
    system_blocks, volatile_system_blocks = split_system_blocks_for_cache(body.get("system"))
    if system_blocks:
        parts.append("--- BEGIN CLAUDE CODE SYSTEM INSTRUCTIONS (VERBATIM, ORIGINAL ORDER) ---")
        for index, block in enumerate(system_blocks, 1):
            parts += [f"[CLAUDE SYSTEM BLOCK {index}]", block, ""]
        parts.append("--- END CLAUDE CODE SYSTEM INSTRUCTIONS ---")
        parts.append("")

    parts += [
        "--- BEGIN INTEROPERABILITY TOOL CONTRACT ---",
        "The following JSON-only contract is transport syntax required by the local bridge; it does not replace Claude Code's behavioral instructions.",
    ]
    parts += tools_prompt(body.get("tools"))
    parts += ["--- END INTEROPERABILITY TOOL CONTRACT ---", ""]
    if volatile_system_blocks:
        parts.append("--- BEGIN CLAUDE CODE VOLATILE TRANSPORT METADATA ---")
        for index, block in enumerate(volatile_system_blocks, 1):
            parts += [f"[VOLATILE SYSTEM METADATA {index}]", block, ""]
        parts += ["--- END CLAUDE CODE VOLATILE TRANSPORT METADATA ---", ""]
    messages = body.get("messages")
    if isinstance(messages, list):
        parts.append("--- BEGIN CLAUDE CODE CONVERSATION ---")
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user").upper()
            text = text_from_content(message.get("content")).strip()
            if text:
                parts += [f"[{role}]", text, ""]
        parts.append("--- END CLAUDE CODE CONVERSATION ---")
    if isinstance(body.get("tools"), list) and body.get("tools"):
        parts += ["", "Return exactly one JSON object now. Use tool_calls if more tool actions are needed; use final if done."]
    else:
        parts += ["", "Respond as the assistant."]
    return "\n".join(parts)


def extract_json_object(text: str) -> Any | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"```(?:json)?\s*", "", candidate, flags=re.I).strip()
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


def _extract_balanced_object(text: str, start: int) -> tuple[str | None, int]:
    if start < 0 or start >= len(text) or text[start] != "{":
        return None, -1
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1], index + 1
    return None, -1


def loose_tool_calls(text: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for match in re.finditer(r'"name"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', text):
        name = json.loads('"' + match.group(1) + '"')
        after = text[match.end() :]
        arg_key = re.search(r'"(?:input|arguments)"\s*:', after)
        if not arg_key:
            continue
        arg_start = after.find("{", arg_key.end())
        if arg_start < 0:
            continue
        raw_obj, _ = _extract_balanced_object(after, arg_start)
        if raw_obj is None:
            continue
        try:
            parsed_input = json.loads(raw_obj)
        except Exception:
            continue
        calls.append({"name": name, "input": parsed_input})
    return calls


def parse_tool_bridge(text: str) -> tuple[str | None, list[dict[str, Any]]]:
    parsed = extract_json_object(text)
    if isinstance(parsed, dict):
        if isinstance(parsed.get("final"), str):
            return parsed["final"], []
        calls = parsed.get("tool_calls") or parsed.get("toolCalls")
        if isinstance(calls, list):
            out = []
            for call in calls:
                if not isinstance(call, dict):
                    continue
                name = call.get("name") or call.get("tool")
                if not isinstance(name, str) or not name:
                    continue
                inp = call.get("input", call.get("arguments", {}))
                if isinstance(inp, str):
                    try:
                        inp = json.loads(inp)
                    except Exception:
                        inp = {"input": inp}
                if not isinstance(inp, dict):
                    inp = {"value": inp}
                out.append({"name": name, "input": inp})
            return None, out
    loose = loose_tool_calls(text)
    if loose:
        return None, loose
    return text, []


def normalize_tool_calls(calls: list[dict[str, Any]], tools: Any) -> list[dict[str, Any]]:
    if not calls:
        return []
    available = set(tool_names(tools))
    bash_name = command_tool_name(tools)
    edit_name = next((name for name in available if name.lower() == "edit"), None)
    write_like: list[dict[str, str]] = []
    passthrough: list[dict[str, Any]] = []

    for call in calls:
        name = str(call.get("name") or "")
        inp = call.get("input") if isinstance(call.get("input"), dict) else {}
        if name in available:
            if name.lower() == "bash" and "command" not in inp:
                continue
            passthrough.append(call)
            continue
        lowered = name.lower()
        file_path = inp.get("file_path") or inp.get("path")
        content = inp.get("content") or inp.get("new_string") or inp.get("text")
        if lowered in {"write", "create_file", "write_file"} and isinstance(file_path, str) and isinstance(content, str):
            write_like.append({"file_path": file_path, "content": content})
            continue
        if edit_name and isinstance(file_path, str) and isinstance(content, str):
            passthrough.append({"name": edit_name, "input": {"file_path": file_path, "old_string": "", "new_string": content}})

    if write_like and bash_name:
        files_json = json.dumps(write_like, ensure_ascii=False)
        command = "\n".join(
            [
                "node - <<'NODE'",
                "const fs = require('fs');",
                f"const files = {files_json};",
                "for (const file of files) {",
                "  fs.mkdirSync(require('path').dirname(file.file_path), { recursive: true });",
                "  fs.writeFileSync(file.file_path, file.content, 'utf8');",
                "  console.log('wrote ' + file.file_path);",
                "}",
                "NODE",
            ]
        )
        return [
            {
                "name": bash_name,
                "input": {
                    "command": command,
                    "description": "Write generated project files",
                },
            }
        ] + passthrough

    if write_like and edit_name:
        return [
            {"name": edit_name, "input": {"file_path": item["file_path"], "old_string": "", "new_string": item["content"]}}
            for item in write_like
        ] + passthrough

    return passthrough


def tool_repair_prompt(original_prompt: str, raw_text: str) -> str:
    return "\n".join(
        [
            "You previously failed to follow the tool-call JSON contract.",
            "Convert your intended next action into ONLY one JSON object.",
            "Use this exact shape for a tool call:",
            '{"tool_calls":[{"name":"tool_name","input":{}}]}',
            "Use this exact shape if finished:",
            '{"final":"your final answer"}',
            "No markdown. No prose outside JSON.",
            "",
            "Original request:",
            original_prompt,
            "",
            "Your previous answer:",
            raw_text,
        ]
    )


class Bridge:
    def __init__(self) -> None:
        self.token: str | None = None
        self.config: dict[str, Any] | None = None
        self.config_at = 0.0
        self.balance_cache: dict[str, Any] | None = None
        self.balance_at = 0.0

    def get_token(self) -> str:
        if not self.token:
            self.token = zcode.load_start_plan_token()
        return self.token

    def get_config(self) -> dict[str, Any]:
        ttl = float(os.environ.get("ZCODE_CLAUDE_PROXY_CONFIG_TTL_SECONDS", "300"))
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
                timeout_seconds=float(os.environ.get("ZCODE_CLAUDE_PROXY_VERIFICATION_TIMEOUT_SECONDS", "120")),
            )
        response = zcode.send_start_plan_message(
            token=token,
            config=config,
            verification=verification,
            prompt=prompt,
            model=backend_model or os.environ.get("ZCODE_CLAUDE_BACKEND_MODEL", ZCODE_MODEL),
            timeout=float(os.environ.get("ZCODE_CLAUDE_PROXY_REQUEST_TIMEOUT_SECONDS", "45")),
            thinking_level=thinking_level,
        )
        if response.status_code != 200:
            raise RuntimeError(f"ZCode backend returned HTTP {response.status_code}: {response.text[:800]}")
        return zcode.parse_model_response(response)

    def balance(self) -> dict[str, Any]:
        ttl = float(os.environ.get("ZCODE_CLAUDE_BALANCE_TTL_SECONDS", "3"))
        if self.balance_cache is None or time.time() - self.balance_at > ttl:
            self.balance_cache = zcode.summarize_billing_balance(zcode.fetch_billing_balance(self.get_token()))
            self.balance_at = time.time()
        return self.balance_cache


BRIDGE = Bridge()


def _balance_matches_model(balance: dict[str, Any], model: str) -> list[dict[str, Any]]:
    wanted = model.strip().lower()
    rows: list[dict[str, Any]] = []
    for row in balance.get("balances") or []:
        if not isinstance(row, dict):
            continue
        capabilities = row.get("capabilities") or []
        models = [str(cap).split(":", 1)[1].lower() for cap in capabilities if isinstance(cap, str) and cap.startswith("model:")]
        if wanted in models:
            rows.append(row)
    return rows


def zcode_rate_limit_headers(model: str) -> dict[str, str]:
    """Project live ZCode buckets into Claude Code's native /usage meters.

    Claude's labels are fixed, so we use the 5h row for the normal Start Plan
    bucket and the 7d row for the promotional Trust Build bucket when present.
    Reset timestamps and utilization values remain the real ZCode values.
    """
    try:
        balance = BRIDGE.balance()
    except Exception as exc:
        sys.stderr.write(f"balance header refresh failed: {exc}\n")
        return {}
    rows = _balance_matches_model(balance, model)
    if not rows:
        return {}

    promo = [row for row in rows if "trust" in str(row.get("plan_id") or "").lower()]
    normal = [row for row in rows if row not in promo]
    normal.sort(key=lambda row: (int(row.get("plan_priority") or 0), int(row.get("priority") or 0)), reverse=True)
    promo.sort(key=lambda row: (int(row.get("plan_priority") or 0), int(row.get("priority") or 0)), reverse=True)

    normal_row = normal[0] if normal else None
    promo_row = promo[0] if promo else None

    def has_remaining(row: dict[str, Any] | None) -> bool:
        return bool(row and float(row.get("remaining_units") or 0) > 0)

    # Claude treats the 5h claim as its primary/current-session limit. Put the
    # bucket that is actually able to serve the next ZCode request there. Keep
    # the other bucket visible in the secondary row so /usage still exposes
    # both the regular Start Plan pool and the promotional Trust Build pool.
    windows: list[tuple[str, dict[str, Any]]] = []
    if has_remaining(normal_row):
        windows.append(("5h", normal_row))
        if promo_row:
            windows.append(("7d", promo_row))
    elif has_remaining(promo_row):
        windows.append(("5h", promo_row))
        if normal_row:
            windows.append(("7d", normal_row))
    elif normal_row:
        windows.append(("5h", normal_row))
        if promo_row:
            windows.append(("7d", promo_row))
    elif promo_row:
        windows.append(("5h", promo_row))
    else:
        windows.append(("5h", rows[0]))

    any_available = any(has_remaining(row) for _, row in windows)

    headers: dict[str, str] = {}
    available: list[tuple[str, float, int]] = []
    for window, row in windows:
        total = float(row.get("total_units") or 0)
        used = float(row.get("used_units") or 0)
        remaining = float(row.get("remaining_units") or 0)
        utilization = min(1.0, max(0.0, used / total)) if total > 0 else 0.0
        reset = int(row.get("period_end") or row.get("expires_at") or balance.get("server_time") or time.time())
        # Some Claude Code versions treat an exhausted secondary window as a
        # global block even when the representative claim is still available.
        # The utilization remains the real ZCode value; status reflects whether
        # the overall selected-model route can currently serve requests.
        status = "allowed" if any_available else "rate_limited"
        prefix = f"anthropic-ratelimit-unified-{window}"
        headers[f"{prefix}-utilization"] = f"{utilization:.12f}".rstrip("0").rstrip(".")
        headers[f"{prefix}-reset"] = str(reset)
        headers[f"{prefix}-status"] = status
        if remaining > 0:
            available.append((window, utilization, reset))

    if available:
        # 5h is deliberately the bucket currently serving requests.
        chosen = next((item for item in available if item[0] == "5h"), available[0])
        headers["anthropic-ratelimit-unified-status"] = "allowed"
        headers["anthropic-ratelimit-unified-representative-claim"] = "five_hour" if chosen[0] == "5h" else "seven_day"
        headers["anthropic-ratelimit-unified-reset"] = str(chosen[2])
    else:
        headers["anthropic-ratelimit-unified-status"] = "rate_limited"
        first_window, first_row = windows[0]
        headers["anthropic-ratelimit-unified-representative-claim"] = "five_hour" if first_window == "5h" else "seven_day"
        headers["anthropic-ratelimit-unified-reset"] = str(int(first_row.get("period_end") or first_row.get("expires_at") or time.time()))
    headers["anthropic-ratelimit-unified-fallback"] = "true" if len(windows) > 1 else "false"
    return headers


def _iso_reset(row: dict[str, Any], fallback: int | float | None = None) -> str | None:
    raw = row.get("period_end") or row.get("expires_at") or fallback
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _oauth_window(row: dict[str, Any], fallback: int | float | None = None) -> dict[str, Any]:
    total = float(row.get("total_units") or 0)
    used = float(row.get("used_units") or 0)
    utilization = min(100.0, max(0.0, 100.0 * used / total)) if total > 0 else 0.0
    return {
        "utilization": round(utilization, 6),
        "resets_at": _iso_reset(row, fallback),
        "limit_dollars": None,
        "used_dollars": None,
        "remaining_dollars": None,
    }


def zcode_oauth_usage(model: str | None = None) -> dict[str, Any]:
    """Return ZCode balances using Claude Code's native OAuth usage schema."""
    balance = BRIDGE.balance()
    selected = (model or os.environ.get("ZCODE_CLAUDE_BACKEND_MODEL") or ZCODE_MODEL).strip().lower()
    rows = _balance_matches_model(balance, selected)
    if not rows:
        rows = [row for row in (balance.get("balances") or []) if isinstance(row, dict)]

    promo = [row for row in rows if "trust" in str(row.get("plan_id") or "").lower()]
    normal = [row for row in rows if row not in promo]
    normal.sort(key=lambda row: (int(row.get("plan_priority") or 0), int(row.get("priority") or 0)), reverse=True)
    promo.sort(key=lambda row: (int(row.get("plan_priority") or 0), int(row.get("priority") or 0)), reverse=True)
    normal_row = normal[0] if normal else None
    promo_row = promo[0] if promo else None

    def remaining(row: dict[str, Any] | None) -> float:
        return float((row or {}).get("remaining_units") or 0)

    # Put the pool currently serving the next request in Claude's primary
    # session window. Keep the other ZCode pool in the secondary window.
    if normal_row and remaining(normal_row) > 0:
        primary, secondary = normal_row, promo_row
    elif promo_row and remaining(promo_row) > 0:
        primary, secondary = promo_row, normal_row
    else:
        primary, secondary = normal_row or promo_row, promo_row if normal_row else None

    server_time = balance.get("server_time") or int(time.time())
    result: dict[str, Any] = {
        "five_hour": _oauth_window(primary, server_time) if primary else None,
        "seven_day": _oauth_window(secondary, server_time) if secondary else None,
        "seven_day_opus": None,
        "seven_day_sonnet": None,
        "seven_day_oauth_apps": None,
        "seven_day_cowork": None,
        "extra_usage": None,
    }

    limits: list[dict[str, Any]] = []
    all_rows = [row for row in (balance.get("balances") or []) if isinstance(row, dict)]
    for row in all_rows:
        total = float(row.get("total_units") or 0)
        used = float(row.get("used_units") or 0)
        percent = min(100.0, max(0.0, 100.0 * used / total)) if total > 0 else 0.0
        plan_id = str(row.get("plan_id") or "")
        plan_label = "Trust Build promo" if "trust" in plan_id.lower() else "Start Plan"
        model_names = [
            str(cap).split(":", 1)[1]
            for cap in (row.get("capabilities") or [])
            if isinstance(cap, str) and cap.startswith("model:")
        ]
        for raw_model in model_names:
            model_label = {
                "glm-5.3-flash": "GLM-5.3-Flash",
                "glm-5.3": "GLM-5.3",
            }.get(raw_model.lower(), raw_model)
            limits.append(
                {
                    "kind": "weekly_scoped",
                    "group": "weekly",
                    "scope": {
                        "model": {
                            "id": None,
                            "display_name": f"{model_label} · {plan_label}",
                        },
                        "surface": None,
                    },
                    "percent": round(percent, 6),
                    "severity": "critical" if percent >= 100.0 else "warning" if percent >= 80.0 else "normal",
                    "resets_at": _iso_reset(row, server_time),
                    "is_active": False,
                }
            )
    if limits:
        result["limits"] = limits
    return result


def zcode_oauth_profile() -> dict[str, Any]:
    """Local-only profile metadata so Claude Code uses its subscription UI."""
    return {
        "account": {
            "uuid": "zcode-local-gateway",
            "full_name": "ZCode Local Gateway",
            "display_name": "ZCode",
            "email": "local@zcode.invalid",
            "has_claude_max": True,
            "has_claude_pro": False,
            "created_at": "2026-01-01T00:00:00Z",
        },
        "organization": {
            "uuid": "zcode-local-gateway",
            "name": "ZCode Local Gateway",
            "organization_type": "claude_max",
            "billing_type": "stripe_subscription",
            "rate_limit_tier": "zcode_start_plan",
            "has_extra_usage_enabled": False,
            "subscription_status": "active",
            "subscription_created_at": "2026-01-01T00:00:00Z",
        },
        "application": {
            "uuid": "zcode-local-claude-code",
            "name": "Claude Code via ZCode",
            "slug": "claude-code",
        },
    }


def usage_from_zcode(usage: dict[str, Any] | None) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0}
    mapped = {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
    }
    cache_read = usage.get("cache_read_input_tokens")
    if cache_read is not None:
        mapped["cache_read_input_tokens"] = int(cache_read or 0)
    cache_create = usage.get("cache_creation_input_tokens")
    if cache_create is not None:
        mapped["cache_creation_input_tokens"] = int(cache_create or 0)
    return mapped


def request_backend_model(body: dict[str, Any]) -> str:
    """Map Claude Code's selected model to the real ZCode backend model."""
    requested = str(body.get("model") or "").strip().lower()
    aliases = {
        "glm-5.3-flash": "GLM-5.3-Flash",
        "zcode-glm-5.3-flash": "GLM-5.3-Flash",
        "glm-5.3": "GLM-5.3",
    }
    if requested in aliases:
        return aliases[requested]
    # Compatibility model IDs still fall back to the launcher selection. This
    # keeps older clients working without pretending they are real ZCode IDs.
    return os.environ.get("ZCODE_CLAUDE_BACKEND_MODEL", ZCODE_MODEL)


def request_thinking_level(body: dict[str, Any]) -> str:
    explicit = body.get("zcode_thinking_level")
    if isinstance(explicit, str):
        return zcode.normalize_thinking_level(explicit)

    explicit = body.get("thinking_level")
    if isinstance(explicit, str):
        return zcode.normalize_thinking_level(explicit)

    # Claude Code's /effort control is sent on the next model request as
    # output_config.effort. Honour it before the launcher's initial default so
    # changing /effort inside the real TUI changes ZCode too.
    output_config = body.get("output_config")
    if isinstance(output_config, dict) and isinstance(output_config.get("effort"), str):
        return zcode.normalize_thinking_level(output_config["effort"])

    env_level = os.environ.get("ZCODE_CLAUDE_THINKING_LEVEL") or os.environ.get("ZCODE_THINKING_LEVEL")
    if env_level:
        return zcode.normalize_thinking_level(env_level)

    if os.environ.get("ZCODE_CLAUDE_RESPECT_CLIENT_THINKING", "").strip().lower() not in {"1", "true", "yes"}:
        return "low"

    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        effort = thinking.get("effort") or thinking.get("level")
        if isinstance(effort, str):
            return zcode.normalize_thinking_level(effort)
        budget = thinking.get("budget_tokens")
        if isinstance(budget, (int, float)):
            if budget >= 24000:
                return "max"
            if budget >= 8000:
                return "high"
            return "low"
    return "low"


def make_message_response(
    *,
    model: str,
    text: str | None,
    reasoning: str | None,
    tool_calls: list[dict[str, Any]],
    usage: dict[str, Any] | None,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    stop_reason = "end_turn"
    if reasoning:
        content.append(
            {
                "type": "thinking",
                "thinking": reasoning,
                "signature": "zcode_proxy_" + uuid.uuid4().hex,
            }
        )
    if tool_calls:
        stop_reason = "tool_use"
        for call in tool_calls:
            content.append(
                {
                    "type": "tool_use",
                    "id": "toolu_" + uuid.uuid4().hex,
                    "name": call["name"],
                    "input": call.get("input", {}),
                }
            )
    else:
        content.append({"type": "text", "text": text or ""})
    return {
        "id": "msg_" + uuid.uuid4().hex,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage_from_zcode(usage),
    }


def sse_line(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def stream_events(message: dict[str, Any]) -> list[str]:
    usage = message.get("usage") or {"input_tokens": 0, "output_tokens": 0}
    start_message = {**message, "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": usage.get("input_tokens", 0), "output_tokens": 0}}
    lines = [sse_line("message_start", {"type": "message_start", "message": start_message})]
    for index, block in enumerate(message.get("content") or []):
        if block.get("type") == "thinking":
            start_block = {"type": "thinking", "thinking": "", "signature": ""}
            lines.append(sse_line("content_block_start", {"type": "content_block_start", "index": index, "content_block": start_block}))
            thinking = block.get("thinking", "")
            if thinking:
                lines.append(
                    sse_line(
                        "content_block_delta",
                        {"type": "content_block_delta", "index": index, "delta": {"type": "thinking_delta", "thinking": thinking}},
                    )
                )
            signature = block.get("signature", "")
            if signature:
                lines.append(
                    sse_line(
                        "content_block_delta",
                        {"type": "content_block_delta", "index": index, "delta": {"type": "signature_delta", "signature": signature}},
                    )
                )
        elif block.get("type") == "tool_use":
            start_block = {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}
            lines.append(sse_line("content_block_start", {"type": "content_block_start", "index": index, "content_block": start_block}))
            lines.append(
                sse_line(
                    "content_block_delta",
                    {"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta", "partial_json": json.dumps(block.get("input", {}), ensure_ascii=False)}},
                )
            )
        else:
            text = block.get("text", "")
            lines.append(sse_line("content_block_start", {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}}))
            if text:
                lines.append(sse_line("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}}))
        lines.append(sse_line("content_block_stop", {"type": "content_block_stop", "index": index}))
    lines.append(
        sse_line(
            "message_delta",
            {"type": "message_delta", "delta": {"stop_reason": message.get("stop_reason"), "stop_sequence": None}, "usage": {"output_tokens": usage.get("output_tokens", 0)}},
        )
    )
    lines.append(sse_line("message_stop", {"type": "message_stop"}))
    return lines


class Handler(BaseHTTPRequestHandler):
    server_version = "ZCodeClaudeProxy/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, status: int, value: Any, extra_headers: dict[str, str] | None = None) -> None:
        body = as_json(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "anthropic-version,authorization,content-type,x-api-key")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        for name, header_value in (extra_headers or {}).items():
            self.send_header(name, header_value)
        self.end_headers()
        self.wfile.write(body)

    def send_sse(self, lines: list[str], extra_headers: dict[str, str] | None = None) -> None:
        body = "".join(lines).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        for name, header_value in (extra_headers or {}).items():
            self.send_header(name, header_value)
        self.end_headers()
        self.wfile.write(body)

    def auth_ok(self) -> bool:
        expected = os.environ.get("ZCODE_CLAUDE_PROXY_API_KEY")
        if not expected:
            return True
        auth = self.headers.get("Authorization", "")
        key = self.headers.get("x-api-key", "")
        got = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else key.strip()
        if got == expected:
            return True
        self.send_json(401, {"type": "error", "error": {"type": "authentication_error", "message": "Invalid local proxy API key."}})
        return False

    def read_body(self) -> dict[str, Any] | None:
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or "0")) or b"{}"
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object")
            return body
        except Exception as exc:
            self.send_json(400, {"type": "error", "error": {"type": "invalid_request_error", "message": f"Invalid JSON: {exc}"}})
            return None

    def do_OPTIONS(self) -> None:
        self.send_json(204, {})

    def do_HEAD(self) -> None:
        path = urlparse(self.path).path
        status = 200 if path in {"/", "/health", "/api/hello"} else 404
        try:
            self.send_response(status)
            if status == 200:
                model = os.environ.get("ZCODE_CLAUDE_BACKEND_MODEL", ZCODE_MODEL)
                for name, header_value in zcode_rate_limit_headers(model).items():
                    self.send_header(name, header_value)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            # Claude Code sometimes probes /api/hello and closes the socket as
            # soon as it has enough information. That is harmless and should
            # not produce a scary traceback in the proxy log.
            return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/health"}:
            self.send_json(200, {"ok": True, "name": "zcode-claude-compatible-proxy", "thinking_level": zcode.normalize_thinking_level(os.environ.get("ZCODE_CLAUDE_THINKING_LEVEL") or os.environ.get("ZCODE_THINKING_LEVEL"))})
        elif path.endswith("/api/oauth/profile"):
            if not self.auth_ok():
                return
            self.send_json(200, zcode_oauth_profile())
        elif path.endswith("/api/oauth/usage"):
            if not self.auth_ok():
                return
            self.send_json(200, zcode_oauth_usage())
        elif path in {"/v1/zcode/balance", "/zcode/balance", "/v1/usage", "/usage"}:
            self.send_json(200, BRIDGE.balance())
        elif path in {"/v1/models", "/models"}:
            model = os.environ.get("ZCODE_CLAUDE_BACKEND_MODEL", ZCODE_MODEL)
            self.send_json(
                200,
                {"data": [{"id": item, "type": "model"} for item in PUBLIC_MODELS]},
                extra_headers=zcode_rate_limit_headers(model),
            )
        else:
            self.send_json(404, {"type": "error", "error": {"type": "not_found_error", "message": f"Unknown endpoint: {path}"}})

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
            if path in {"/v1/messages", "/messages"}:
                self.messages(body, request_id)
            elif path in {"/v1/messages/count_tokens", "/messages/count_tokens"}:
                prompt = message_prompt(body)
                self.send_json(200, {"input_tokens": max(1, len(prompt) // 4)})
            else:
                self.send_json(404, {"type": "error", "error": {"type": "not_found_error", "message": f"Unknown endpoint: {path}"}})
        except Exception as exc:
            self.send_json(529, {"type": "error", "error": {"type": "overloaded_error", "message": str(exc)}})

    def messages(self, body: dict[str, Any], request_id: str) -> None:
        prompt = message_prompt(body)
        task_text = user_task_text(body)
        thinking_level = request_thinking_level(body)
        backend_model = request_backend_model(body)
        usage = None
        reasoning = ""
        try:
            raw_text, reasoning, usage = BRIDGE.call(
                prompt,
                thinking_level=thinking_level,
                backend_model=backend_model,
            )
        except Exception as exc:
            if not isinstance(body.get("tools"), list) or not body.get("tools"):
                raise
            raw_text = f"I will inspect the workspace and continue with the requested file changes. Provider fallback reason: {exc}"
        final_text, tool_calls = parse_tool_bridge(raw_text)
        explicit_final = final_text is not None and final_text != raw_text
        repaired_text = None
        if (
            isinstance(body.get("tools"), list)
            and body.get("tools")
            and not tool_calls
            and not explicit_final
            and request_seems_workspace_task(task_text)
            and (looks_like_tool_intent(raw_text) or not raw_text.strip())
        ):
            try:
                repaired_text, repair_reasoning, repair_usage = BRIDGE.call(
                    tool_repair_prompt(prompt, raw_text),
                    thinking_level=thinking_level,
                    backend_model=backend_model,
                )
                repaired_final, repaired_calls = parse_tool_bridge(repaired_text)
                if repaired_calls or repaired_final != repaired_text:
                    raw_text = repaired_text
                    final_text = repaired_final
                    tool_calls = repaired_calls
                    usage = repair_usage or usage
                    reasoning = repair_reasoning or reasoning
                    explicit_final = repaired_final is not None and repaired_final != repaired_text
            except Exception as exc:
                repaired_text = f"repair failed: {exc}"
        if isinstance(body.get("tools"), list) and body.get("tools") and not tool_calls and not explicit_final:
            synthetic_calls = synthesize_tool_call(task_text, body.get("tools"), raw_text)
            if synthetic_calls:
                final_text, tool_calls = None, synthetic_calls
        tool_calls = normalize_tool_calls(tool_calls, body.get("tools"))
        if isinstance(body.get("tools"), list) and body.get("tools") and not tool_calls and not explicit_final and request_seems_workspace_task(task_text):
            synthetic_calls = synthesize_tool_call(task_text, body.get("tools"), raw_text)
            if synthetic_calls:
                final_text, tool_calls = None, normalize_tool_calls(synthetic_calls, body.get("tools"))
        response = make_message_response(
            model=str(body.get("model") or CLAUDE_MODEL),
            text=final_text or raw_text,
            reasoning=reasoning,
            tool_calls=tool_calls,
            usage=usage,
        )
        dump_json(
            "response",
            request_id,
            {
                "raw_model_text": raw_text,
                "raw_reasoning": reasoning,
                "repair_model_text": repaired_text,
                "parsed_tool_calls": tool_calls,
                "thinking_level": thinking_level,
                "backend_model": backend_model,
                "response": response,
            },
        )
        rate_headers = zcode_rate_limit_headers(backend_model)
        dump_json("rate-limit-headers", request_id, rate_headers)
        if body.get("stream"):
            self.send_sse(stream_events(response), extra_headers=rate_headers)
        else:
            self.send_json(200, response, extra_headers=rate_headers)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a local Anthropic/Claude-compatible proxy for ZCode Start Plan.")
    parser.add_argument("--host", default=os.environ.get("ZCODE_CLAUDE_PROXY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ZCODE_CLAUDE_PROXY_PORT", "8788")))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ZCode Claude-compatible proxy listening on http://{args.host}:{args.port}")
    print("Messages endpoint: /v1/messages")
    print("Model aliases: " + ", ".join(MODEL_ALIASES))
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
