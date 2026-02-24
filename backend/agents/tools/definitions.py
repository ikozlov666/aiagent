"""
Agent Tools — functions the AI agent can call to interact with the sandbox.
Defined in OpenAI function-calling format (works with all providers).
"""

# ============================================
# Tool definitions (JSON schema for LLM)
# ============================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "Execute a bash command in the project sandbox. Use for: installing packages (npm install, pip install), running scripts, building projects, starting dev servers, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute, e.g. 'npm init -y' or 'python3 app.py'"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file in the project. Use for writing code, configs, HTML, CSS, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path relative to /workspace, e.g. 'src/index.js' or 'index.html'"
                    },
                    "content": {
                        "type": "string",
                        "description": "The full file content to write"
                    }
                },
                "required": ["filepath", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_files",
            "description": "Create or overwrite multiple SMALL files in one call (configs, short scripts). For files with LARGE content (HTML pages, CSS, JS), use write_file instead — one file per call. REQUIRED: pass 'files' array. Each object MUST have 'filepath' and 'content'. Example: {\"files\": [{\"filepath\": \"config.json\", \"content\": \"{...}\"}, {\"filepath\": \"README.md\", \"content\": \"# Hello\"}]}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "description": "REQUIRED. List of {filepath, content}. filepath relative to /workspace, e.g. index.html, src/style.css.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "filepath": {"type": "string"},
                                "content": {"type": "string"}
                            },
                            "required": ["filepath", "content"]
                        }
                    }
                },
                "required": ["files"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file in the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path relative to /workspace, e.g. 'src/index.js'"
                    }
                },
                "required": ["filepath"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories in the project workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to /workspace, e.g. '.' or 'src'. Default is root.",
                        "default": "."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "Navigate to a URL in the browser. Opens the page in Chromium.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to navigate to, e.g. 'http://localhost:3000' or 'https://example.com'"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click on an element in the browser. Use CSS selector or text content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector (e.g. 'button.submit') or text content to click"
                    }
                },
                "required": ["selector"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "Type text into an input field or textarea.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the input field, e.g. 'input[name=\"email\"]'"
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to type into the field"
                    }
                },
                "required": ["selector", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_fill_form",
            "description": "Fill multiple inputs and optionally submit in ONE call. Faster than several browser_type + browser_click. Use after browser_get_page_structure. steps: list of {selector, value} or {selector, value/label, type: 'select'}; submit_selector: optional button to click after filling.",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "description": "List of {selector, value} for inputs, or {selector, value or label, type: 'select'} for dropdowns",
                        "items": {
                            "type": "object",
                            "properties": {
                                "selector": {"type": "string"},
                                "value": {"type": "string"},
                                "label": {"type": "string"},
                                "type": {"type": "string", "description": "Use 'select' for <select> elements"}
                            },
                            "required": ["selector"]
                        }
                    },
                    "submit_selector": {
                        "type": "string",
                        "description": "Optional CSS selector of submit button to click after filling",
                        "default": ""
                    }
                },
                "required": ["steps"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_select",
            "description": "Select option(s) in a <select> dropdown. Use value (attribute) or label (visible text).",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the select element, e.g. 'select[name=\"country\"]'"
                    },
                    "value": {
                        "type": "string",
                        "description": "Option value attribute (use either value or label)"
                    },
                    "label": {
                        "type": "string",
                        "description": "Option visible text (use either value or label)"
                    }
                },
                "required": ["selector"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "Take a screenshot of the current page. Returns base64 encoded image.",
            "parameters": {
                "type": "object",
                "properties": {
                    "full_page": {
                        "type": "boolean",
                        "description": "If true, capture full page. If false, capture viewport only.",
                        "default": False
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_get_content",
            "description": "Get the text content of the current page or a specific element.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "Optional CSS selector. If not provided, returns page text.",
                        "default": ""
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_get_page_structure",
            "description": "Get a map of interactive elements (inputs, buttons, links) with exact selectors. Call this FIRST when testing a page so you know which selector to use for browser_type and browser_click — avoids guessing and speeds up testing.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_wait",
            "description": "Wait for an element to appear or for a timeout.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector to wait for"
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in milliseconds (default: 5000)",
                        "default": 5000
                    }
                },
                "required": ["selector"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_get_console_logs",
            "description": "Get browser console logs (JS errors, console.log, warnings). Use after opening a page to debug why it fails. Returns level, text, url for each entry.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_get_network_failures",
            "description": "Get failed network requests and responses with status 4xx/5xx. Listens for 2 seconds after call. Use after browser_navigate to find broken requests when testing a page.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_execute_script",
            "description": "Run JavaScript in the current page. Use for scrolling (e.g. window.scrollTo(0, document.body.scrollHeight) to scroll to bottom), or any other page script.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "JavaScript code to execute in the page, e.g. 'window.scrollTo(0, document.body.scrollHeight)'"
                    }
                },
                "required": ["script"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_scroll",
            "description": "Scroll the page down or up by a number of pixels, or to the very bottom. Use after browser_navigate when user asks to scroll the page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "description": "Scroll direction: 'down' or 'up'",
                        "enum": ["down", "up"]
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Pixels to scroll (default 500)",
                        "default": 500
                    },
                    "to_bottom": {
                        "type": "boolean",
                        "description": "If true, scroll to the very bottom of the page (ignores direction/amount)",
                        "default": False
                    }
                },
                "required": []
            }
        }
    },
]

TOOL_NAMES = [t["function"]["name"] for t in TOOLS]
