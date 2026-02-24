"""
Agent Engine — the core agent loop.
Receives a user task, calls LLM with tools, executes tool calls,
and streams progress back via a callback.

v2: Integrated dynamic prompts, smart context, task classifier, adaptive escalation.
"""
import json
import time
import asyncio
import traceback
from typing import Callable, Awaitable, Optional, List
from dataclasses import dataclass, field

from config import settings
from llm.router import llm_router
from agents.tools.definitions import TOOLS
from agents.tools.executor import ToolExecutor
from agents.planner_parallel import plan_task, topological_waves, merge_results
from docker_manager.manager import docker_manager

# ── New modules ───────────────────────────────────────────────────
from agents.prompts import get_prompt, PromptConfig
from agents.classifier import classify_task, should_use_parallel_plan
from agents.context import (
    compress_tool_result,
    build_context_summary,
    estimate_tokens,
    compress_recent_messages,
)
from agents.escalation import EscalationState, make_args_hash


@dataclass
class AgentStep:
    """A single step in the agent's execution."""
    step_number: int
    type: str  # "thinking", "tool_call", "tool_result", "response"
    content: str
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    tool_result: Optional[dict] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "step_number": self.step_number,
            "type": self.type,
            "content": self.content,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "tool_result": self.tool_result,
            "timestamp": self.timestamp,
        }


# Plan file in the project — the model creates it and follows it
AGENT_PLAN_FILE = ".agent_plan.md"

# Context compression settings
LAST_N_MESSAGES_FULL = 14       # reduced from 26
CONTEXT_SUMMARY_THRESHOLD = 20  # reduced from 30
MAX_CONTEXT_TOKENS = 12_000     # token budget for recent messages
MAX_TOTAL_CONTEXT_TOKENS = 48_000  # hard cap before sending to LLM (most models support 64-128k)


class AgentEngine:
    """
    Core agent loop: receives task → classifies → selects prompt → executes with tools → returns result.
    """

    MAX_ITERATIONS = settings.AGENT_MAX_ITERATIONS  # Safety limit (configurable)

    # Class-level regex patterns cache (lazy initialization)
    _filepath_regex = None
    _content_regex = None

    def __init__(self, project_id: str, use_multi_agent: bool = False):
        self.project_id = project_id
        self.tool_executor = ToolExecutor(project_id)
        self.steps: list[AgentStep] = []
        # System message will be set dynamically based on task class
        self.messages: list[dict] = [
            {"role": "system", "content": ""}
        ]
        self.use_multi_agent = use_multi_agent
        self._planner = None
        self._coder = None
        self._reviewer = None
        self._stop_requested = False

        # ── New: task classification & escalation state ───────────
        self._task_class: str = "coding"
        self._prompt_cfg: PromptConfig = get_prompt("coding")
        self.escalation = EscalationState()

    def stop(self):
        """Request agent to stop execution."""
        self._stop_requested = True

    def _get_filtered_tools(self) -> list[dict]:
        """Get tools filtered by current prompt config."""
        if self._prompt_cfg.tool_filter is None:
            return TOOLS
        return [
            t for t in TOOLS
            if t["function"]["name"] in self._prompt_cfg.tool_filter
        ]

    @staticmethod
    def _find_safe_split(rest: list[dict], target_n: int) -> int:
        """
        Find the index in `rest` where it's safe to split into [old, recent].

        The split must NOT land inside an assistant(tool_calls) → tool → tool chain.
        Safe boundaries: 'user' message, or 'assistant' WITHOUT tool_calls.
        We start at the desired position and walk BACKWARD to find the nearest
        safe boundary (a 'user' message).  This guarantees recent never starts
        with orphaned 'tool' messages.
        """
        split = max(0, len(rest) - target_n)

        # Walk backward to find a safe start (a 'user' message)
        while split > 0:
            role = rest[split].get("role")
            if role == "user":
                break
            split -= 1

        # If we hit the start without finding a user message, walk forward instead
        if split == 0 and rest[0].get("role") != "user":
            split = 0
            while split < len(rest):
                if rest[split].get("role") == "user":
                    break
                split += 1

        return split

    @staticmethod
    def _validate_messages(messages: list[dict]) -> list[dict]:
        """
        Ensure the chat history is API-valid:
        - No orphan 'tool' messages
        - No orphan 'assistant' messages with tool_calls lacking matching tool results

        Some OpenAI-compatible APIs require that every assistant message containing
        'tool_calls' is immediately followed by tool messages for each tool_call_id.
        This function enforces that invariant (or safely removes the invalid parts)
        to prevent 400 invalid_request_error responses.
        """
        result: list[dict] = []
        i = 0

        while i < len(messages):
            m = messages[i]
            role = m.get("role")

            # Drop orphan tool messages (they must follow assistant.tool_calls)
            if role == "tool":
                tc_id = m.get("tool_call_id")
                print(f"⚠️ [Agent] Dropping orphaned tool message (tool_call_id={tc_id})")
                i += 1
                continue

            # Handle assistant tool_calls chains
            if role == "assistant" and m.get("tool_calls"):
                tc_ids: list[str] = []
                for tc in (m.get("tool_calls") or []):
                    if isinstance(tc, dict) and tc.get("id"):
                        tc_ids.append(tc["id"])

                # Look ahead: collect consecutive tool messages after this assistant
                seen: set[str] = set()
                j = i + 1
                while j < len(messages) and messages[j].get("role") == "tool":
                    tid = messages[j].get("tool_call_id")
                    if tid in tc_ids:
                        seen.add(tid)
                    j += 1

                # If chain is complete → keep assistant + its tool messages
                if tc_ids and seen == set(tc_ids):
                    result.append(m)
                    result.extend(messages[i + 1 : j])
                    i = j
                    continue

                # Chain is incomplete → make message API-safe
                print(f"⚠️ [Agent] Dropping incomplete tool_calls chain: expected={tc_ids}, seen={sorted(seen)}")
                safe = dict(m)
                safe.pop("tool_calls", None)

                # If assistant had no actual text, drop it completely
                if not (safe.get("content") or "").strip():
                    i += 1
                    continue

                result.append(safe)
                i += 1
                continue

            # Normal message
            result.append(m)
            i += 1

        return result

    def _get_messages_for_llm(self) -> list[dict]:
        """
        Messages for LLM: with long history — compressed summary of old messages
        + last N recent messages (preserving full tool_call_id chains).
        """
        messages = self.messages
        if not messages:
            return messages
        system_msg = messages[0] if messages[0].get("role") == "system" else None
        rest = messages[1:] if system_msg else messages[:]

        if len(rest) <= CONTEXT_SUMMARY_THRESHOLD:
            return self._validate_messages(messages)

        # Find safe split point that doesn't break tool_call chains
        split = self._find_safe_split(rest, LAST_N_MESSAGES_FULL)
        old_part = rest[:split]
        recent = rest[split:]

        # If split couldn't find a good boundary, send everything
        if not recent:
            return self._validate_messages(messages)

        # Build structured summary of old messages
        summary_text = build_context_summary(old_part)

        # Find current goal in recent messages
        goal_prefix = ""
        if getattr(settings, "AGENT_CURRENT_GOAL_IN_CONTEXT", True):
            for m in recent:
                if m.get("role") == "user":
                    goal = (m.get("content") or "").strip()[:300]
                    if goal:
                        goal_prefix = f"Текущая цель (ответь на это): {goal}\n\n"
                    break

        summary_content = f"{goal_prefix}[Контекст (сжато)]:\n{summary_text}"

        # If recent part is still too large, compress tool results in it
        if estimate_tokens(recent) > MAX_CONTEXT_TOKENS:
            recent = compress_recent_messages(recent)

        out = []
        if system_msg:
            out.append(system_msg)
        out.append({"role": "user", "content": summary_content})
        out.extend(recent)
        out = self._validate_messages(out)

        # Hard cap: if total context still exceeds the limit, drop oldest
        # recent messages until we fit (keeping system + summary + last few)
        total_tokens = estimate_tokens(out)
        if total_tokens > MAX_TOTAL_CONTEXT_TOKENS:
            print(f"⚠️ [Agent] Context too large ({total_tokens} tokens > {MAX_TOTAL_CONTEXT_TOKENS}), trimming...")
            # Keep system (idx 0) + summary (idx 1) + trim from idx 2
            header = out[:2]
            tail = out[2:]
            while tail and estimate_tokens(header + tail) > MAX_TOTAL_CONTEXT_TOKENS:
                dropped = tail.pop(0)
                # If we dropped an assistant with tool_calls, also drop following tool msgs
                if dropped.get("role") == "assistant" and dropped.get("tool_calls"):
                    while tail and tail[0].get("role") == "tool":
                        tail.pop(0)
            out = header + tail

        return out

    async def _inject_plan_into_system(self) -> None:
        """If .agent_plan.md exists in the project, append it to system message."""
        if not self.messages or self.messages[0].get("role") != "system":
            return
        try:
            content = await docker_manager.read_file(
                self.project_id,
                f"/workspace/{AGENT_PLAN_FILE}",
            )
        except Exception:
            return
        content = (content or "").strip()
        if not content:
            return
        plan_block = f"\n\nТекущий план (файл {AGENT_PLAN_FILE}):\n---\n{content[:4000]}\n---"
        self.messages[0] = {
            "role": "system",
            "content": (self.messages[0].get("content") or "") + plan_block,
        }

    @classmethod
    def _get_regex_patterns(cls):
        """Get or compile regex patterns for JSON repair (lazy initialization)."""
        if cls._filepath_regex is None:
            import re
            cls._filepath_regex = re.compile(r'"filepath"\s*:\s*"([^"]+)"')
            cls._content_regex = re.compile(r'"content"\s*:\s*"(.*)', re.DOTALL)
        return cls._filepath_regex, cls._content_regex

    async def run(
        self,
        user_message: str,
        on_step: Optional[Callable[[AgentStep], Awaitable[None]]] = None,
        on_stream_chunk: Optional[Callable[[str], Awaitable[None]]] = None,
        task_type: str = "coding",
        use_multi_agent: Optional[bool] = None,
        images: Optional[list[dict]] = None,
    ) -> str:
        """
        Run the agent loop for a user task.

        Args:
            user_message: The user's request
            on_step: Async callback for each step (for real-time streaming)
            on_stream_chunk: Async callback for streaming text chunks
            task_type: Type of task for LLM routing (may be overridden by classifier)
            use_multi_agent: Override instance setting for multi-agent mode
            images: Optional list of image dicts with 'base64' or 'url' and 'mime_type'

        Returns:
            The agent's final text response
        """
        use_multi = use_multi_agent if use_multi_agent is not None else self.use_multi_agent

        # Use multi-agent workflow if enabled
        if use_multi:
            return await self._run_multi_agent(user_message, on_step)

        # ── 1. Classify task ──────────────────────────────────────
        self._task_class = classify_task(
            user_message,
            images=images,
            history_len=len(self.messages),
        )
        self._prompt_cfg = get_prompt(self._task_class)
        print(f"🏷️ [Agent] Task classified as: {self._task_class}")

        # ── 2. Set up system prompt ───────────────────────────────
        self.messages[0] = {"role": "system", "content": self._prompt_cfg.system}

        # Reset state
        self._stop_requested = False
        self.escalation = EscalationState(
            current_provider=llm_router.get_provider(task_type).name,
        )

        self.messages.append({"role": "user", "content": user_message})

        # ── 3. Trim history (keep tool_call chains intact) ────────
        MAX_MESSAGES = 50
        if len(self.messages) > MAX_MESSAGES:
            system_msg = self.messages[0] if self.messages[0].get("role") == "system" else None
            rest = self.messages[1:] if system_msg else self.messages[:]
            max_rest = MAX_MESSAGES - (1 if system_msg else 0)
            while len(rest) > max_rest:
                i = 0
                while i < len(rest) and rest[i].get("role") != "user":
                    i += 1
                if i >= len(rest):
                    break
                j = i + 1
                while j < len(rest) and rest[j].get("role") != "user":
                    j += 1
                rest = rest[:i] + rest[j:]
            self.messages = ([system_msg] + rest) if system_msg else rest

        # ── 4. Inject plan file into system message ───────────────
        await self._inject_plan_into_system()

        step_num = 0

        # ── 5. Fast path: simple_chat (no tools) ─────────────────
        if self._task_class == "simple_chat":
            try:
                return await self._run_simple_chat(
                    user_message, on_step, on_stream_chunk, images,
                )
            except Exception as e:
                print(f"⚠️ [Agent] Fast path failed, falling back to full loop:\n{traceback.format_exc()}")
                # Fall through to full loop
                self._task_class = "coding"
                self._prompt_cfg = get_prompt("coding")
                self.messages[0] = {"role": "system", "content": self._prompt_cfg.system}

        # ── 6. Parallel path: split into subtasks ─────────────────
        if should_use_parallel_plan(user_message, images):
            try:
                subtasks = await plan_task(user_message)
                waves = topological_waves(subtasks)
                if waves and len(waves[0]) >= 2:
                    if on_step:
                        await on_step(AgentStep(
                            step_num + 1, "thinking",
                            "📋 Разбиваю на подзадачи, выполняю параллельно...",
                        ))
                    result = await self._run_parallel_subtasks(
                        user_message, subtasks, on_step, task_type, images,
                    )
                    if on_step:
                        await on_step(AgentStep(step_num + 2, "response", result))
                    self.messages.append({"role": "assistant", "content": result})
                    return result
            except Exception as e:
                print(f"⚠️ [Agent] Parallel plan failed, falling back to single agent:\n{traceback.format_exc()}")

        # ── 7. Main agent loop ────────────────────────────────────
        return await self._run_loop(0, on_step, task_type, images, self.MAX_ITERATIONS, None)

    async def _run_simple_chat(
        self,
        user_message: str,
        on_step: Optional[Callable[[AgentStep], Awaitable[None]]],
        on_stream_chunk: Optional[Callable[[str], Awaitable[None]]],
        images: Optional[list[dict]],
    ) -> str:
        """Fast path: one LLM call without tools for simple chat messages."""
        # Build chat-only message history (no tool_calls) for context
        def to_chat_msg(m):
            r = m.get("role")
            if r == "user":
                return {"role": "user", "content": (m.get("content") or "").strip()}
            if r == "assistant" and "tool_calls" not in m:
                c = (m.get("content") or "").strip()
                if c:
                    return {"role": "assistant", "content": c}
            return None

        chat_only = [to_chat_msg(m) for m in self.messages]
        chat_only = [m for m in chat_only if m is not None]
        recent = chat_only[-12:] if len(chat_only) > 12 else chat_only

        fast_messages = [
            {"role": "system", "content": self._prompt_cfg.system},
        ] + recent

        cfg = self._prompt_cfg
        use_streaming = (
            getattr(settings, "AGENT_USE_STREAMING", True)
            and on_stream_chunk is not None
        )

        if use_streaming:
            final_text = ""
            async for chunk in llm_router.chat_stream(
                messages=fast_messages,
                task_type="coding",
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                images=images,
            ):
                if chunk:
                    final_text += chunk
                    await on_stream_chunk(chunk)
            final_text = final_text.strip() or "Привет!"
        else:
            response = await llm_router.chat(
                messages=fast_messages,
                task_type="coding",
                tools=None,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                images=images,
            )
            choice = response.choices[0]
            final_text = (choice.message.content or "").strip() or "Привет!"

        self.messages.append({"role": "assistant", "content": final_text})
        if on_step:
            await on_step(AgentStep(1, "response", final_text))
        print(f"⚡ [Agent] Fast reply ({self._task_class}): {final_text[:50]}...")
        return final_text

    async def _run_parallel_subtasks(
        self,
        user_message: str,
        subtasks: List[dict],
        on_step: Optional[Callable[[AgentStep], Awaitable[None]]],
        task_type: str,
        images: Optional[list] = None,
    ) -> str:
        """Execute subtasks in topological waves; within a wave — in parallel."""
        waves = topological_waves(subtasks)
        all_results = []
        result_by_id = {}

        # Get the default system prompt for subtask engines
        coding_prompt = get_prompt("coding")

        for wave_idx, wave in enumerate(waves):
            if self._stop_requested:
                return "Выполнение остановлено пользователем"

            async def run_one(subtask: dict):
                engine = AgentEngine(self.project_id)
                engine.messages = [
                    {"role": "system", "content": coding_prompt.system},
                    {"role": "user", "content": subtask["description"]},
                ]
                step_prefix = f"[{subtask['id']}] "

                async def wrapped_on_step(s: AgentStep):
                    if on_step:
                        await on_step(AgentStep(
                            s.step_number, s.type, step_prefix + s.content,
                            s.tool_name, s.tool_args, s.tool_result, s.timestamp,
                        ))

                return await engine._run_loop(
                    0, wrapped_on_step, task_type, images,
                    max_iterations=settings.AGENT_SUBTASK_MAX_ITERATIONS,
                    stop_ref=lambda: self._stop_requested,
                )

            if len(wave) == 1:
                res = await run_one(wave[0])
                result_by_id[wave[0]["id"]] = res
                all_results.append(res)
            else:
                print(f"⚡ [Agent] Параллельное выполнение {len(wave)} подзадач (волна {wave_idx + 1})")
                results = await asyncio.gather(*[run_one(st) for st in wave], return_exceptions=True)
                for st, res in zip(wave, results):
                    if isinstance(res, Exception):
                        err = f"Подзадача '{st.get('id', '?')}' завершилась с ошибкой: {res}"
                        print(f"❌ [Agent] Parallel subtask failed: {err}")
                        result_by_id[st["id"]] = err
                        all_results.append(err)
                    else:
                        result_by_id[st["id"]] = res
                        all_results.append(res)

        ordered = [result_by_id.get(s["id"], "") for s in subtasks]
        return await merge_results(user_message, ordered)

    async def _run_loop(
        self,
        step_num_start: int,
        on_step: Optional[Callable[[AgentStep], Awaitable[None]]],
        task_type: str,
        images: Optional[list],
        max_iterations: int,
        stop_ref: Optional[Callable[[], bool]],
        allow_auto_extend: bool = True,
    ) -> str:
        """Core agent loop: LLM + tools. Uses self.messages and self.tool_executor."""
        step_num = step_num_start
        stop_check = stop_ref if stop_ref is not None else (lambda: self._stop_requested)
        cfg = self._prompt_cfg
        filtered_tools = self._get_filtered_tools()

        for iteration in range(max_iterations):
            if stop_check():
                stop_step = AgentStep(
                    step_number=step_num + 1,
                    type="response",
                    content="⏹️ Выполнение остановлено пользователем",
                )
                if on_step:
                    await on_step(stop_step)
                return "Выполнение остановлено пользователем"

            step_num += 1

            # Early exit: agent is stuck (all escalations exhausted, still failing)
            if self.escalation.is_stuck:
                stuck_msg = (
                    "Агент зациклился после всех попыток эскалации. "
                    "Попробуйте переформулировать задачу или разбить её на подзадачи."
                )
                print(f"🔒 [Agent] Stuck detected at iteration {iteration + 1}")
                stuck_step = AgentStep(step_number=step_num, type="response", content=stuck_msg)
                if on_step:
                    await on_step(stuck_step)
                self.messages.append({"role": "assistant", "content": stuck_msg})
                return stuck_msg

            # Warn when approaching limit
            remaining = max_iterations - iteration
            if remaining <= 5 and remaining > 1:
                warning_step = AgentStep(
                    step_number=step_num,
                    type="thinking",
                    content=f"⚠️ Осталось {remaining} итераций. Завершаю задачу...",
                )
                if on_step:
                    await on_step(warning_step)

            # ── Escalation check ──────────────────────────────────
            if self.escalation.should_escalate():
                target = self.escalation.get_escalation_target()
                hint = self.escalation.get_escalation_hint()
                self.escalation.mark_escalated(target)

                if on_step:
                    await on_step(AgentStep(
                        step_num, "thinking",
                        f"🔄 Переключаюсь на модель '{target}' для лучшего результата...",
                    ))

                # Inject hint so the new model re-analyzes the problem
                self.messages.append({"role": "user", "content": hint})
                print(f"🔄 [Agent] Escalated: {self.escalation.current_provider} → {target}")

            # Thinking step for UI
            last_user_content = ""
            for m in reversed(self.messages):
                if m.get("role") == "user" and m.get("content"):
                    raw = (m.get("content") or "").strip()
                    last_user_content = raw[:80] + ("…" if len(raw) > 80 else "")
                    break
            n_msgs = len(self.messages)
            thinking_content = f"Думаю... (итерация {iteration + 1}/{max_iterations})"
            if last_user_content or n_msgs > 2:
                thinking_content += f"\nВ запросе: {n_msgs} сообщ. Последнее от вас: «{last_user_content or '—'}»"
            thinking_step = AgentStep(
                step_number=step_num,
                type="thinking",
                content=thinking_content,
            )
            if on_step:
                await on_step(thinking_step)

            print(f"🧠 [Agent] Итерация {iteration + 1}/{max_iterations}: Думаю над задачей...")

            # ── LLM call (with per-request timeout and retry) ─────
            messages_to_send = self._get_messages_for_llm()

            effective_task_type = task_type
            if self.escalation.escalated:
                effective_task_type = self.escalation.current_provider

            max_tokens_loop = cfg.max_tokens
            if (not self.escalation.escalated
                    and getattr(settings, "AGENT_REDUCED_MAX_TOKENS", True)
                    and max_tokens_loop > 2048):
                max_tokens_loop = 2048

            llm_timeout = getattr(settings, "LLM_REQUEST_TIMEOUT_SECONDS", 120)
            max_retries = 2
            response = None

            for attempt in range(1, max_retries + 1):
                try:
                    print(f"📡 [Agent] LLM запрос (attempt {attempt}, task: {effective_task_type}, "
                          f"class: {self._task_class}, msgs: {len(messages_to_send)})...")

                    response = await asyncio.wait_for(
                        llm_router.chat(
                            messages=messages_to_send,
                            task_type=effective_task_type,
                            tools=filtered_tools,
                            temperature=cfg.temperature,
                            max_tokens=max_tokens_loop,
                            images=images,
                        ),
                        timeout=llm_timeout,
                    )
                    print(f"✅ [Agent] Получен ответ от LLM")
                    break
                except asyncio.TimeoutError:
                    print(f"⏱️ [Agent] LLM timeout ({llm_timeout}s), attempt {attempt}/{max_retries}")
                    if attempt < max_retries:
                        await asyncio.sleep(2 * attempt)
                        continue
                    error_step = AgentStep(
                        step_number=step_num, type="error",
                        content=f"LLM не ответил за {llm_timeout}с после {max_retries} попыток",
                    )
                    if on_step:
                        await on_step(error_step)
                    return f"LLM не ответил за {llm_timeout}с. Попробуйте повторить запрос."
                except Exception as e:
                    print(f"❌ [Agent] LLM error (attempt {attempt}):\n{traceback.format_exc()}")
                    if attempt < max_retries:
                        await asyncio.sleep(2 * attempt)
                        continue
                    error_step = AgentStep(
                        step_number=step_num, type="error",
                        content=f"Ошибка LLM: {str(e)}",
                    )
                    if on_step:
                        await on_step(error_step)
                    return f"Произошла ошибка при обращении к AI: {str(e)}"

            if not response or not response.choices:
                print(f"⚠️ [Agent] LLM returned empty response (no choices)")
                error_step = AgentStep(
                    step_number=step_num, type="error",
                    content="LLM вернул пустой ответ. Повторяю...",
                )
                if on_step:
                    await on_step(error_step)
                continue

            choice = response.choices[0]
            message = choice.message
            if not message:
                print(f"⚠️ [Agent] LLM choice has no message, skipping iteration")
                continue

            # Send LLM text to the activity panel
            if message.content and message.content.strip():
                llm_step = AgentStep(
                    step_number=step_num,
                    type="llm_text",
                    content=message.content.strip(),
                )
                self.steps.append(llm_step)
                if on_step:
                    await on_step(llm_step)

            # If no tool calls — agent is done
            if not message.tool_calls:
                final_text = message.content or "Готово!"
                self.messages.append({"role": "assistant", "content": final_text})

                final_step = AgentStep(
                    step_number=step_num,
                    type="response",
                    content=final_text,
                )
                self.steps.append(final_step)
                if on_step:
                    await on_step(final_step)

                return final_text

            # ── Process tool calls ────────────────────────────────
            self.messages.append({
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                    }
                    for tc in message.tool_calls
                ]
            })

            # Execute tool calls
            async def execute_single_tool(tool_call, step_offset):
                """Execute a single tool call and return result with metadata."""
                tool_name = tool_call.function.name

                # Parse arguments
                _json_was_truncated = False
                try:
                    if tool_call.function.arguments:
                        args_str = tool_call.function.arguments
                        try:
                            tool_args = json.loads(args_str)
                        except json.JSONDecodeError:
                            _json_was_truncated = True
                            print(f"⚠️ [Agent] JSON parse error for {tool_name}, attempting fix...")
                            tool_args = self._repair_json_args(tool_name, args_str)
                    else:
                        tool_args = {}
                except Exception as e:
                    print(f"❌ [Agent] Unexpected error parsing arguments for {tool_name}:\n{traceback.format_exc()}")
                    tool_args = {}

                if not tool_args and tool_name == "write_file":
                    tool_args = {"filepath": "", "content": ""}
                if tool_name == "write_files" and not tool_args.get("files"):
                    tool_args = {"files": []}

                # If write_files JSON was truncated, skip execution and tell model to use write_file
                if _json_was_truncated and tool_name == "write_files":
                    truncated_result = {
                        "success": False,
                        "error": (
                            "write_files JSON was truncated (output too long). "
                            "Do NOT retry write_files. Instead use write_file to write each file "
                            "ONE AT A TIME in separate tool calls."
                        ),
                    }
                    result_step = AgentStep(
                        step_number=step_num + step_offset,
                        type="tool_result",
                        content="⚠️ write_files обрезан — переключаюсь на write_file",
                        tool_name=tool_name, tool_result=truncated_result,
                    )
                    self.steps.append(result_step)
                    if on_step:
                        await on_step(result_step)
                    return {
                        "tool_call_id": tool_call.id,
                        "tool_name": tool_name,
                        "result": truncated_result,
                    }

                # Notify: tool call
                call_step = AgentStep(
                    step_number=step_num + step_offset,
                    type="tool_call",
                    content=f"Вызываю: {tool_name}",
                    tool_name=tool_name,
                    tool_args=tool_args,
                )
                self.steps.append(call_step)
                if on_step:
                    await on_step(call_step)

                print(f"🔧 [Agent] Вызываю инструмент: {tool_name}")

                # Execute
                try:
                    result = await self.tool_executor.execute(tool_name, tool_args)
                    print(f"📋 [Agent] {tool_name}: {'✅' if result.get('success') else '❌'}")
                except Exception as e:
                    print(f"❌ [Agent] Ошибка выполнения {tool_name}:\n{traceback.format_exc()}")
                    result = {"success": False, "error": str(e), "result": None}

                # ── Record for escalation ─────────────────────────
                self.escalation.record_tool_result(
                    tool_name,
                    make_args_hash(tool_args),
                    result.get("success", False),
                )

                # Notify: tool result
                result_step = AgentStep(
                    step_number=step_num + step_offset + 1,
                    type="tool_result",
                    content=self._summarize_result(tool_name, result),
                    tool_name=tool_name,
                    tool_result=result,
                )
                self.steps.append(result_step)
                if on_step:
                    await on_step(result_step)

                return {
                    "tool_call_id": tool_call.id,
                    "tool_name": tool_name,
                    "result": result,
                }

            # ── Parallel execution of independent tools ───────────
            tool_results = []
            independent_tools = {
                'read_file', 'write_file', 'list_files',
                'browser_screenshot', 'browser_get_content',
                'browser_get_page_structure',
                'browser_get_console_logs', 'browser_get_network_failures',
                'browser_execute_script', 'browser_scroll',
            }

            current_step = 0
            i = 0
            while i < len(message.tool_calls):
                tool_call = message.tool_calls[i]

                if stop_check():
                    stop_step = AgentStep(
                        step_number=step_num + current_step + 1,
                        type="response",
                        content="⏹️ Выполнение остановлено пользователем",
                    )
                    if on_step:
                        await on_step(stop_step)
                    return "Выполнение остановлено пользователем"

                tool_name = tool_call.function.name

                if tool_name in independent_tools:
                    batch = [tool_call]
                    j = i + 1
                    while j < len(message.tool_calls):
                        if message.tool_calls[j].function.name in independent_tools:
                            batch.append(message.tool_calls[j])
                            j += 1
                        else:
                            break

                    if len(batch) > 1:
                        print(f"⚡ [Agent] Параллельное выполнение {len(batch)} независимых инструментов")
                        tasks = [
                            execute_single_tool(tc, current_step + idx * 2)
                            for idx, tc in enumerate(batch)
                        ]
                        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                        for idx, br in enumerate(batch_results):
                            if isinstance(br, Exception):
                                tc = batch[idx]
                                tn = tc.function.name if getattr(tc, "function", None) else "unknown"
                                print(f"❌ [Agent] Tool batch item failed ({tn}): {br}")
                                tool_results.append({
                                    "tool_call_id": tc.id,
                                    "tool_name": tn,
                                    "result": {"success": False, "error": str(br), "result": None},
                                })
                            elif isinstance(br, dict):
                                tool_results.append(br)
                            else:
                                tc = batch[idx]
                                tn = tc.function.name if getattr(tc, "function", None) else "unknown"
                                print(f"⚠️ [Agent] Tool batch item returned non-dict ({tn}): {type(br).__name__}")
                                tool_results.append({
                                    "tool_call_id": tc.id,
                                    "tool_name": tn,
                                    "result": {"success": False, "error": "Tool returned invalid result", "result": None},
                                })
                        current_step += len(batch) * 2
                        i += len(batch)
                    else:
                        result = await execute_single_tool(tool_call, current_step)
                        if isinstance(result, dict):
                            tool_results.append(result)
                        else:
                            print(f"⚠️ [Agent] Tool returned non-dict: {type(result).__name__}")
                            tool_results.append({
                                "tool_call_id": tool_call.id,
                                "tool_name": tool_name,
                                "result": {"success": False, "error": "Tool returned invalid result", "result": None},
                            })
                        current_step += 2
                        i += 1
                else:
                    result = await execute_single_tool(tool_call, current_step)
                    if isinstance(result, dict):
                        tool_results.append(result)
                    else:
                        print(f"⚠️ [Agent] Tool returned non-dict: {type(result).__name__}")
                        tool_results.append({
                            "tool_call_id": tool_call.id,
                            "tool_name": tool_name,
                            "result": {"success": False, "error": "Tool returned invalid result", "result": None},
                        })
                    current_step += 2
                    i += 1

            # ── Add tool messages with smart compression ──────────
            for tr in tool_results:
                if not isinstance(tr, dict):
                    print(f"⚠️ [Agent] Skipping malformed tool result item: {type(tr).__name__}")
                    continue
                result = tr.get("result")
                t_name = tr.get("tool_name", "unknown")
                t_call_id = tr.get("tool_call_id")
                if t_call_id is None:
                    print(f"⚠️ [Agent] Skipping tool result without tool_call_id: {tr}")
                    continue
                if not isinstance(result, dict):
                    result = {"success": False, "error": "Malformed tool result", "result": None}

                # Serialize and compress using the context module
                raw_json = json.dumps(result, ensure_ascii=False)
                compressed = compress_tool_result(t_name, raw_json)

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": t_call_id,
                    "content": compressed,
                })

        extension = max(0, int(getattr(settings, "AGENT_ITERATION_EXTENSION", 0) or 0))
        if allow_auto_extend and extension > 0 and not stop_check() and not self.escalation.is_stuck:
            note = (
                f"⚙️ Достигнут лимит {max_iterations} итераций, "
                f"автоматически добавляю ещё {extension} для завершения задачи..."
            )
            extend_step = AgentStep(step_number=step_num + 1, type="thinking", content=note)
            if on_step:
                await on_step(extend_step)
            print(f"🔁 [Agent] Auto-extending iterations: +{extension}")
            return await self._run_loop(
                step_num,
                on_step,
                task_type,
                images,
                extension,
                stop_ref,
                allow_auto_extend=False,
            )

        return "Достигнут лимит итераций. Задача слишком сложная — попробуйте разбить на подзадачи."

    def _repair_json_args(self, tool_name: str, args_str: str) -> dict:
        """Attempt to repair malformed JSON from LLM."""
        print(f"⚠️ [Agent] Malformed JSON for {tool_name}: '{args_str[:300]}...'")

        if tool_name == "write_file":
            filepath_regex, content_regex = AgentEngine._get_regex_patterns()
            tool_args = {"filepath": "", "content": ""}
            filepath_match = filepath_regex.search(args_str)
            content_match = content_regex.search(args_str)

            if filepath_match:
                tool_args["filepath"] = filepath_match.group(1)
                if content_match:
                    import re
                    content = content_match.group(1)
                    content = re.sub(r'"\s*[,}].*$', '', content, flags=re.DOTALL)
                    content = (content
                               .replace('\\n', '\n')
                               .replace('\\t', '\t')
                               .replace('\\"', '"')
                               .replace('\\\\', '\\'))
                    tool_args["content"] = content
                print(f"✅ [Agent] Extracted: filepath={tool_args['filepath']}, "
                      f"content_len={len(tool_args.get('content', ''))}")
            return tool_args

        if tool_name == "write_files":
            import re
            filepath_regex, content_regex = AgentEngine._get_regex_patterns()
            files = []
            # Try to extract individual file objects from truncated JSON
            for m in re.finditer(r'\{\s*"filepath"\s*:\s*"([^"]+)"\s*,\s*"content"\s*:\s*"', args_str):
                fp = m.group(1)
                start = m.end()
                # Find end of this content string (may be truncated)
                end = args_str.find('"}', start)
                if end == -1:
                    end = len(args_str)
                raw = args_str[start:end]
                raw = (raw
                       .replace('\\n', '\n')
                       .replace('\\t', '\t')
                       .replace('\\"', '"')
                       .replace('\\\\', '\\'))
                files.append({"filepath": fp, "content": raw})
            if files:
                print(f"✅ [Agent] Repaired write_files: extracted {len(files)} file(s)")
                return {"files": files}
            print(f"⚠️ [Agent] write_files repair failed — no files extracted")
            return {"files": []}

        return {}

    def _summarize_result(self, tool_name: str, result: dict) -> str:
        """Create a human-readable summary of a tool result."""
        if not result.get("success", False):
            return f"❌ Ошибка: {result.get('error', 'unknown')}"

        r = result.get("result", {})

        if tool_name == "execute_command":
            exit_code = r.get("exit_code", -1)
            if exit_code == 0:
                stdout = r.get("stdout", "").strip()
                return f"✅ Команда выполнена" + (f": {stdout[:200]}" if stdout else "")
            else:
                stderr = r.get("stderr", "").strip()
                return f"⚠️ Код выхода {exit_code}: {stderr[:200]}"

        elif tool_name == "write_file":
            return f"✅ Файл создан: {r.get('filepath', '?')}"

        elif tool_name == "write_files":
            written = r.get("written", [])
            errs = r.get("errors")
            if errs:
                return f"✅ Записано файлов: {len(written)}, ошибок: {len(errs)}"
            return f"✅ Записано файлов: {len(written)}"

        elif tool_name == "read_file":
            content = r.get("content", "")
            lines = len(content.split("\n"))
            return f"📄 Прочитан файл: {r.get('filepath', '?')} ({lines} строк)"

        elif tool_name == "list_files":
            files = r.get("files", [])
            return f"📁 Найдено {len(files)} файлов в {r.get('path', '?')}"

        elif tool_name == "browser_get_page_structure":
            el = r.get("elements", [])
            return f"✅ На странице {len(el)} элементов (inputs/buttons). Используй их selector для type и click."
        elif tool_name == "browser_select":
            return f"✅ Выбрана опция: {r.get('selector', '?')}"
        elif tool_name == "browser_fill_form":
            return f"✅ Форма заполнена: {r.get('filled', 0)} полей, URL: {r.get('url', '')[:50]}"
        elif tool_name == "browser_screenshot":
            pt = r.get("page_text", "")
            if len(pt) > 150:
                return f"✅ Скриншот сохранён. Текст страницы: {pt[:150]}..."
            return f"✅ Скриншот сохранён. Текст страницы: {pt or '(пусто)'}"
        elif tool_name == "browser_get_console_logs":
            logs = r.get("logs", [])
            if logs:
                return f"✅ Консоль: {len(logs)} записей. Используй для поиска JS-ошибок."
            return "✅ Консоль: записей нет."
        elif tool_name == "browser_get_network_failures":
            fails = r.get("request_failures", [])
            bad = r.get("bad_status_responses", [])
            return f"✅ Сеть: {len(fails)} сбоев запросов, {len(bad)} ответов 4xx/5xx."
        elif tool_name == "browser_execute_script":
            return f"✅ Скрипт выполнен: {r.get('result', 'ok')[:80]}"
        elif tool_name == "browser_scroll":
            return f"✅ Страница прокручена: {r.get('scrolled', 'ok')}"

        return f"✅ {tool_name} выполнен"

    async def _run_multi_agent(
        self,
        user_message: str,
        on_step: Optional[Callable[[AgentStep], Awaitable[None]]] = None,
    ) -> str:
        """
        Multi-agent workflow: Planner → Coder → Reviewer
        """
        self._stop_requested = False
        try:
            plan_step = AgentStep(
                step_number=0,
                type="thinking",
                content="🤔 Планирую выполнение задачи...",
            )
            if on_step:
                await on_step(plan_step)

            if self._stop_requested:
                return "Выполнение остановлено пользователем"

            plan = await self.planner.plan(user_message, on_step)

            plan_summary_step = AgentStep(
                step_number=1,
                type="response",
                content=f"📋 План создан: {len(plan.get('steps', []))} шагов",
            )
            if on_step:
                await on_step(plan_summary_step)

            execution_results = []
            steps = plan.get('steps', [])

            for i, step in enumerate(steps):
                if self._stop_requested:
                    stop_step = AgentStep(
                        step_number=i + 2,
                        type="response",
                        content="⏹️ Выполнение остановлено пользователем",
                    )
                    if on_step:
                        await on_step(stop_step)
                    return "Выполнение остановлено пользователем"

                exec_step = AgentStep(
                    step_number=i + 2,
                    type="thinking",
                    content=f"⚙️ Выполняю шаг {step.get('number', i+1)}: {step.get('action', '')}",
                )
                if on_step:
                    await on_step(exec_step)

                result = await self._coder.execute_step(step, on_step)
                result['step_number'] = step.get('number', i + 1)
                execution_results.append(result)

            review_step = AgentStep(
                step_number=len(steps) + 2,
                type="thinking",
                content="🔍 Проверяю результаты выполнения...",
            )
            if on_step:
                await on_step(review_step)

            review = await self._reviewer.review(plan, execution_results, on_step)

            if review.get('approved', True):
                final_message = f"✅ Задача выполнена успешно!\n\nПлан: {len(steps)} шагов\n"
                if review.get('suggestions'):
                    final_message += f"\n💡 Предложения: {', '.join(review['suggestions'][:3])}"
            else:
                final_message = f"⚠️ Задача выполнена с замечаниями:\n"
                for issue in review.get('issues', [])[:3]:
                    final_message += f"- {issue.get('issue', '')}\n"

            final_step = AgentStep(
                step_number=len(steps) + 3,
                type="response",
                content=final_message,
            )
            if on_step:
                await on_step(final_step)

            return final_message

        except Exception as e:
            print(f"❌ [Agent] Multi-agent workflow error:\n{traceback.format_exc()}")
            error_msg = f"Ошибка в multi-agent workflow: {str(e)}"
            error_step = AgentStep(
                step_number=999,
                type="error",
                content=error_msg,
            )
            if on_step:
                await on_step(error_step)
            return error_msg
