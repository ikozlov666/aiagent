"""
Coder Agent — выполняет шаги плана, пишет код.
"""
import json
from typing import Optional, Callable, Awaitable
from llm.router import llm_router
from agents.engine import AgentStep, AgentEngine
from agents.tools.definitions import TOOLS


CODER_SYSTEM_PROMPT = """You are a Coding Agent. Your job is to execute specific steps from a plan.

You have access to these tools:
- execute_command: Run bash commands
- write_file: Create/edit files
- read_file: Read existing files
- list_files: List directory contents
- browser_navigate, browser_click, browser_type, browser_screenshot: Browser automation

Execute the given step using the available tools. Be thorough and check your work."""


class CoderAgent:
    """Executes plan steps by writing code and using tools."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.engine = AgentEngine(project_id)  # Reuse engine for tool execution

    async def execute_step(
        self,
        step: dict,
        on_step: Optional[Callable[[AgentStep], Awaitable[None]]] = None,
    ) -> dict:
        """
        Execute a single step from the plan.
        
        Args:
            step: {"number": 1, "action": "...", "description": "..."}
            on_step: Callback for streaming progress
        
        Returns:
            {"success": bool, "result": str, "output": str}
        """
        step_description = f"{step.get('action', '')}: {step.get('description', '')}"
        
        step_msg = AgentStep(
            step_number=step.get("number", 0),
            type="thinking",
            content=f"Выполняю шаг {step.get('number', 0)}: {step.get('action', '')}",
        )
        if on_step:
            await on_step(step_msg)

        # Use AgentEngine to execute the step
        try:
            result = await self.engine.run(
                user_message=step_description,
                on_step=on_step,
                task_type="coding",
            )
            return {
                "success": True,
                "result": result,
                "output": result
            }
        except Exception as e:
            return {
                "success": False,
                "result": f"Error: {str(e)}",
                "output": str(e),
                "error": str(e)
            }
