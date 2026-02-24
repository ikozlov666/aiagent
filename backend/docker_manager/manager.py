"""
Docker Manager — creates and manages isolated sandbox containers per project.
"""
import os
import time
import asyncio
from typing import Optional
import docker
from docker.models.containers import Container
from config import settings


MAX_SANDBOXES_PER_USER = 3  # Max concurrent sandboxes
SANDBOX_TTL_SECONDS = 3600  # 1 hour idle TTL


class DockerManager:
    """Manages Docker sandbox containers for projects."""

    def __init__(self):
        self.client = docker.from_env()
        self._containers: dict[str, Container] = {}
        self._last_activity: dict[str, float] = {}  # project_id -> last activity timestamp
        self._container_status_cache: dict[str, tuple[float, str]] = {}
        self._container_reload_ttl_seconds = 1.0
        self._running_servers_cache: dict[str, tuple[float, dict]] = {}
        self._running_servers_ttl_seconds = 1.5
        os.makedirs(settings.PROJECTS_DIR, exist_ok=True)

    def _container_name(self, project_id: str) -> str:
        return f"sandbox-{project_id}"

    def get_container(self, project_id: str) -> Optional[Container]:
        """Get existing container by project ID."""
        name = self._container_name(project_id)

        # Check cache
        if project_id in self._containers:
            try:
                c = self._containers[project_id]
                now = time.time()
                cached_state = self._container_status_cache.get(project_id)
                should_reload = (
                    not cached_state
                    or (now - cached_state[0]) > self._container_reload_ttl_seconds
                )
                if should_reload:
                    c.reload()
                    self._container_status_cache[project_id] = (now, c.status)
                status = self._container_status_cache.get(project_id, (0, "unknown"))[1]
                if status == "running":
                    return c
            except Exception:
                del self._containers[project_id]
                self._container_status_cache.pop(project_id, None)

        # Search by name
        try:
            c = self.client.containers.get(name)
            if c.status == "running":
                self._containers[project_id] = c
                return c
        except docker.errors.NotFound:
            pass

        return None

    def touch_activity(self, project_id: str):
        """Update last activity timestamp for a project."""
        self._last_activity[project_id] = time.time()

    async def create_sandbox(self, project_id: str) -> Container:
        """Create a new isolated sandbox container for a project."""
        # Check if already exists
        existing = self.get_container(project_id)
        if existing:
            self.touch_activity(project_id)
            return existing

        # Auto-cleanup old containers before creating new ones
        await self.cleanup_idle_sandboxes()

        # Create project directory on host
        project_dir = os.path.abspath(os.path.join(settings.PROJECTS_DIR, project_id))
        os.makedirs(project_dir, exist_ok=True)

        name = self._container_name(project_id)

        # Remove old stopped container if exists
        try:
            old = self.client.containers.get(name)
            old.remove(force=True)
        except docker.errors.NotFound:
            pass

        # Create container
        container = self.client.containers.run(
            image=settings.SANDBOX_IMAGE,
            name=name,
            detach=True,
            mem_limit=settings.SANDBOX_MEM_LIMIT,
            cpu_period=settings.SANDBOX_CPU_PERIOD,
            cpu_quota=settings.SANDBOX_CPU_QUOTA,
            ports={
                "6080/tcp": None,  # noVNC
                "3000/tcp": None,  # Express / serve / http-server
                "5173/tcp": None,  # Vite dev server
                "8080/tcp": None,  # Alternative dev server
                "5000/tcp": None,  # Flask / Python
                "4000/tcp": None,  # Alternative
                "8000/tcp": None,  # FastAPI / Django
            },
            volumes={
                project_dir: {
                    "bind": "/workspace",
                    "mode": "rw",
                }
            },
            environment={
                "PROJECT_ID": project_id,
            },
            restart_policy={"Name": "no"},  # Don't auto-restart
        )

        self._containers[project_id] = container
        self.touch_activity(project_id)
        print(f"🐳 Created sandbox: {name}")

        # Wait for container to be ready
        await asyncio.sleep(2)
        container.reload()

        return container

    def get_logs(self, project_id: str, tail: int = 200) -> str:
        """Get container stdout/stderr logs (last N lines). For debugging sandbox and dev server."""
        container = self.get_container(project_id)
        if not container:
            return ""
        try:
            out = container.logs(stdout=True, stderr=True, tail=tail)
            return out.decode("utf-8", errors="replace")
        except Exception as e:
            return f"[Error reading logs: {e}]"

    def get_ports(self, project_id: str) -> dict:
        """Get mapped ports for a container."""
        container = self.get_container(project_id)
        if not container:
            return {}

        container.reload()
        self._container_status_cache[project_id] = (time.time(), container.status)
        ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})

        result = {}
        for container_port, host_bindings in ports.items():
            if host_bindings:
                port_num = container_port.split("/")[0]
                result[port_num] = host_bindings[0]["HostPort"]

        return result

    async def check_dev_server(self, project_id: str, port: int = 3000) -> bool:
        """Check if dev server is running on specified port."""
        container = self.get_container(project_id)
        if not container:
            return False
        
        try:
            # Check if port is listening
            result = await self.exec_command(
                project_id,
                f"timeout 1 bash -c '</dev/tcp/localhost/{port}' 2>/dev/null && echo 'open' || echo 'closed'",
                timeout=2
            )
            return "open" in result.get("stdout", "").lower()
        except:
            return False

    def _should_invalidate_running_servers_cache(self, command: str) -> bool:
        """Invalidate server cache only for commands likely to affect listening ports."""
        normalized = " ".join((command or "").lower().split())
        if not normalized:
            return False

        starts_with_tokens = (
            "npm run", "npm start", "yarn dev", "yarn start", "pnpm dev", "pnpm start",
            "vite", "next dev", "uvicorn", "gunicorn", "flask run", "django-admin runserver",
            "python -m http.server", "serve", "http-server", "supervisorctl", "systemctl",
            "pkill", "killall", "kill ",
        )
        if normalized.startswith(starts_with_tokens):
            return True

        # common inline cases, e.g. "cd app && npm run dev &"
        inline_tokens = (
            "&& npm run", "&& yarn", "&& pnpm", "&& vite", "&& uvicorn", "&& gunicorn",
            "&& flask run", "&& python -m http.server", "&& kill ", "&& pkill", "&& killall",
        )
        return any(token in normalized for token in inline_tokens)

    async def find_running_servers(self, project_id: str) -> dict:
        """Find running dev servers in the container."""
        start_ts = time.perf_counter()
        now = time.time()
        cached = self._running_servers_cache.get(project_id)
        if cached and (now - cached[0]) < self._running_servers_ttl_seconds:
            print(f"[DockerManager] find_running_servers cache hit project={project_id} age={(now - cached[0]):.2f}s")
            return dict(cached[1])

        container = self.get_container(project_id)
        if not container:
            return {}

        common_ports = [3000, 5173, 8080, 5000, 8000, 4000]
        ports = self.get_ports(project_id)
        running_servers: dict[str, str | None] = {}

        check_ports = " ".join(str(port) for port in common_ports)
        probes = [
            (
                "ss",
                "for p in " + check_ports + "; do "
                "if command -v ss >/dev/null 2>&1 && ss -ltn | awk '{print $4}' | grep -E '(^|:)'\"$p\"'$' >/dev/null; then echo $p; fi; done",
            ),
            (
                "netstat",
                "for p in " + check_ports + "; do "
                "if command -v netstat >/dev/null 2>&1 && netstat -ltn | awk '{print $4}' | grep -E '(^|:)'\"$p\"'$' >/dev/null; then echo $p; fi; done",
            ),
        ]

        used_probe = "none"
        for probe_name, probe_cmd in probes:
            result = await self.exec_command(project_id, probe_cmd, timeout=5)
            if not result.get("success"):
                print(f"[DockerManager] probe={probe_name} project={project_id} failed stderr={result.get('stderr', '')[:200]}")
                continue

            used_probe = probe_name
            for line in (result.get("stdout") or "").splitlines():
                port = line.strip()
                if port:
                    running_servers[port] = ports.get(port)
            break

        self._running_servers_cache[project_id] = (now, running_servers)
        elapsed_ms = (time.perf_counter() - start_ts) * 1000
        print(f"[DockerManager] find_running_servers project={project_id} probe={used_probe} result={running_servers} took={elapsed_ms:.1f}ms")
        return running_servers

    async def exec_command(
        self,
        project_id: str,
        command: str,
        workdir: str = "/workspace",
        timeout: int = 30,
    ) -> dict:
        """Execute a command inside the sandbox container.

        The docker SDK's exec_run is synchronous and blocks the event loop,
        so we offload it to a thread and enforce a real asyncio timeout.
        """
        self.touch_activity(project_id)
        if self._should_invalidate_running_servers_cache(command):
            self._running_servers_cache.pop(project_id, None)
            print(f"[DockerManager] invalidated running_servers cache for project={project_id} command={command[:80]}")
        container = self.get_container(project_id)
        if not container:
            container = await self.create_sandbox(project_id)

        def _blocking_exec():
            return container.exec_run(
                cmd=["bash", "-c", command],
                workdir=workdir,
                demux=True,
            )

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_blocking_exec),
                timeout=timeout,
            )

            stdout = result.output[0].decode("utf-8", errors="replace") if result.output[0] else ""
            stderr = result.output[1].decode("utf-8", errors="replace") if result.output[1] else ""

            return {
                "exit_code": result.exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "success": result.exit_code == 0,
            }
        except asyncio.TimeoutError:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s: {command[:120]}",
                "success": False,
            }
        except Exception as e:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "success": False,
            }

    def _shell_escape_path(self, path: str) -> str:
        """Escape path for safe use in single-quoted bash: ' -> '\\''."""
        return path.replace("'", "'\\''")

    async def read_file(self, project_id: str, filepath: str) -> str:
        """Read a file from the sandbox."""
        safe_path = self._shell_escape_path(filepath)
        result = await self.exec_command(project_id, f"cat '{safe_path}'")
        if result["success"]:
            return result["stdout"] or ""
        raise FileNotFoundError(f"File not found: {filepath} — {result['stderr']}")

    async def read_file_base64(self, project_id: str, filepath: str) -> str:
        """Read file as base64 (supports binary files)."""
        safe_path = self._shell_escape_path(filepath)
        result = await self.exec_command(
            project_id,
            f"if [ -f '{safe_path}' ]; then base64 -w0 '{safe_path}'; else exit 44; fi"
        )
        if result.get("success"):
            return (result.get("stdout") or "").strip()
        if result.get("exit_code") == 44:
            raise FileNotFoundError(f"File not found: {filepath}")
        raise RuntimeError(result.get("stderr") or f"Failed to read file: {filepath}")

    async def write_file(self, project_id: str, filepath: str, content: str) -> dict:
        """Write a file to the sandbox."""
        safe_path = self._shell_escape_path(filepath)
        dirname = os.path.dirname(filepath)
        if dirname:
            await self.exec_command(project_id, f"mkdir -p '{self._shell_escape_path(dirname)}'")
        result = await self.exec_command(
            project_id,
            f"cat > '{safe_path}' << 'ENDOFFILE'\n{content}\nENDOFFILE"
        )
        return result

    async def list_files(self, project_id: str, path: str = "/workspace") -> list[dict]:
        """List files in the sandbox."""
        safe_path = self._shell_escape_path(path)
        result = await self.exec_command(
            project_id,
            f"find '{safe_path}' -maxdepth 3 -not -path '*/node_modules/*' -not -path '*/.git/*' "
            f"| head -200 | while read f; do "
            f"if [ -d \"$f\" ]; then echo \"dir:$f\"; else echo \"file:$f\"; fi; done"
        )

        if not result["success"]:
            return []

        files = []
        for line in result["stdout"].strip().split("\n"):
            if not line:
                continue
            ftype, fpath = line.split(":", 1) if ":" in line else ("file", line)
            files.append({
                "type": ftype,
                "path": fpath,
                "name": os.path.basename(fpath),
            })
        return files

    async def list_files_tree(self, project_id: str, path: str = "/workspace") -> dict:
        """List files in tree structure."""
        files = await self.list_files(project_id, path)
        
        # Build tree structure
        tree = {"type": "dir", "name": "workspace", "path": "/workspace", "children": []}
        path_map = {"/workspace": tree}
        
        for item in files:
            item_path = item["path"]
            if item_path == "/workspace":
                continue
                
            # Get relative path
            if item_path.startswith("/workspace/"):
                rel_path = item_path[len("/workspace/"):]
            else:
                rel_path = item_path
                
            parts = rel_path.split("/")
            current_path = "/workspace"
            current_node = tree
            
            # Build path to parent
            for i, part in enumerate(parts[:-1]):
                current_path = f"{current_path}/{part}"
                if current_path not in path_map:
                    dir_node = {
                        "type": "dir",
                        "name": part,
                        "path": current_path,
                        "children": []
                    }
                    path_map[current_path] = dir_node
                    current_node["children"].append(dir_node)
                current_node = path_map[current_path]
            
            # Add file/dir
            if item["type"] == "dir" and item_path not in path_map:
                dir_node = {
                    "type": "dir",
                    "name": item["name"],
                    "path": item_path,
                    "children": []
                }
                path_map[item_path] = dir_node
                current_node["children"].append(dir_node)
            elif item["type"] == "file":
                file_node = {
                    "type": "file",
                    "name": item["name"],
                    "path": item_path,
                }
                current_node["children"].append(file_node)
        
        # Sort children: dirs first, then files
        def sort_children(node):
            if "children" in node:
                node["children"].sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
                for child in node["children"]:
                    sort_children(child)
        
        sort_children(tree)
        return tree

    async def destroy_sandbox(self, project_id: str):
        """Stop and remove a sandbox container."""
        container = self.get_container(project_id)
        if container:
            container.remove(force=True)
            self._containers.pop(project_id, None)
            print(f"🗑️  Destroyed sandbox: {self._container_name(project_id)}")

    def list_sandboxes(self) -> list[dict]:
        """List all running sandboxes."""
        containers = self.client.containers.list(
            filters={"name": "sandbox-"}
        )
        return [
            {
                "project_id": c.name.replace("sandbox-", ""),
                "status": c.status,
                "name": c.name,
                "idle_seconds": int(time.time() - self._last_activity.get(
                    c.name.replace("sandbox-", ""), 0
                )),
            }
            for c in containers
        ]

    async def cleanup_idle_sandboxes(self):
        """Remove sandbox containers that have been idle too long."""
        now = time.time()
        sandboxes = self.list_sandboxes()
        removed = 0
        
        for sb in sandboxes:
            pid = sb["project_id"]
            last = self._last_activity.get(pid, 0)
            
            # If no activity recorded or idle too long
            if last == 0 or (now - last) > SANDBOX_TTL_SECONDS:
                try:
                    await self.destroy_sandbox(pid)
                    removed += 1
                except Exception as e:
                    print(f"⚠️ Failed to cleanup sandbox {pid}: {e}")
        
        if removed > 0:
            print(f"🧹 Cleaned up {removed} idle sandbox(es)")
        return removed

    async def destroy_all_sandboxes(self):
        """Stop and remove ALL sandbox containers."""
        sandboxes = self.list_sandboxes()
        for sb in sandboxes:
            try:
                await self.destroy_sandbox(sb["project_id"])
            except Exception as e:
                print(f"⚠️ Failed to destroy sandbox {sb['project_id']}: {e}")
        print(f"🧹 Destroyed all {len(sandboxes)} sandbox(es)")


# Singleton
docker_manager = DockerManager()
