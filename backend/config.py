"""
Application configuration.
All LLM providers use OpenAI-compatible API format.
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # ============================================
    # App
    # ============================================
    APP_NAME: str = "AI Agent Platform"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ============================================
    # Docker Sandbox
    # ============================================
    SANDBOX_IMAGE: str = "ai-agent-sandbox:latest"
    SANDBOX_MEM_LIMIT: str = "8g"
    SANDBOX_CPU_QUOTA: int = 800000  # 8 CPU cores
    SANDBOX_CPU_PERIOD: int = 100000
    PROJECTS_DIR: str = "./projects"

    # ============================================
    # LLM Providers (all OpenAI-compatible)
    # ============================================
    # DeepSeek — primary, cheapest
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # Qwen — secondary cheap option
    QWEN_API_KEY: str = ""
    QWEN_BASE_URL: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    QWEN_MODEL: str = "qwen-plus"

    # Claude (via OpenAI-compatible proxy or direct)
    CLAUDE_API_KEY: str = ""
    CLAUDE_BASE_URL: str = "https://api.anthropic.com/v1"
    CLAUDE_MODEL: str = "claude-sonnet-4-20250514"

    # OpenAI — fallback
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o"

    # Default provider for agent work
    DEFAULT_LLM_PROVIDER: str = "deepseek"

    # ============================================
    # Agent: timeout (как в ChatGPT — лимит на один ответ модели, не на весь сценарий)
    # ============================================
    LLM_REQUEST_TIMEOUT_SECONDS: int = 120  # Таймаут одного запроса к LLM (сек). Если модель не ответила — ошибка шага, агент не падает.
    AGENT_TIMEOUT_SECONDS: int = 0  # 0 = без лимита на весь сценарий (как ChatGPT). >0 = макс. время всего run в секундах.
    AGENT_USE_STREAMING: bool = True  # Stream simple-chat replies token-by-token
    AGENT_ENHANCED_CONTEXT: bool = True  # Richer context summary (files, URL, errors)
    AGENT_CURRENT_GOAL_IN_CONTEXT: bool = True  # Add "current goal" when context is compressed
    AGENT_REDUCED_MAX_TOKENS: bool = True  # Use 1536 instead of 2048 in main loop (faster)
    AGENT_MAX_ITERATIONS: int = 50  # Base iteration budget for the main agent loop
    AGENT_SUBTASK_MAX_ITERATIONS: int = 25  # Iteration budget for each parallel subtask engine
    AGENT_ITERATION_EXTENSION: int = 20  # Extra iterations when limit is reached but work is still progressing

    # ============================================
    # Redis (optional, for sessions/caching)
    # ============================================
    REDIS_URL: str = "redis://localhost:6379/0"

    # ============================================
    # PostgreSQL Database
    # ============================================
    POSTGRES_USER: str = "aiagent"
    POSTGRES_PASSWORD: str = "aiagent123"
    POSTGRES_DB: str = "aiagent"
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432

    @property
    def DATABASE_URL(self) -> str:
        """Construct PostgreSQL connection URL."""
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # ============================================
    # JWT Authentication
    # ============================================
    SECRET_KEY: str = "your-secret-key-change-in-production"  # Should be in .env
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # ============================================
    # Telegram Bot
    # ============================================
    TELEGRAM_BOT_TOKEN: str = ""  # Should be in .env
    TELEGRAM_WEBHOOK_URL: Optional[str] = None  # Optional webhook URL

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
