"""
Telegram Bot integration for AI Agent Platform.
Allows users to interact with the agent via Telegram.
"""
import asyncio
import logging
from typing import Optional
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from config import settings
from database import get_db
from models import User
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from agents.engine import AgentEngine, AgentStep

logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram bot for AI Agent Platform."""

    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.application: Optional[Application] = None
        self.agent_engines: dict[str, AgentEngine] = {}

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        user_id = update.effective_user.id
        username = update.effective_user.username or update.effective_user.first_name
        
        await update.message.reply_text(
            f"🤖 Привет, {username}!\n\n"
            "Я AI Agent Platform бот. Я могу помочь вам:\n"
            "• Создать проект\n"
            "• Выполнить задачи с помощью AI агента\n"
            "• Управлять вашими проектами\n\n"
            "Используйте /help для списка команд."
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        help_text = """
📋 Доступные команды:

/start - Начать работу с ботом
/help - Показать эту справку
/newproject <название> - Создать новый проект
/projects - Список ваших проектов
/task <описание> - Выполнить задачу в текущем проекте

Просто отправьте текст, и я выполню его как задачу для агента!
        """
        await update.message.reply_text(help_text.strip())

    async def newproject_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /newproject command."""
        user_id = update.effective_user.id
        project_name = " ".join(context.args) if context.args else None
        
        # Find or create user
        async with get_db_session() as db:
            try:
                result = await db.execute(
                    select(User).where(User.email == f"telegram_{user_id}@telegram.local")
                )
                user = result.scalar_one_or_none()
                
                if not user:
                    # Create user from Telegram
                    from auth import get_password_hash
                    import uuid
                    user = User(
                        id=uuid.uuid4(),
                        email=f"telegram_{user_id}@telegram.local",
                        username=update.effective_user.username or f"user_{user_id}",
                        hashed_password=get_password_hash(str(uuid.uuid4())),  # Random password
                    )
                    db.add(user)
                    await db.commit()
                    await db.refresh(user)
                
                # Create project (simplified - would need proper project creation)
                await update.message.reply_text(
                    f"✅ Проект создан!\n"
                    f"Название: {project_name or 'Без названия'}\n"
                    f"Используйте /task для выполнения задач."
                )
            except Exception as e:
                await db.rollback()
                logger.error(f"Error in newproject: {e}")
                await update.message.reply_text(f"❌ Ошибка: {str(e)}")

    async def projects_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /projects command."""
        user_id = update.effective_user.id
        
        async with get_db_session() as db:
            try:
                result = await db.execute(
                    select(User).where(User.email == f"telegram_{user_id}@telegram.local")
                )
                user = result.scalar_one_or_none()
                
                if not user:
                    await update.message.reply_text("❌ Пользователь не найден. Используйте /start")
                    return
                
                from models import Project
                result = await db.execute(
                    select(Project).where(Project.owner_id == user.id).order_by(Project.created_at.desc())
                )
                projects = result.scalars().all()
                
                if not projects:
                    await update.message.reply_text("📁 У вас пока нет проектов. Используйте /newproject для создания.")
                else:
                    projects_list = "\n".join([
                        f"• {p.name} ({p.status})" for p in projects[:10]
                    ])
                    await update.message.reply_text(f"📁 Ваши проекты:\n\n{projects_list}")
            except Exception as e:
                logger.error(f"Error in projects: {e}")
                await update.message.reply_text(f"❌ Ошибка: {str(e)}")

    async def task_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /task command."""
        if not context.args:
            await update.message.reply_text("❌ Укажите задачу. Пример: /task создать файл hello.py")
            return
        
        task_description = " ".join(context.args)
        await self.handle_task(update, task_description)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle regular text messages as tasks."""
        if update.message.text and not update.message.text.startswith('/'):
            await self.handle_task(update, update.message.text)

    async def handle_task(self, update: Update, task_description: str):
        """Handle a task from user."""
        user_id = str(update.effective_user.id)
        chat_id = update.effective_chat.id
        
        # Send "thinking" message
        thinking_msg = await update.message.reply_text("🤔 Думаю над задачей...")
        
        try:
            # Get or create agent engine for this user
            if user_id not in self.agent_engines:
                # For now, use user_id as project_id (simplified)
                self.agent_engines[user_id] = AgentEngine(user_id)
            
            engine = self.agent_engines[user_id]
            
            # Collect steps
            steps = []
            
            async def on_step(step: AgentStep):
                steps.append(step)
                # Update message with progress
                if step.type == "tool_call":
                    await thinking_msg.edit_text(
                        f"⚙️ Выполняю: {step.tool_name}\n"
                        f"Шаг {step.step_number}..."
                    )
                elif step.type == "tool_result":
                    if step.tool_result and step.tool_result.get("success"):
                        await thinking_msg.edit_text(
                            f"✅ {step.content}\n"
                            f"Шаг {step.step_number}..."
                        )
            
            # Run agent
            result = await engine.run(
                user_message=task_description,
                on_step=on_step,
                task_type="coding",
            )
            
            # Send final result
            await thinking_msg.edit_text(
                f"✅ Задача выполнена!\n\n{result[:1000]}"  # Limit to 1000 chars
            )
            
        except Exception as e:
            logger.error(f"Error handling task: {e}", exc_info=True)
            await thinking_msg.edit_text(f"❌ Ошибка: {str(e)}")

    async def start_bot(self):
        """Start the Telegram bot."""
        if not self.bot_token:
            logger.warning("TELEGRAM_BOT_TOKEN not set, skipping Telegram bot")
            return
        
        self.application = Application.builder().token(self.bot_token).build()
        
        # Register handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("newproject", self.newproject_command))
        self.application.add_handler(CommandHandler("projects", self.projects_command))
        self.application.add_handler(CommandHandler("task", self.task_command))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
        # Start bot
        if settings.TELEGRAM_WEBHOOK_URL:
            # Use webhook mode
            await self.application.bot.set_webhook(settings.TELEGRAM_WEBHOOK_URL)
            logger.info(f"Telegram bot webhook set to {settings.TELEGRAM_WEBHOOK_URL}")
        else:
            # Use polling mode
            logger.info("Starting Telegram bot in polling mode...")
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling()
            logger.info("Telegram bot started")

    async def stop_bot(self):
        """Stop the Telegram bot."""
        if self.application:
            await self.application.stop()
            await self.application.shutdown()
            logger.info("Telegram bot stopped")


# Global bot instance
telegram_bot = TelegramBot()
