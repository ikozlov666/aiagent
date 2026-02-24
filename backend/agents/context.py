"""
Smart context compression — reduces token usage while preserving
the information the model needs to make correct next decisions.

Two main features:
  1. compress_tool_result()  — shrink individual tool results by type
  2. build_context_summary() — structured summary of old messages

Usage:
    from agents.context import compress_tool_result, build_context_summary
"""

import json
from typing import Optional


# ─── Per-tool result size limits (chars) ──────────────────────────
# Each tool type has a maximum result length.  Beyond it, content is
# intelligently truncated (not just sliced) with a "(truncated)" note.

TOOL_RESULT_LIMITS = {
    "execute_command":              2000,
    "write_file":                   200,
    "write_files":                  300,
    "read_file":                    3000,
    "list_files":                   1500,
    "browser_navigate":             300,
    "browser_screenshot":           2000,
    "browser_get_content":          3000,
    "browser_get_page_structure":   2000,
    "browser_click":                200,
    "browser_type":                 200,
    "browser_select":               200,
    "browser_fill_form":            300,
    "browser_wait":                 200,
    "browser_get_console_logs":     1500,
    "browser_get_network_failures": 1000,
}


# ─── Public API ───────────────────────────────────────────────────

def compress_tool_result(tool_name: str, result_json: str) -> str:
    """
    Compress a tool result *before* it is added to the message history.

    Call this right when building the role="tool" message, so every
    subsequent LLM call benefits from the smaller context.

    Args:
        tool_name:   Name of the tool that produced the result.
        result_json: JSON-serialized result dict.

    Returns:
        Compressed JSON string.
    """
    limit = TOOL_RESULT_LIMITS.get(tool_name, 2000)

    if len(result_json) <= limit:
        return result_json

    try:
        data = json.loads(result_json)
    except (json.JSONDecodeError, TypeError):
        return result_json[:limit] + '... (truncated)'

    # ── Specialised compressors ───────────────────────────────────
    if tool_name == "execute_command":
        return _compress_command(data, limit)

    if tool_name == "read_file":
        return _compress_read_file(data, limit)

    if tool_name in ("browser_screenshot", "browser_get_content",
                      "browser_get_page_structure"):
        return _compress_browser(data, limit)

    if tool_name in ("write_file", "write_files"):
        return _compress_write(data, limit)

    # Default: serialize → slice
    out = json.dumps(data, ensure_ascii=False)
    if len(out) > limit:
        out = out[:limit] + '... (truncated)'
    return out


def build_context_summary(messages: list[dict]) -> str:
    """
    Build a structured summary of old (evicted) messages.

    Preserves key facts the model needs to continue work:
      - original user goal
      - files created / modified
      - commands executed
      - browser URLs visited
      - recent errors
      - last assistant text

    Args:
        messages: The OLD part of the conversation that will be replaced
                  by this summary.

    Returns:
        Multi-line summary string.
    """
    goal = ""
    files_written: list[str] = []
    commands_run: list[str] = []
    browser_urls: list[str] = []
    errors: list[str] = []
    last_assistant = ""

    for m in messages:
        role = m.get("role")

        if role == "user":
            text = (m.get("content") or "").strip()
            if text and not goal:
                goal = text[:300]

        elif role == "assistant":
            for tc in (m.get("tool_calls") or []):
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                name = fn.get("name", "")
                args_str = fn.get("arguments", "")
                _extract_tool_facts(
                    name, args_str,
                    files_written, commands_run, browser_urls,
                )
            text = (m.get("content") or "").strip()
            if text:
                last_assistant = text[:200]

        elif role == "tool":
            _extract_tool_errors(m, errors)

    # ── Assemble ──────────────────────────────────────────────────
    parts: list[str] = []

    if goal:
        parts.append(f"ЗАДАЧА: {goal}")

    if files_written:
        recent = files_written[-12:]
        extra = f" (ещё {len(files_written) - 12})" if len(files_written) > 12 else ""
        parts.append(f"СОЗДАННЫЕ ФАЙЛЫ: {', '.join(recent)}{extra}")

    if commands_run:
        recent = commands_run[-6:]
        parts.append(f"ВЫПОЛНЕННЫЕ КОМАНДЫ: {'; '.join(recent)}")

    if browser_urls:
        parts.append(f"ОТКРЫТЫЕ URL: {', '.join(browser_urls[-3:])}")

    if errors:
        parts.append(f"ПОСЛЕДНИЕ ОШИБКИ: {' | '.join(errors[-3:])}")

    if last_assistant:
        parts.append(f"ПОСЛЕДНИЙ ОТВЕТ МОДЕЛИ: {last_assistant}")

    return "\n".join(parts) if parts else "Предыдущий диалог (сжато)."


def estimate_tokens(messages: list[dict]) -> int:
    """Token count estimate that handles multilingual text.

    English averages ~4 chars/token, but Cyrillic/CJK are closer to ~2 chars/token
    due to multi-byte encoding. We use a conservative ~2.5 chars/token for mixed text
    to avoid underestimating and hitting the model's context limit.
    """
    total_chars = 0
    for m in messages:
        total_chars += len(m.get("content") or "")
        for tc in (m.get("tool_calls") or []):
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            total_chars += len(fn.get("arguments", ""))
    # ~2.5 chars per token (conservative for mixed Cyrillic + English + code)
    return int(total_chars / 2.5)


def compress_recent_messages(messages: list[dict], per_msg_limit: int = 1500) -> list[dict]:
    """
    Additionally compress tool-result messages in the 'recent' slice
    when it still exceeds the token budget.
    """
    result = []
    for m in messages:
        if m.get("role") == "tool":
            content = m.get("content", "")
            if len(content) > per_msg_limit:
                content = content[:per_msg_limit] + "... (truncated in context)"
            result.append({**m, "content": content})
        else:
            result.append(m)
    return result


# ─── Private helpers ──────────────────────────────────────────────

def _compress_command(data: dict, limit: int) -> str:
    """execute_command: keep exit_code, tail of stdout, full stderr."""
    r = data.get("result", data)
    if not isinstance(r, dict):
        r = {}
    exit_code = r.get("exit_code", -1)
    stdout = r.get("stdout", "")
    stderr = r.get("stderr", "")

    # stderr is usually more important (error info)
    half = limit // 2
    if stderr and len(stderr) > half:
        stderr = "..." + stderr[-half:]

    # stdout: head + tail
    if stdout and len(stdout) > half:
        lines = stdout.split("\n")
        if len(lines) > 10:
            head = "\n".join(lines[:3])
            tail = "\n".join(lines[-7:])
            stdout = f"{head}\n... ({len(lines) - 10} lines omitted) ...\n{tail}"
        else:
            stdout = stdout[:half] + "... (truncated)"

    out = {"success": data.get("success"), "result": {
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
    }}
    return json.dumps(out, ensure_ascii=False)


def _compress_read_file(data: dict, limit: int) -> str:
    """read_file: keep filepath, trim file content."""
    r = data.get("result", data)
    if not isinstance(r, dict):
        r = {}
    content = r.get("content", "")
    filepath = r.get("filepath", "")

    body_limit = limit - 200  # room for JSON wrapper
    if len(content) > body_limit:
        lines = content.split("\n")
        if len(lines) > 30:
            head = "\n".join(lines[:15])
            tail = "\n".join(lines[-10:])
            content = f"{head}\n\n... ({len(lines) - 25} lines omitted) ...\n\n{tail}"
        else:
            content = content[:body_limit] + "\n... (truncated)"

    out = {"success": True, "result": {"filepath": filepath, "content": content}}
    return json.dumps(out, ensure_ascii=False)


def _compress_browser(data: dict, limit: int) -> str:
    """browser_screenshot / get_content / get_page_structure: trim page_text/elements."""
    r = data.get("result", data)
    if not isinstance(r, dict):
        return json.dumps(data, ensure_ascii=False)[:limit] + "... (truncated)"

    body_limit = limit - 200
    for key in ("page_text", "text", "content"):
        if key in r and isinstance(r[key], str) and len(r[key]) > body_limit:
            r[key] = r[key][:body_limit] + "... (truncated)"

    # elements list (page_structure): keep first 30
    if "elements" in r and isinstance(r["elements"], list) and len(r["elements"]) > 30:
        total = len(r["elements"])
        r["elements"] = r["elements"][:30]
        r["_note"] = f"Showing 30 of {total} elements"

    out = json.dumps(data, ensure_ascii=False)
    if len(out) > limit:
        out = out[:limit] + "... (truncated)"
    return out


def _compress_write(data: dict, limit: int) -> str:
    """write_file / write_files: minimal — just filepath and success."""
    r = data.get("result", data)
    if not isinstance(r, dict):
        r = {}

    # write_file
    if "filepath" in r:
        out = {"success": data.get("success", True), "result": {"filepath": r["filepath"]}}
        return json.dumps(out, ensure_ascii=False)

    # write_files
    if "written" in r:
        out = {
            "success": data.get("success", True),
            "result": {
                "written": r.get("written", []),
                "errors": r.get("errors"),
            },
        }
        s = json.dumps(out, ensure_ascii=False)
        if len(s) > limit:
            s = s[:limit] + "... (truncated)"
        return s

    return json.dumps(data, ensure_ascii=False)[:limit]


def _extract_tool_facts(
    tool_name: str,
    args_str: str,
    files_written: list[str],
    commands_run: list[str],
    browser_urls: list[str],
) -> None:
    """Extract key facts from tool-call arguments for the summary."""
    try:
        args = json.loads(args_str) if args_str else {}
    except (json.JSONDecodeError, TypeError):
        return

    if tool_name == "write_file":
        fp = args.get("filepath", "")
        if fp and fp not in files_written:
            files_written.append(fp)

    elif tool_name == "write_files":
        for f in (args.get("files") or []):
            fp = f.get("filepath", "") if isinstance(f, dict) else ""
            if fp and fp not in files_written:
                files_written.append(fp)

    elif tool_name == "execute_command":
        cmd = args.get("command", "")[:80]
        if cmd:
            commands_run.append(cmd)

    elif tool_name == "browser_navigate":
        url = (args.get("url") or "")[:100]
        if url:
            browser_urls.append(url)


def _extract_tool_errors(msg: dict, errors: list[str]) -> None:
    """Extract error info from a tool-result message."""
    content = msg.get("content", "")
    try:
        data = json.loads(content) if isinstance(content, str) else content
    except (json.JSONDecodeError, TypeError):
        return

    if not isinstance(data, dict):
        return

    if data.get("success", True):
        return  # no error

    err = data.get("error") or ""
    r = data.get("result", {})
    stderr = r.get("stderr", "") if isinstance(r, dict) else ""
    snippet = (err or stderr)[:120]
    if snippet:
        errors.append(snippet)
