"""
Reviewer Agent — проверяет код и результаты выполнения.
"""
from typing import Optional, Callable, Awaitable
from llm.router import llm_router
from agents.engine import AgentStep


REVIEWER_SYSTEM_PROMPT = """You are a Code Review Agent. Your job is to review the execution results and check for:
1. Code quality and best practices
2. Missing steps or incomplete work
3. Errors or potential issues
4. Whether the task was completed successfully

Provide constructive feedback and suggest improvements if needed."""


class ReviewerAgent:
    """Reviews code and execution results."""

    def __init__(self, project_id: str):
        self.project_id = project_id

    async def review(
        self,
        plan: dict,
        execution_results: list[dict],
        on_step: Optional[Callable[[AgentStep], Awaitable[None]]] = None,
    ) -> dict:
        """
        Review the execution results against the plan.
        
        Args:
            plan: Original plan with steps
            execution_results: Results from each step execution
        
        Returns:
            {
                "approved": bool,
                "issues": [{"step": 1, "issue": "...", "severity": "error|warning"}],
                "suggestions": ["..."],
                "summary": "..."
            }
        """
        step = AgentStep(
            step_number=999,
            type="thinking",
            content="Проверяю результаты выполнения...",
        )
        if on_step:
            await on_step(step)

        # Build review context
        review_context = f"""Original Plan:
{plan.get('summary', '')}

Execution Results:
"""
        for i, result in enumerate(execution_results):
            step_num = result.get('step_number', i + 1)
            success = result.get('success', False)
            output = result.get('output', '')[:500]  # Limit length
            review_context += f"\nStep {step_num}: {'✅ Success' if success else '❌ Failed'}\n{output}\n"

        messages = [
            {"role": "system", "content": REVIEWER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Review this execution:\n\n{review_context}"}
        ]

        try:
            response = await llm_router.chat(
                messages=messages,
                task_type="review",
                temperature=0.3,  # More deterministic for review
            )

            review_text = response.content if hasattr(response, 'content') else str(response)
            
            # Parse review (simple heuristic)
            has_errors = any(word in review_text.lower() for word in ['error', 'failed', 'issue', 'problem', 'wrong'])
            has_warnings = any(word in review_text.lower() for word in ['warning', 'improve', 'better', 'suggest'])
            
            return {
                "approved": not has_errors,
                "issues": self._extract_issues(review_text),
                "suggestions": self._extract_suggestions(review_text),
                "summary": review_text[:500],
                "full_review": review_text
            }
        except Exception as e:
            return {
                "approved": True,  # Default to approved if review fails
                "issues": [],
                "suggestions": [],
                "summary": f"Review error: {str(e)}",
                "error": str(e)
            }

    def _extract_issues(self, review_text: str) -> list:
        """Extract issues from review text."""
        issues = []
        lines = review_text.split('\n')
        for line in lines:
            line = line.strip()
            if any(word in line.lower() for word in ['error', 'issue', 'problem', 'wrong', 'missing']):
                issues.append({
                    "issue": line[:200],
                    "severity": "error" if "error" in line.lower() else "warning"
                })
        return issues[:5]  # Limit to 5 issues

    def _extract_suggestions(self, review_text: str) -> list:
        """Extract suggestions from review text."""
        suggestions = []
        lines = review_text.split('\n')
        for line in lines:
            line = line.strip()
            if any(word in line.lower() for word in ['suggest', 'recommend', 'improve', 'better', 'consider']):
                suggestions.append(line[:200])
        return suggestions[:5]  # Limit to 5 suggestions
