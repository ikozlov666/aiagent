"""
Planner Agent — декомпозирует задачу на шаги.
"""
from typing import Optional, Callable, Awaitable
from llm.router import llm_router
from agents.engine import AgentStep


PLANNER_SYSTEM_PROMPT = """You are a Planning Agent. Your job is to break down user tasks into clear, actionable steps.

Analyze the user's request and create a detailed plan with:
1. Clear steps in logical order
2. What needs to be done at each step
3. Dependencies between steps
4. Expected outcomes

Return your plan as a structured list. Be specific and actionable."""


class PlannerAgent:
    """Plans the execution of user tasks by breaking them into steps."""

    def __init__(self, project_id: str):
        self.project_id = project_id

    async def plan(
        self,
        user_task: str,
        on_step: Optional[Callable[[AgentStep], Awaitable[None]]] = None,
    ) -> dict:
        """
        Create a plan for executing the user task.
        
        Returns:
            {
                "steps": [
                    {"number": 1, "action": "Create package.json", "description": "..."},
                    {"number": 2, "action": "Install dependencies", "description": "..."},
                ],
                "summary": "Overall plan summary"
            }
        """
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Create a detailed plan for this task:\n\n{user_task}"}
        ]

        step = AgentStep(
            step_number=0,
            type="thinking",
            content="Планирую выполнение задачи...",
        )
        if on_step:
            await on_step(step)

        try:
            response = await llm_router.chat(
                messages=messages,
                task_type="planning",
                temperature=0.7,  # More creative for planning
            )

            plan_text = response.content if hasattr(response, 'content') else str(response)
            
            # Parse plan from LLM response
            # LLM should return structured plan, but we'll extract steps
            steps = self._parse_plan(plan_text)
            
            return {
                "steps": steps,
                "summary": plan_text[:500],  # First 500 chars as summary
                "full_plan": plan_text
            }
        except Exception as e:
            return {
                "steps": [{"number": 1, "action": "Execute task", "description": user_task}],
                "summary": f"Error in planning: {str(e)}",
                "error": str(e)
            }

    def _parse_plan(self, plan_text: str) -> list:
        """Parse plan text into structured steps."""
        steps = []
        lines = plan_text.split('\n')
        current_step = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Look for numbered steps (1., 2., Step 1, etc.)
            if any(line.startswith(prefix) for prefix in ['1.', '2.', '3.', '4.', '5.', 'Step 1', 'Step 2', '-']):
                if current_step:
                    steps.append(current_step)
                
                # Extract step number and action
                number = len(steps) + 1
                action = line.lstrip('0123456789.- ').strip()
                if action.startswith('Step'):
                    action = action.split(':', 1)[-1].strip()
                
                current_step = {
                    "number": number,
                    "action": action[:100],  # Limit length
                    "description": ""
                }
            elif current_step and line:
                # Add to current step description
                if current_step["description"]:
                    current_step["description"] += " " + line
                else:
                    current_step["description"] = line
        
        if current_step:
            steps.append(current_step)
        
        # If no structured steps found, create one default step
        if not steps:
            steps = [{
                "number": 1,
                "action": "Execute task",
                "description": plan_text[:200]
            }]
        
        return steps
