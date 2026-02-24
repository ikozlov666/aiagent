"""
LLM Router — routes requests to the optimal model based on task type.
All providers use OpenAI-compatible API via the `openai` Python package.

v2: Updated routing table for new task classes, provider override for escalation.
"""
import json
import time
from typing import AsyncGenerator, Optional
from openai import AsyncOpenAI
from config import settings


class LLMProvider:
    """Wrapper around OpenAI-compatible API."""

    def __init__(self, name: str, api_key: str, base_url: str, model: str):
        self.name = name
        self.model = model
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        ) if api_key else None

    @property
    def available(self) -> bool:
        return self.client is not None and bool(self.client.api_key)

    async def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = False,
        images: Optional[list[dict]] = None,
    ):
        """
        Send chat completion request.
        
        Args:
            messages: List of message dicts
            tools: Optional list of tool definitions
            temperature: Sampling temperature
            max_tokens: Max tokens in response
            stream: Whether to stream response
            images: Optional list of image dicts with 'url' or 'base64' and 'mime_type'
        """
        if not self.available:
            raise ValueError(f"Provider {self.name} is not configured (no API key)")

        # Process messages to include images if provided
        processed_messages = self._process_messages_with_images(messages, images)

        kwargs = {
            "model": self.model,
            "messages": processed_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = await self.client.chat.completions.create(**kwargs)
        return response

    def _process_messages_with_images(self, messages: list[dict], images: Optional[list[dict]]) -> list[dict]:
        """Process messages to include image content for vision models."""
        if not images:
            return messages
        
        processed = []
        for msg in messages:
            if msg.get("role") == "user" and images:
                # Convert text message to content array with text + images
                content = []
                if msg.get("content"):
                    content.append({"type": "text", "text": msg["content"]})
                
                for img in images:
                    if "base64" in img:
                        content.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{img.get('mime_type', 'image/png')};base64,{img['base64']}"
                            }
                        })
                    elif "url" in img:
                        content.append({
                            "type": "image_url",
                            "image_url": {"url": img["url"]}
                        })
                
                processed.append({
                    "role": msg["role"],
                    "content": content if len(content) > 1 else (content[0]["text"] if content else "")
                })
            else:
                processed.append(msg)
        
        return processed

    async def chat_stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        """Stream chat completion response."""
        response = await self.chat(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class CostTracker:
    """Tracks LLM API costs."""

    # Approximate costs per 1M tokens (input/output)
    COSTS = {
        "deepseek": {"input": 0.14, "output": 0.28},
        "qwen": {"input": 0.15, "output": 0.60},
        "claude": {"input": 3.0, "output": 15.0},
        "openai": {"input": 2.5, "output": 10.0},
    }

    def __init__(self):
        self.total_cost = 0.0
        self.requests = []

    def track(self, provider: str, input_tokens: int, output_tokens: int):
        costs = self.COSTS.get(provider, {"input": 1.0, "output": 3.0})
        cost = (input_tokens * costs["input"] + output_tokens * costs["output"]) / 1_000_000
        self.total_cost += cost
        self.requests.append({
            "provider": provider,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost,
            "timestamp": time.time(),
        })
        return cost

    def get_summary(self) -> dict:
        return {
            "total_cost": round(self.total_cost, 4),
            "total_requests": len(self.requests),
            "by_provider": self._by_provider(),
        }

    def _by_provider(self) -> dict:
        result = {}
        for req in self.requests:
            p = req["provider"]
            if p not in result:
                result[p] = {"requests": 0, "cost": 0.0}
            result[p]["requests"] += 1
            result[p]["cost"] += req["cost"]
        return result


class LLMRouter:
    """Routes LLM requests to the optimal provider based on task type."""

    # Providers whose API accepts content with type "image_url"
    VISION_PROVIDERS = {"openai", "claude", "deepseek"}

    # ── Routing rules: task_type → provider_name ──────────────────
    # Updated for new task classes from classifier.py
    ROUTING = {
        # New task classes
        "simple_chat": "deepseek",
        "quick_build": "deepseek",
        "coding":      "deepseek",
        "browser":     "deepseek",
        "complex":     "deepseek",
        "debug":       "deepseek",
        "review":      "claude",     # review benefits from stronger model
        "vision":      "claude",     # vision requires multi-modal

        # Legacy task types (backward compat with main.py)
        "planning":    "deepseek",
        "simple_fix":  "qwen",
        "default":     "deepseek",
    }

    def __init__(self):
        self.providers = {
            "deepseek": LLMProvider(
                name="deepseek",
                api_key=settings.DEEPSEEK_API_KEY,
                base_url=settings.DEEPSEEK_BASE_URL,
                model=settings.DEEPSEEK_MODEL,
            ),
            "qwen": LLMProvider(
                name="qwen",
                api_key=settings.QWEN_API_KEY,
                base_url=settings.QWEN_BASE_URL,
                model=settings.QWEN_MODEL,
            ),
            "claude": LLMProvider(
                name="claude",
                api_key=settings.CLAUDE_API_KEY,
                base_url=settings.CLAUDE_BASE_URL,
                model=settings.CLAUDE_MODEL,
            ),
            "openai": LLMProvider(
                name="openai",
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
                model=settings.OPENAI_MODEL,
            ),
        }
        self.cost_tracker = CostTracker()
        self._fallback_order = ["deepseek", "qwen", "openai", "claude"]

    def get_provider(self, task_type: str = "default") -> LLMProvider:
        """
        Get the best available provider for the given task type.

        If task_type matches a provider name directly (e.g. "openai", "claude"),
        that provider is used — this supports escalation override.
        """
        # Direct provider override (used by escalation)
        if task_type in self.providers:
            provider = self.providers[task_type]
            if provider.available:
                return provider
            # If the direct override is unavailable, fall through to routing

        # Normal routing table lookup
        provider_name = self.ROUTING.get(task_type, settings.DEFAULT_LLM_PROVIDER)
        provider = self.providers.get(provider_name)

        if provider and provider.available:
            return provider

        # Fallback: find any available provider
        for name in self._fallback_order:
            p = self.providers.get(name)
            if p and p.available:
                print(f"⚠️  Fallback: {provider_name} → {name}")
                return p

        raise RuntimeError("No LLM providers configured! Set at least one API key in .env")

    def _messages_without_images_and_note(self, messages: list[dict], images_count: int) -> list[dict]:
        """Copy of messages without images; adds a note to the first user message."""
        result = []
        for msg in messages:
            if msg.get("role") == "user" and images_count > 0:
                note = "\n\n[Пользователь приложил изображение(я). Текущая модель не поддерживает просмотр картинок — ответь вежливо и попроси описать задачу текстом или прикрепить текст.]"
                content = (msg.get("content") or "") + note
                result.append({"role": msg["role"], "content": content})
                images_count = 0
            else:
                result.append(dict(msg))
        return result

    async def chat(
        self,
        messages: list[dict],
        task_type: str = "default",
        tools: Optional[list[dict]] = None,
        **kwargs,
    ):
        """Route a chat request to the optimal provider."""
        provider = self.get_provider(task_type)
        images = kwargs.pop("images", None)

        # If provider doesn't support images, strip them and add text note
        if images and provider.name not in self.VISION_PROVIDERS:
            messages = self._messages_without_images_and_note(messages, len(images))
            images = None
            print(f"⚠️ [LLM] Провайдер {provider.name} не поддерживает изображения — отправляем только текст")

        try:
            response = await provider.chat(messages=messages, tools=tools, images=images, **kwargs)

            # Track cost
            if hasattr(response, "usage") and response.usage:
                self.cost_tracker.track(
                    provider.name,
                    response.usage.prompt_tokens,
                    response.usage.completion_tokens,
                )

            return response
        except Exception as e:
            err_str = str(e).lower()
            # If API rejected image_url — retry without images
            if images and ("image_url" in err_str or "expected 'text'" in err_str or 'expected "text"' in err_str):
                print(f"⚠️ [LLM] API не поддерживает изображения ({provider.name}), повтор без картинок")
                messages = self._messages_without_images_and_note(messages, len(images))
                return await provider.chat(messages=messages, tools=tools, images=None, **kwargs)
            print(f"❌ Error with {provider.name}: {e}")
            # Try fallback
            for name in self._fallback_order:
                if name == provider.name:
                    continue
                p = self.providers.get(name)
                if p and p.available:
                    print(f"🔄 Retrying with {name}")
                    fallback_messages = messages
                    fallback_images = images
                    if images and p.name not in self.VISION_PROVIDERS:
                        fallback_messages = self._messages_without_images_and_note(messages, len(images))
                        fallback_images = None
                    return await p.chat(messages=fallback_messages, tools=tools, images=fallback_images, **kwargs)
            raise

    async def chat_stream(
        self,
        messages: list[dict],
        task_type: str = "default",
        **kwargs,
    ):
        """Stream chat completion (for simple-chat path)."""
        provider = self.get_provider(task_type)
        images = kwargs.pop("images", None)
        if images and provider.name not in self.VISION_PROVIDERS:
            messages = self._messages_without_images_and_note(messages, len(images))
            images = None
        if images:
            messages = provider._process_messages_with_images(messages, images)
        kwargs.setdefault("tools", None)
        async for chunk in provider.chat_stream(messages=messages, **kwargs):
            yield chunk


# Singleton
llm_router = LLMRouter()
