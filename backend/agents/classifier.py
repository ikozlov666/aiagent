"""
Task classifier — determines task class for prompt selection and LLM routing.

Two levels:
  Level 1 — fast rule-based (0 ms, 0 tokens): covers 90%+ of cases.
  Level 2 — optional LLM-based (for ambiguous messages): one cheap call.

Task classes:
  "simple_chat"  — greetings, questions, follow-ups (no tools)
  "quick_build"  — simple one-page site / landing
  "coding"       — standard code generation / modification
  "browser"      — open website, scraping, testing in browser
  "complex"      — multi-step, full-stack, architecture tasks
  "debug"        — fix errors, investigate bugs
  "review"       — code review, explain code
  "vision"       — image analysis (attached images)

Usage:
    from agents.classifier import classify_task
    task_class = classify_task("создай лендинг для кофейни")
    # -> "quick_build"
"""

import re
from typing import Optional


# ─── Compiled patterns (module-level, compiled once) ──────────────

_GREETING_RE = re.compile(
    r"^(привет|здравствуй|хай|хелло|hello|hi|hey|yo|good\s*morning|"
    r"добр(?:ый|ое|ого)|как\s+дела|что\s+нового|кто\s+ты|"
    r"what(?:'s|\s+is)\s+up)\b",
    re.IGNORECASE,
)

_QUESTION_RE = re.compile(
    r"^(что\s+такое|как\s+работает|зачем|почему|объясни|расскажи|"
    r"what\s+is|how\s+does|explain|tell\s+me\s+about|why\s+do)\b",
    re.IGNORECASE,
)

_LIST_MARKERS_RE = re.compile(r"(\d+\.\s|\n-\s|\n\*\s)")


# ─── Keyword sets ─────────────────────────────────────────────────

_CODE_KEYWORDS = frozenset({
    # Russian
    "создай", "напиши", "сделай", "добавь", "исправь", "удали",
    "файл", "компонент", "страниц", "сервер", "api", "база",
    "установи", "запусти", "собери", "разверни", "настрой",
    "модуль", "функци", "класс", "роут", "эндпоинт",
    # English
    "create", "write", "make", "build", "add", "remove",
    "install", "run", "deploy", "configure", "implement", "refactor",
    "module", "function", "class", "route", "endpoint", "component",
})

_BROWSER_KEYWORDS = frozenset({
    "открой", "открыть", "перейди", "зайди на", "сайт",
    "браузер", "парс", "скрап", "спарси",
    "open", "navigate", "browse", "visit", "scrape", "parse",
    "google", "яндекс", "website", "url", "http",
    "погод", "узнай погод", "weather",
})

_DEBUG_KEYWORDS = frozenset({
    "ошибк", "баг", "не работает", "сломал", "крашится", "падает",
    "error", "bug", "broken", "crash", "debug", "issue",
    "почему не", "why doesn't", "why isn't", "not working",
    "failed", "failing", "exception", "traceback",
})

_REVIEW_KEYWORDS = frozenset({
    "review", "ревью", "проверь код", "посмотри код",
    "code review", "объясни код", "explain code",
    "покажи проблемы", "найди баги",
})

_COMPLEX_KEYWORDS = frozenset({
    "полноценн", "full-stack", "fullstack", "приложение",
    "application", "платформ", "систем", "архитектур",
    "фронтенд и бэкенд", "frontend and backend",
    "база данных", "database", "авторизац", "authentication",
    "crud", "dashboard", "панель управлен",
})

_QUICK_BUILD_KEYWORDS = frozenset({
    "одностраничник", "лендинг", "landing", "simple page",
    "one page", "one-page", "простой сайт", "простая страниц",
    "minimal site", "минимальн",
})


def _kw_match(text_lower: str, keywords: frozenset) -> bool:
    """Check if any keyword is found in the lowered text."""
    return any(kw in text_lower for kw in keywords)


def classify_task(
    message: str,
    images: Optional[list] = None,
    history_len: int = 0,
) -> str:
    """
    Classify user message into a task class (Level 1: rule-based).

    Args:
        message:     The user's message text.
        images:      List of attached images (if any).
        history_len: Number of messages in conversation history
                     (used to bias short follow-ups toward simple_chat).

    Returns:
        One of: "simple_chat", "quick_build", "coding", "browser",
                "complex", "debug", "review", "vision"
    """
    # ── Images → vision ───────────────────────────────────────────
    if images:
        return "vision"

    text = message.strip()
    lower = text.lower()
    word_count = len(text.split())

    # ── Greetings (short + pattern match) ─────────────────────────
    if word_count <= 15 and _GREETING_RE.search(lower):
        # Unless it also contains code keywords ("привет, создай сайт")
        if not _kw_match(lower, _CODE_KEYWORDS) and not _kw_match(lower, _BROWSER_KEYWORDS):
            return "simple_chat"

    # ── Short follow-ups in existing conversations ────────────────
    # "да", "нет", "ага", "ок", "хорошо", "дальше", "покажи"
    if word_count <= 3 and history_len > 2:
        short_tokens = {"да", "нет", "ага", "ок", "хорошо", "ладно", "дальше",
                        "покажи", "yes", "no", "ok", "sure", "next", "go",
                        "спасибо", "thanks", "thank you"}
        if lower in short_tokens or text.rstrip("!?.") in short_tokens:
            return "simple_chat"

    # ── General questions (no code keywords) ──────────────────────
    if (word_count <= 30
            and _QUESTION_RE.search(lower)
            and not _kw_match(lower, _CODE_KEYWORDS)
            and not _kw_match(lower, _BROWSER_KEYWORDS)):
        return "simple_chat"

    # ── Code review ───────────────────────────────────────────────
    if _kw_match(lower, _REVIEW_KEYWORDS):
        return "review"

    # ── Browser tasks ─────────────────────────────────────────────
    if _kw_match(lower, _BROWSER_KEYWORDS):
        # If also has code keywords, it's probably coding with browser test
        if _kw_match(lower, _COMPLEX_KEYWORDS):
            return "complex"
        return "browser"

    # ── Debug / fix ───────────────────────────────────────────────
    if _kw_match(lower, _DEBUG_KEYWORDS):
        return "debug"

    # ── Quick build (one-pager) ───────────────────────────────────
    if word_count <= 25 and _kw_match(lower, _QUICK_BUILD_KEYWORDS):
        return "quick_build"

    # ── Complex task ──────────────────────────────────────────────
    if _kw_match(lower, _COMPLEX_KEYWORDS):
        return "complex"

    # Long message with list markers → complex
    if word_count > 40 and _LIST_MARKERS_RE.search(text):
        return "complex"

    # Very long description → complex
    if word_count > 120:
        return "complex"

    # ── Standard coding ───────────────────────────────────────────
    if _kw_match(lower, _CODE_KEYWORDS):
        return "coding"

    # ── Fallback ──────────────────────────────────────────────────
    # Short message without recognizable keywords → chat
    if word_count <= 12:
        return "simple_chat"

    # Default: coding
    return "coding"


def should_use_parallel_plan(message: str, images: Optional[list] = None) -> bool:
    """
    Whether to try splitting the task into parallel subtasks.
    Moved here from engine.py for consistency.
    """
    if images:
        return False
    text = message.strip()
    if len(text) < 80:
        return False
    lower = text.lower()
    if " и " in lower or " + " in text or " плюс " in lower:
        return True
    if any(w in lower for w in [
        "ландинг", "лендинг", "api", "фронт", "бэк",
        "frontend", "backend", "сервер", "сайт",
    ]):
        return True
    return len(text) > 150
