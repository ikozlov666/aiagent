"""
Tool Executor — runs agent tool calls inside the Docker sandbox.
"""
import json
import os
import inspect
import time
from typing import Callable, Awaitable

from docker_manager.manager import docker_manager
from agents.tools.browser_tools import BrowserTools


class ToolExecutor:
    """Executes tool calls inside a project's Docker sandbox."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.browser_tools = BrowserTools(project_id)
        # Cache method signatures for performance (inspect.signature is slow)
        self._signature_cache = {}
        for attr_name in dir(self):
            if attr_name.startswith('_tool_'):
                handler = getattr(self, attr_name)
                if callable(handler):
                    self._signature_cache[attr_name] = inspect.signature(handler)
        
        # Cache for read_file and list_files (TTL: 5 seconds)
        self._file_cache = {}  # {key: (data, timestamp)}
        self._cache_ttl = 5.0

    async def execute(self, tool_name: str, arguments: dict) -> dict:
        """Execute a tool call and return the result."""
        handler = getattr(self, f"_tool_{tool_name}", None)
        if not handler:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

        # Get function signature from cache (much faster than inspect.signature)
        handler_name = f"_tool_{tool_name}"
        sig = self._signature_cache.get(handler_name)
        if not sig:
            sig = inspect.signature(handler)
            self._signature_cache[handler_name] = sig

        required_params = [p.name for p in sig.parameters.values() if p.default == inspect.Parameter.empty]
        arguments = arguments or {}  # LLM может передать {} для инструментов без обязательных аргументов (например browser_get_content)
        missing_params = [p for p in required_params if p not in arguments]
        
        if missing_params:
            return {
                "success": False, 
                "error": f"Tool {tool_name} missing required arguments: {', '.join(missing_params)}. Provided: {list(arguments.keys())}"
            }

        try:
            # Only pass arguments that the handler expects
            filtered_args = {k: v for k, v in arguments.items() if k in sig.parameters}
            result = await handler(**filtered_args)
            return {"success": True, "result": result}
        except TypeError as e:
            # More specific error for missing arguments
            error_msg = str(e)
            if "missing" in error_msg and "required" in error_msg:
                return {
                    "success": False,
                    "error": f"Tool {tool_name} called with incorrect arguments. {error_msg}. Provided: {list(arguments.keys())}"
                }
            return {"success": False, "error": f"Tool {tool_name} error: {error_msg}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _tool_execute_command(self, command: str) -> dict:
        """Execute bash command in sandbox."""
        result = await docker_manager.exec_command(self.project_id, command)
        return {
            "exit_code": result["exit_code"],
            "stdout": result["stdout"][:5000],  # Limit output
            "stderr": result["stderr"][:2000],
        }

    async def _tool_write_file(self, filepath: str, content: str) -> dict:
        """Write file to sandbox."""
        # Normalize path
        if filepath.startswith("/workspace/"):
            filepath = filepath[len("/workspace/"):]
        full_path = f"/workspace/{filepath}"

        result = await docker_manager.write_file(self.project_id, full_path, content)
        return {
            "filepath": filepath,
            "written": result["success"],
        }

    async def _tool_write_files(self, files: list = None) -> dict:
        """Write multiple files in one call. Each item: {filepath, content}."""
        if not files or not isinstance(files, list):
            return {
                "written": [],
                "errors": ["write_files requires non-empty 'files' array. Call with files: [{filepath: 'index.html', content: '...'}, ...]."],
            }
        written = []
        errors = []
        for i, item in enumerate(files):
            if not isinstance(item, dict):
                errors.append(f"item {i}: not a dict")
                continue
            filepath = item.get("filepath")
            content = item.get("content")
            if not filepath:
                errors.append(f"item {i}: missing filepath")
                continue
            if filepath.startswith("/workspace/"):
                filepath = filepath[len("/workspace/"):]
            full_path = f"/workspace/{filepath}"
            try:
                result = await docker_manager.write_file(self.project_id, full_path, content or "")
                written.append({"filepath": filepath, "written": result["success"]})
            except Exception as e:
                errors.append(f"{filepath}: {e}")
        return {
            "written": written,
            "errors": errors if errors else None,
        }

    async def _tool_read_file(self, filepath: str) -> dict:
        """Read file from sandbox with caching."""
        if filepath.startswith("/workspace/"):
            filepath = filepath[len("/workspace/"):]
        full_path = f"/workspace/{filepath}"
        
        # Check cache
        cache_key = f"read:{filepath}"
        now = time.time()
        if cache_key in self._file_cache:
            cached_data, cached_time = self._file_cache[cache_key]
            if now - cached_time < self._cache_ttl:
                return cached_data
        
        # Read from sandbox
        content = await docker_manager.read_file(self.project_id, full_path)
        result = {
            "filepath": filepath,
            "content": content[:10000],  # Limit content size
        }
        
        # Cache result
        self._file_cache[cache_key] = (result, now)
        
        # Clean old cache entries (keep cache size reasonable)
        if len(self._file_cache) > 50:
            self._file_cache = {k: v for k, v in self._file_cache.items() if now - v[1] < self._cache_ttl}
        
        return result

    async def _tool_list_files(self, path: str = ".") -> dict:
        """List files in sandbox with caching."""
        if path == ".":
            full_path = "/workspace"
        elif path.startswith("/workspace"):
            full_path = path
        else:
            full_path = f"/workspace/{path}"
        
        # Check cache
        cache_key = f"list:{path}"
        now = time.time()
        if cache_key in self._file_cache:
            cached_data, cached_time = self._file_cache[cache_key]
            if now - cached_time < self._cache_ttl:
                return cached_data
        
        # List from sandbox
        files = await docker_manager.list_files(self.project_id, full_path)
        result = {
            "path": path,
            "files": files[:100],  # Limit results
        }
        
        # Cache result
        self._file_cache[cache_key] = (result, now)
        
        # Clean old cache entries
        if len(self._file_cache) > 50:
            self._file_cache = {k: v for k, v in self._file_cache.items() if now - v[1] < self._cache_ttl}
        
        return result

    # Browser tools
    async def _tool_browser_navigate(self, url: str) -> dict:
        """Navigate browser to URL."""
        result = await self.browser_tools.navigate(url)
        return {
            "url": result.get("url", url),
            "title": result.get("title", ""),
            "success": result.get("success", False),
        }

    async def _tool_browser_click(self, selector: str) -> dict:
        """Click element in browser."""
        result = await self.browser_tools.click(selector)
        return {
            "selector": selector,
            "success": result.get("success", False),
            "error": result.get("error"),
        }

    async def _tool_browser_type(self, selector: str, text: str) -> dict:
        """Type text into input field."""
        result = await self.browser_tools.type_text(selector, text)
        return {
            "selector": selector,
            "text": text,
            "success": result.get("success", False),
            "error": result.get("error"),
        }

    async def _tool_browser_fill_form(
        self, steps: list, submit_selector: str = ""
    ) -> dict:
        """Fill form fields and optionally submit in one call (faster for login forms)."""
        result = await self.browser_tools.fill_form(steps, submit_selector)
        return {
            "success": result.get("success", False),
            "filled": result.get("filled", 0),
            "url": result.get("url", ""),
            "error": result.get("error"),
        }

    async def _tool_browser_select(self, selector: str, value: str = "", label: str = "") -> dict:
        """Select option in dropdown."""
        result = await self.browser_tools.select_option(selector, value=value, label=label)
        return {
            "selector": selector,
            "success": result.get("success", False),
            "error": result.get("error"),
        }

    async def _tool_browser_screenshot(self, full_page: bool = False) -> dict:
        """Take screenshot of browser. Also returns page text so non-vision models can analyze."""
        result = await self.browser_tools.screenshot(full_page)
        screenshot_b64 = result.get("screenshot", "")
        screenshot_path = result.get("screenshot_path", "")
        page_text = result.get("page_text", "")
        return {
            "success": result.get("success", False),
            "has_screenshot": bool(screenshot_b64),
            "screenshot_path": screenshot_path,
            "page_text": page_text,
            "size_bytes": len(screenshot_b64) if screenshot_b64 else 0,
            "error": result.get("error"),
        }

    async def _tool_browser_get_content(self, selector: str = "") -> dict:
        """Get page or element content."""
        result = await self.browser_tools.get_content(selector)
        return {
            "selector": selector or "page",
            "content": result.get("content", ""),
            "success": result.get("success", False),
            "error": result.get("error"),
        }

    async def _tool_browser_get_page_structure(self) -> dict:
        """Get interactive elements with selectors for browser testing."""
        result = await self.browser_tools.get_page_structure()
        return {
            "elements": result.get("elements", []),
            "url": result.get("url", ""),
            "success": result.get("success", False),
            "error": result.get("error"),
        }

    async def _tool_browser_wait(self, selector: str, timeout: int = 5000) -> dict:
        """Wait for element to appear."""
        result = await self.browser_tools.wait(selector, timeout)
        return {
            "selector": selector,
            "found": result.get("success", False),
            "error": result.get("error"),
        }

    async def _tool_browser_get_console_logs(self) -> dict:
        """Get browser console logs (errors, warnings, log) for debugging. Call after opening page."""
        result = await self.browser_tools.get_console_logs()
        return {
            "success": result.get("success", False),
            "logs": result.get("logs", []),
            "url": result.get("url", ""),
            "error": result.get("error"),
        }

    async def _tool_browser_get_network_failures(self) -> dict:
        """Get failed network requests and responses with status 4xx/5xx. Listens 2s after call. Use after navigate to debug."""
        result = await self.browser_tools.get_network_failures()
        return {
            "success": result.get("success", False),
            "request_failures": result.get("request_failures", []),
            "bad_status_responses": result.get("bad_status_responses", []),
            "url": result.get("url", ""),
            "error": result.get("error"),
        }

    async def _tool_browser_execute_script(self, script: str) -> dict:
        """Run JavaScript in the page (e.g. scroll to bottom)."""
        result = await self.browser_tools.execute_script(script)
        return {
            "success": result.get("success", False),
            "result": result.get("result"),
            "url": result.get("url", ""),
            "error": result.get("error"),
        }

    async def _tool_browser_scroll(self, direction: str = "down", amount: int = 500, to_bottom: bool = False) -> dict:
        """Scroll the page down/up or to bottom."""
        result = await self.browser_tools.scroll(direction=direction, amount=amount, to_bottom=to_bottom)
        return {
            "success": result.get("success", False),
            "scrolled": result.get("scrolled"),
            "url": result.get("url", ""),
            "error": result.get("error"),
        }