
import inspect
import os


class ToolExecutor:

    async def execute(self, tool_name: str, arguments: dict):
        method_name = f"_tool_{tool_name}"

        if not hasattr(self, method_name):
            return {"error": f"Unknown tool: {tool_name}"}

        method = getattr(self, method_name)
        sig = inspect.signature(method)

        required_params = [
            p.name
            for p in sig.parameters.values()
            if p.default == inspect.Parameter.empty
        ]

        missing_params = [p for p in required_params if p not in arguments]

        if missing_params:
            return {
                "error": f"Tool '{tool_name}' missing required arguments: {missing_params}",
                "provided_arguments": arguments,
                "hint": "Model must provide all required parameters."
            }

        return await method(**arguments)

    async def _tool_write_files(self, files: list):
        if not files:
            return {
                "error": "files array is empty",
                "hint": "files must contain at least one file with filepath and content"
            }

        for f in files:
            if "filepath" not in f or "content" not in f:
                return {
                    "error": "Invalid file object structure",
                    "required": ["filepath", "content"],
                    "received": f
                }

            filepath = f["filepath"]
            content = f["content"]

            os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

            with open(filepath, "w", encoding="utf-8") as file:
                file.write(content)

        return {"status": "files_written", "count": len(files)}
