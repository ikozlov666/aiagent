"""
Планировщик и параллельное выполнение подзадач.
Один вызов LLM разбивает задачу на подзадачи; независимые выполняются параллельно.
"""
import json
import re
import asyncio
from typing import Callable, Awaitable, Optional, List

from llm.router import llm_router

# Промпт для разбиения задачи на подзадачи (JSON)
PLANNER_PROMPT = """Разбей задачу пользователя на подзадачи для параллельного выполнения.

Правила:
- Если задача одна и не делится — верни массив из одной подзадачи с полным текстом.
- Если можно сделать 2+ независимых части (например: "лендинг + API", "фронт и бэк") — разбей.
- Каждая подзадача должна быть самодостаточной (свои файлы/команды).
- Ответь ТОЛЬКО валидным JSON-массивом, без markdown и пояснений.

Формат:
[
  {"id": "1", "description": "описание подзадачи 1", "depends_on": []},
  {"id": "2", "description": "описание подзадачи 2", "depends_on": []}
]

depends_on — список id подзадач, которые должны выполниться раньше (обычно [] для параллельных).
Язык описаний — как у пользователя."""


async def plan_task(user_message: str) -> List[dict]:
    """
    Один вызов LLM: разбить задачу на подзадачи.
    Возвращает список {"id", "description", "depends_on"}.
    """
    messages = [
        {"role": "system", "content": PLANNER_PROMPT},
        {"role": "user", "content": user_message},
    ]
    try:
        response = await llm_router.chat(
            messages=messages,
            task_type="coding",
            tools=None,
            temperature=0.2,
            max_tokens=1024,
        )
        text = (response.choices[0].message.content or "").strip()
        # Убрать markdown code block если есть
        if "```" in text:
            match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
            if match:
                text = match.group(1).strip()
        data = json.loads(text)
        if not isinstance(data, list):
            return [{"id": "1", "description": user_message, "depends_on": []}]
        out = []
        for i, item in enumerate(data):
            if isinstance(item, dict):
                out.append({
                    "id": str(item.get("id", i + 1)),
                    "description": str(item.get("description", "")).strip() or user_message,
                    "depends_on": item.get("depends_on") if isinstance(item.get("depends_on"), list) else [],
                })
        return out if out else [{"id": "1", "description": user_message, "depends_on": []}]
    except Exception as e:
        print(f"⚠️ [Planner] Ошибка: {e}")
        return [{"id": "1", "description": user_message, "depends_on": []}]


def topological_waves(subtasks: List[dict]) -> List[List[dict]]:
    """
    Разбить подзадачи на "волны" по зависимостям.
    Волна 0 — без зависимостей, волна 1 — зависят от волны 0, и т.д.
    """
    ids = {s["id"] for s in subtasks}
    waves = []
    done = set()

    while len(done) < len(subtasks):
        wave = []
        for s in subtasks:
            if s["id"] in done:
                continue
            deps = set(s.get("depends_on") or [])
            if deps <= done and (deps <= ids or not deps):
                wave.append(s)
        if not wave:
            # циклическая зависимость или битые id — добавляем оставшиеся
            wave = [s for s in subtasks if s["id"] not in done]
        for s in wave:
            done.add(s["id"])
        waves.append(wave)
    return waves


async def merge_results(user_message: str, results: List[str]) -> str:
    """Один вызов LLM: собрать итоговый ответ из результатов подзадач."""
    parts = "\n\n".join([f"Подзадача {i+1}:\n{r}" for i, r in enumerate(results)])
    messages = [
        {"role": "system", "content": "Ты ассистент. Пользователь попросил выполнить задачу. Ниже результаты подзадач. Дай краткий итог на языке пользователя: что сделано, как запустить/проверить. Без лишнего."},
        {"role": "user", "content": f"Запрос пользователя: {user_message}\n\nРезультаты:\n{parts}"},
    ]
    try:
        response = await llm_router.chat(
            messages=messages,
            task_type="coding",
            tools=None,
            temperature=0.3,
            max_tokens=1024,
        )
        return (response.choices[0].message.content or "").strip() or "Готово."
    except Exception as e:
        return "\n\n".join(results) + f"\n\n(Сводка не сформирована: {e})"
