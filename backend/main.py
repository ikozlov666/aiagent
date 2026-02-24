"""
AI Agent Platform — FastAPI Backend
WebSocket chat + REST API for project management.
"""
import json
import uuid
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response
from pydantic import BaseModel
import os
import base64
import mimetypes

from config import settings
from docker_manager.manager import docker_manager
from agents.engine import AgentEngine, AgentStep
from llm.router import llm_router
from database import init_db, get_db
from models import User, Project
from auth import verify_password, get_password_hash, create_access_token, decode_access_token
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from telegram_bot import telegram_bot

PORTS_UPDATE_DEBOUNCE_SECONDS = 1.0
STREAM_FLUSH_INTERVAL_SECONDS = 0.08

# ============================================
# App lifecycle
# ============================================
async def periodic_cleanup():
    """Periodically clean up idle sandbox containers."""
    while True:
        await asyncio.sleep(300)  # Every 5 minutes
        try:
            removed = await docker_manager.cleanup_idle_sandboxes()
        except Exception as e:
            print(f"⚠️ Cleanup error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"🚀 {settings.APP_NAME} starting...")
    print(f"   LLM providers: {[n for n, p in llm_router.providers.items() if p.available]}")
    # Initialize database
    await init_db()
    print("   ✅ Database initialized")
    
    # Start Telegram bot
    try:
        await telegram_bot.start_bot()
        print("   ✅ Telegram bot started")
    except Exception as e:
        print(f"   ⚠️ Telegram bot failed to start: {e}")
    
    # Start periodic cleanup task
    cleanup_task = asyncio.create_task(periodic_cleanup())
    print("   ✅ Periodic sandbox cleanup started (every 5 min)")
    
    yield
    
    # Stop cleanup
    cleanup_task.cancel()
    
    # Stop Telegram bot
    try:
        await telegram_bot.stop_bot()
    except Exception as e:
        print(f"   ⚠️ Error stopping Telegram bot: {e}")
    
    print("👋 Shutting down...")


app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
)

# CORS (allow frontend dev server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================
# WebSocket connection manager
# ============================================
class ConnectionManager:
    """Manages WebSocket connections per project."""

    def __init__(self):
        self.connections: dict[str, list[WebSocket]] = {}

    async def connect(self, project_id: str, websocket: WebSocket):
        await websocket.accept()
        if project_id not in self.connections:
            self.connections[project_id] = []
        self.connections[project_id].append(websocket)

    def disconnect(self, project_id: str, websocket: WebSocket):
        if project_id in self.connections:
            try:
                self.connections[project_id].remove(websocket)
            except ValueError:
                pass  # Already removed
            if not self.connections[project_id]:
                del self.connections[project_id]

    async def broadcast(self, project_id: str, data: dict):
        if project_id not in self.connections:
            return

        message = json.dumps(data, ensure_ascii=False)
        sockets = self.connections[project_id][:]
        if not sockets:
            return

        results = await asyncio.gather(
            *(ws.send_text(message) for ws in sockets),
            return_exceptions=True,
        )

        for ws, result in zip(sockets, results):
            if isinstance(result, Exception):
                try:
                    self.connections[project_id].remove(ws)
                except ValueError:
                    pass


ws_manager = ConnectionManager()

# Store active agent engines per project
agent_engines: dict[str, AgentEngine] = {}

# Security
security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Get current authenticated user from JWT token."""
    token = credentials.credentials
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )
    try:
        from uuid import UUID
        uid = UUID(user_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user id in token",
        )
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    
    return user


# ============================================
# REST API — Projects
# ============================================
class CreateProjectRequest(BaseModel):
    name: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    project_id: str
    images: Optional[List[str]] = None  # Base64 encoded images


class WriteFileRequest(BaseModel):
    filepath: str
    content: str


class UserRegister(BaseModel):
    email: str
    username: str
    password: str


class UserLogin(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str


# Security: File size limits
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_EDITOR_FILE_SIZE = 1024 * 1024  # 1MB for editor


def validate_filepath(filepath: str) -> str:
    """Validate and sanitize filepath to prevent path traversal."""
    if not filepath:
        raise HTTPException(400, "filepath cannot be empty")
    
    # Remove any leading slashes and normalize
    normalized = os.path.normpath(filepath).lstrip('/')
    
    # Check for path traversal attempts
    if '..' in normalized or normalized.startswith('/'):
        raise HTTPException(400, "Invalid filepath: path traversal detected")
    
    # Ensure it's within workspace
    full_path = os.path.join("/workspace", normalized)
    if not full_path.startswith("/workspace/"):
        raise HTTPException(400, "Invalid filepath: outside workspace")
    
    return normalized


@app.post("/api/projects")
async def create_project(
    req: CreateProjectRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new project with its own Docker sandbox."""
    try:
        project_uuid = uuid.uuid4()
        project_id = str(project_uuid)[:8]
        name = req.name or f"project-{project_id}"

        # Create project in database first
        db_project = Project(
            id=project_uuid,
            name=name,
            owner_id=current_user.id,
            status="creating",
        )
        db.add(db_project)
        await db.commit()
        await db.refresh(db_project)

        # Create sandbox container
        container = await docker_manager.create_sandbox(project_id)
        ports = docker_manager.get_ports(project_id)
        
        # Update project status
        db_project.status = "ready"
        db_project.container_id = container.id if container else None
        await db.commit()

        return {
            "project_id": project_id,
            "name": name,
            "status": "running",
            "ports": ports,
            "novnc_port": ports.get("6080"),
            "preview_port": ports.get("3000"),
        }
    except Exception as e:
        import traceback
        error_detail = str(e)
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create project: {error_detail}"
        )


@app.get("/api/projects")
async def list_projects(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all active projects."""
    sandboxes = docker_manager.list_sandboxes()
    result = []
    for s in sandboxes:
        ports = docker_manager.get_ports(s["project_id"])
        result.append({
            **s,
            "ports": ports,
            "novnc_port": ports.get("6080"),
            "preview_port": ports.get("3000"),
        })
    return {"projects": result}


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    """Delete a project and its sandbox."""
    await docker_manager.destroy_sandbox(project_id)
    agent_engines.pop(project_id, None)
    return {"status": "deleted", "project_id": project_id}


@app.get("/api/projects/{project_id}/files")
async def list_files(project_id: str, path: str = ".", tree: bool = False):
    """List files in a project."""
    container = docker_manager.get_container(project_id)
    if not container:
        raise HTTPException(404, "Project not found")

    # Validate path parameter
    if path != ".":
        validated_path = validate_filepath(path)
        full_path = f"/workspace/{validated_path}"
    else:
        full_path = "/workspace"

    if tree:
        # Таймаут, чтобы запрос не висел при занятом сандбоксе
        try:
            tree_data = await asyncio.wait_for(
                docker_manager.list_files_tree(project_id, full_path),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            raise HTTPException(504, "Сандбокс не ответил вовремя. Попробуйте обновить список файлов.")
        return {"tree": tree_data}
    else:
        # Return flat list (backward compatibility)
        files = await docker_manager.list_files(project_id, full_path)
        return {"files": files}


@app.get("/api/projects/{project_id}/files/read")
async def read_file(project_id: str, filepath: str):
    """Read a file from the project."""
    try:
        container = docker_manager.get_container(project_id)
    except Exception as e:
        print(f"⚠️ [files/read] get_container failed: {e}")
        raise HTTPException(503, "Сервис сандбокса временно недоступен")
    if not container:
        raise HTTPException(404, "Project not found")

    validated_path = validate_filepath(filepath)

    try:
        content = await docker_manager.read_file(project_id, f"/workspace/{validated_path}")
        if content is None:
            content = ""
        try:
            content_size = len(content.encode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            raise HTTPException(415, "Файл не в кодировке UTF-8, просмотр в редакторе недоступен")
        if content_size > MAX_EDITOR_FILE_SIZE:
            raise HTTPException(413, f"File too large for editor ({content_size / 1024 / 1024:.1f}MB). Max: {MAX_EDITOR_FILE_SIZE / 1024 / 1024}MB")
        return {"filepath": validated_path, "content": content}
    except FileNotFoundError:
        raise HTTPException(404, f"File not found: {filepath}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"⚠️ [files/read] Error: {e}")
        raise HTTPException(500, f"Ошибка чтения файла: {str(e)}")




@app.get("/api/projects/{project_id}/files/raw")
async def read_file_raw(project_id: str, filepath: str):
    """Read a file as raw bytes for previews (images/pdf/audio/video/binary)."""
    container = docker_manager.get_container(project_id)
    if not container:
        raise HTTPException(404, "Project not found")

    validated_path = validate_filepath(filepath)

    try:
        b64 = await docker_manager.read_file_base64(project_id, f"/workspace/{validated_path}")
        data = base64.b64decode(b64, validate=False)
        if len(data) > MAX_FILE_SIZE:
            raise HTTPException(413, f"File too large ({len(data) / 1024 / 1024:.1f}MB). Max: {MAX_FILE_SIZE / 1024 / 1024}MB")

        media_type, _ = mimetypes.guess_type(validated_path)
        headers = {"Content-Disposition": f'inline; filename="{validated_path.split("/")[-1]}"'}
        return Response(content=data, media_type=media_type or "application/octet-stream", headers=headers)
    except FileNotFoundError:
        raise HTTPException(404, f"File not found: {filepath}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Ошибка чтения бинарного файла: {str(e)}")
@app.post("/api/projects/{project_id}/files/write")
async def write_file(project_id: str, req: WriteFileRequest):
    """Write a file to the project."""
    container = docker_manager.get_container(project_id)
    if not container:
        raise HTTPException(404, "Project not found")

    # Validate filepath (prevent path traversal)
    validated_path = validate_filepath(req.filepath)
    
    # Validate file size
    content_size = len(req.content.encode('utf-8'))
    if content_size > MAX_FILE_SIZE:
        raise HTTPException(413, f"File too large ({content_size / 1024 / 1024:.1f}MB). Max: {MAX_FILE_SIZE / 1024 / 1024}MB")

    try:
        result = await docker_manager.write_file(project_id, f"/workspace/{validated_path}", req.content)
        if result.get("success"):
            return {"filepath": validated_path, "status": "saved"}
        else:
            raise HTTPException(500, f"Failed to write file: {result.get('error', 'unknown')}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error writing file: {str(e)}")


@app.get("/api/projects/{project_id}/ports")
async def get_ports(project_id: str):
    """Get mapped ports for a project's sandbox."""
    container = docker_manager.get_container(project_id)
    if not container:
        raise HTTPException(404, "Project not found")
    
    ports = docker_manager.get_ports(project_id)
    # Also check for running dev servers
    running_servers = await docker_manager.find_running_servers(project_id)
    ports.update(running_servers)
    
    return {"ports": ports}


@app.get("/api/projects/{project_id}/logs")
async def get_project_logs(project_id: str, tail: int = 200):
    """Get sandbox container logs (stdout/stderr, last N lines). For debugging."""
    container = docker_manager.get_container(project_id)
    if not container:
        raise HTTPException(404, "Project not found")
    logs = docker_manager.get_logs(project_id, tail=min(max(1, tail), 2000))
    return {"logs": logs, "tail": tail}


@app.get("/api/llm/status")
async def llm_status():
    """Get LLM providers status and cost summary."""
    providers = {}
    for name, provider in llm_router.providers.items():
        providers[name] = {
            "available": provider.available,
            "model": provider.model,
        }
    return {
        "providers": providers,
        "default": settings.DEFAULT_LLM_PROVIDER,
        "costs": llm_router.cost_tracker.get_summary(),
    }


# ============================================
# Sandbox Management
# ============================================
@app.get("/api/sandboxes")
async def list_sandboxes():
    """List all running sandboxes."""
    return {"sandboxes": docker_manager.list_sandboxes()}


@app.delete("/api/sandboxes/{project_id}")
async def destroy_sandbox(project_id: str):
    """Destroy a specific sandbox."""
    await docker_manager.destroy_sandbox(project_id)
    # Also clean up agent engine
    agent_engines.pop(project_id, None)
    return {"status": "destroyed", "project_id": project_id}


@app.post("/api/sandboxes/cleanup")
async def cleanup_sandboxes():
    """Clean up idle sandboxes."""
    removed = await docker_manager.cleanup_idle_sandboxes()
    return {"removed": removed, "remaining": len(docker_manager.list_sandboxes())}


@app.delete("/api/sandboxes")
async def destroy_all_sandboxes():
    """Destroy ALL sandboxes."""
    await docker_manager.destroy_all_sandboxes()
    agent_engines.clear()
    return {"status": "all_destroyed"}


# ============================================
# WebSocket — Terminal
# ============================================
@app.websocket("/ws/terminal/{project_id}")
async def websocket_terminal(websocket: WebSocket, project_id: str):
    """
    WebSocket endpoint for interactive terminal in the sandbox.
    Executes commands and streams output in real-time.
    """
    await websocket.accept()
    
    # Ensure sandbox exists
    container = docker_manager.get_container(project_id)
    if not container:
        try:
            container = await docker_manager.create_sandbox(project_id)
        except Exception as e:
            await websocket.send_text(json.dumps({
                "type": "error",
                "data": f"Failed to create sandbox: {e}\r\n"
            }))
            await websocket.close()
            return
    
    # Send welcome message
    await websocket.send_text(json.dumps({
        "type": "output",
        "data": "\r\n✅ Terminal connected. Commands execute in /workspace\r\n$ "
    }))
    
    try:
        while True:
            # Receive command from client
            data = await websocket.receive_text()
            message = json.loads(data)
            command = message.get("command", "").strip()
            
            if not command:
                continue
            
            if command.lower() in ["exit", "quit"]:
                await websocket.send_text(json.dumps({
                    "type": "output",
                    "data": "\r\n👋 Terminal disconnected\r\n"
                }))
                break
            
            # Execute command in container
            try:
                result = await docker_manager.exec_command(
                    project_id,
                    command,
                    workdir="/workspace",
                    timeout=60
                )
                
                # Send output
                if result["stdout"]:
                    await websocket.send_text(json.dumps({
                        "type": "output",
                        "data": result["stdout"]
                    }))
                
                if result["stderr"]:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "data": result["stderr"]
                    }))
                
                # Send prompt
                exit_code = result.get("exit_code", 0)
                prompt = f"\r\n$ " if exit_code == 0 else f"\r\n⚠️  Exit code: {exit_code}\r\n$ "
                await websocket.send_text(json.dumps({
                    "type": "output",
                    "data": prompt
                }))
                
            except Exception as e:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "data": f"\r\n❌ Error: {str(e)}\r\n$ "
                }))
    
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Terminal WebSocket error: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass  # Already closed


# ============================================
# WebSocket — Chat with Agent
# ============================================
@app.websocket("/ws/chat/{project_id}")
async def websocket_chat(websocket: WebSocket, project_id: str):
    """
    WebSocket endpoint for chatting with the AI agent.
    The agent works inside the project's Docker sandbox.
    """
    await ws_manager.connect(project_id, websocket)

    # Ensure sandbox exists
    container = docker_manager.get_container(project_id)
    if not container:
        try:
            await docker_manager.create_sandbox(project_id)
        except Exception as e:
            await websocket.send_text(json.dumps({
                "type": "error",
                "content": f"Failed to create sandbox: {e}"
            }))
            return

    # Get or create agent engine for this project
    if project_id not in agent_engines:
        agent_engines[project_id] = AgentEngine(project_id)
    engine = agent_engines[project_id]

    # Get ports info
    ports = docker_manager.get_ports(project_id)

    # Send connection info
    await websocket.send_text(json.dumps({
        "type": "connected",
        "project_id": project_id,
        "ports": ports,
    }))

    # Track running agent task
    agent_task: asyncio.Task | None = None

    # Heartbeat + port watcher: keeps WS alive and auto-detects dev servers
    _last_known_ports: dict = {}
    _last_ports_update_ts = 0.0

    async def _broadcast_ports_update(ports_payload: dict, force: bool = False):
        nonlocal _last_known_ports, _last_ports_update_ts
        now = asyncio.get_running_loop().time()
        if ports_payload == _last_known_ports:
            return
        if not force and (now - _last_ports_update_ts) < PORTS_UPDATE_DEBOUNCE_SECONDS:
            return
        _last_known_ports = dict(ports_payload)
        _last_ports_update_ts = now
        await ws_manager.broadcast(project_id, {
            "type": "ports_update",
            "ports": ports_payload,
        })

    async def _heartbeat_and_ports():
        nonlocal _last_known_ports
        try:
            while True:
                await asyncio.sleep(8)
                await websocket.send_text(json.dumps({"type": "heartbeat"}))

                # Check for new dev servers while agent is working
                if agent_task and not agent_task.done():
                    try:
                        ports = docker_manager.get_ports(project_id)
                        servers = await docker_manager.find_running_servers(project_id)
                        merged = {**ports, **servers}
                        print(f"[Preview] Live port update: {merged}")
                        await _broadcast_ports_update(merged)
                    except Exception:
                        pass
        except Exception:
            pass

    heartbeat_task = asyncio.create_task(_heartbeat_and_ports())

    async def run_agent(user_message: str, images: list | None, task_type: str):
        """Run agent in background task so WS can still receive stop commands."""
        print(f"🤖 [Agent] Starting agent run with message: '{user_message[:100]}...'")
        try:
            async def on_agent_step(step: AgentStep):
                nonlocal _last_known_ports
                step_dict = step.to_dict()
                await ws_manager.broadcast(project_id, {
                    "type": "agent_step",
                    "step_type": step_dict.get("type"),
                    "step_number": step_dict.get("step_number"),
                    "content": step_dict.get("content"),
                    "tool_name": step_dict.get("tool_name"),
                    "tool_args": step_dict.get("tool_args"),
                    "tool_result": step_dict.get("tool_result"),
                    "timestamp": step_dict.get("timestamp"),
                })

                # After execute_command completes, check if a dev server appeared
                if (step_dict.get("type") == "tool_result"
                        and step_dict.get("tool_name") == "execute_command"):
                    try:
                        ports = docker_manager.get_ports(project_id)
                        servers = await docker_manager.find_running_servers(project_id)
                        merged = {**ports, **servers}
                        print(f"[Preview] Port change after execute_command: {merged}")
                        await _broadcast_ports_update(merged)
                    except Exception:
                        pass

            agent_timeout = settings.AGENT_TIMEOUT_SECONDS
            run_kw = {
                "user_message": user_message,
                "on_step": on_agent_step,
                "task_type": task_type,
                "images": images,
            }
            if getattr(settings, "AGENT_USE_STREAMING", True):
                stream_buffer: list[str] = []
                stream_flush_task: asyncio.Task | None = None

                async def _flush_stream_buffer():
                    nonlocal stream_flush_task
                    if not stream_buffer:
                        stream_flush_task = None
                        return
                    payload = "".join(stream_buffer)
                    stream_buffer.clear()
                    await ws_manager.broadcast(project_id, {"type": "agent_stream_chunk", "content": payload})
                    stream_flush_task = None

                async def _flush_stream_buffer_later():
                    await asyncio.sleep(STREAM_FLUSH_INTERVAL_SECONDS)
                    await _flush_stream_buffer()

                async def _on_stream_chunk(chunk: str):
                    nonlocal stream_flush_task
                    stream_buffer.append(chunk)
                    if stream_flush_task is None or stream_flush_task.done():
                        stream_flush_task = asyncio.create_task(_flush_stream_buffer_later())

                run_kw["on_stream_chunk"] = _on_stream_chunk
            try:
                if agent_timeout > 0:
                    result = await asyncio.wait_for(
                        engine.run(**run_kw),
                        timeout=agent_timeout,
                    )
                else:
                    # Как в ChatGPT: без глобального лимита, только таймаут на каждый запрос к LLM
                    result = await engine.run(**run_kw)
            except asyncio.TimeoutError:
                mins = agent_timeout // 60
                await ws_manager.broadcast(project_id, {
                    "type": "error",
                    "content": f"⏱️ Агент не ответил за {mins} мин. Вы можете отправить новое сообщение.",
                })
                return

            if getattr(settings, "AGENT_USE_STREAMING", True) and 'stream_flush_task' in locals() and stream_flush_task:
                if not stream_flush_task.done():
                    stream_flush_task.cancel()
                await _flush_stream_buffer()

            print(f"[Chat] WS agent_response: len={len(result)}")
            await ws_manager.broadcast(project_id, {
                "type": "agent_response",
                "content": result,
            })

            # Check for running dev servers
            container_ports = docker_manager.get_ports(project_id)
            running_servers = await docker_manager.find_running_servers(project_id)
            p = dict(container_ports)
            p.update(running_servers)
            print(f"[Preview] project_id={project_id} container_ports={container_ports} running_servers={running_servers} -> broadcast ports={p}")
            await _broadcast_ports_update(p, force=True)

        except asyncio.CancelledError:
            # Не шлём agent_stopped здесь — он уже отправлен при получении команды stop в цикле WebSocket
            pass
        except Exception as e:
            import traceback
            print(f"❌ [Agent] run_agent error:\n{traceback.format_exc()}")
            await ws_manager.broadcast(project_id, {
                "type": "error",
                "content": f"Agent error: {str(e)}",
            })

    try:
        while True:
            data = await websocket.receive_text()
            print(f"📨 [WebSocket] Received message: {data[:200]}...")  # Log received message
            msg = json.loads(data)

            # Handle stop command
            if msg.get("type") == "stop":
                print(f"🛑 [WebSocket] Stop command received")
                engine.stop()
                if agent_task and not agent_task.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(agent_task), timeout=3.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        agent_task.cancel()
                print(f"[Chat] WS agent_stopped (user requested)")
                await ws_manager.broadcast(project_id, {
                    "type": "agent_stopped",
                    "content": "⏹️ Агент остановлен",
                })
                continue

            user_message = msg.get("message", "")
            images_data = msg.get("images", [])
            attached_files = msg.get("attached_files", [])  # [{ filename, content }] — из буфера обмена
            print(f"[Chat] WS user_message: len={len(user_message or '')} has_images={bool(images_data)} attached_files={len(attached_files or [])}")
            print(f"💬 [WebSocket] User message: '{user_message[:100] or '(empty)'}...', has_images: {bool(images_data)}, attached_files: {len(attached_files)}")
            if not user_message and not images_data and not attached_files:
                print(f"⚠️ [WebSocket] Empty message and no attachments, skipping")
                continue

            # Don't start new task if agent is already running
            if agent_task and not agent_task.done():
                await ws_manager.broadcast(project_id, {
                    "type": "error",
                    "content": "⚠️ Агент уже выполняет задачу. Нажмите 'Остановить' чтобы прервать.",
                })
                continue

            # Записать прикреплённые файлы (из буфера) в .attachments/ в проекте
            if attached_files:
                for f in attached_files:
                    name = f.get("filename") or "pasted.txt"
                    content = f.get("content") or ""
                    path = f"/workspace/.attachments/{name}"
                    try:
                        await docker_manager.write_file(project_id, path, content)
                        print(f"📎 [WebSocket] Wrote attached file: .attachments/{name}")
                    except Exception as e:
                        print(f"⚠️ [WebSocket] Failed to write .attachments/{name}: {e}")
                attachment_note = "\n\n[Прикреплённые файлы из буфера записаны в проект]\nФайлы: .attachments/" + ", .attachments/".join(
                    (f.get("filename") or "pasted.txt") for f in attached_files
                ) + ". Используй read_file при необходимости."
                user_message = (user_message or "Обработай прикреплённые файлы.") + attachment_note
            elif not user_message:
                user_message = "Проанализируй изображение и создай код" if images_data else ""

            # Process images
            images = None
            if images_data:
                images = []
                for img_data in images_data:
                    if isinstance(img_data, str):
                        images.append({"base64": img_data, "mime_type": "image/png"})
                    elif isinstance(img_data, dict):
                        images.append(img_data)

            await ws_manager.broadcast(project_id, {
                "type": "user_message",
                "content": user_message,
                "has_images": bool(images),
            })

            task_type = "vision" if images else "coding"

            # Run agent in background task (WS keeps listening for stop)
            print(f"🚀 [WebSocket] Starting agent task for message: '{user_message[:50]}...'")
            agent_task = asyncio.create_task(run_agent(user_message, images, task_type))
            print(f"✅ [WebSocket] Agent task created")

    except WebSocketDisconnect:
        ws_manager.disconnect(project_id, websocket)
        heartbeat_task.cancel()
        if agent_task and not agent_task.done():
            engine.stop()
    except Exception as e:
        ws_manager.disconnect(project_id, websocket)
        heartbeat_task.cancel()
        if agent_task and not agent_task.done():
            engine.stop()
        print(f"WebSocket error: {e}")


# ============================================
# HTTP fallback for chat (non-WS clients)
# ============================================
@app.post("/api/chat")
async def http_chat(req: ChatRequest):
    """HTTP endpoint for chat (for testing or non-WS clients). Supports images via base64 in request."""
    project_id = req.project_id

    container = docker_manager.get_container(project_id)
    if not container:
        raise HTTPException(404, "Project not found. Create a project first.")

    if project_id not in agent_engines:
        agent_engines[project_id] = AgentEngine(project_id)
    engine = agent_engines[project_id]

    steps = []

    async def on_step(step: AgentStep):
        steps.append(step.to_dict())

    result = await engine.run(user_message=req.message, on_step=on_step)

    return {
        "response": result,
        "steps": steps,
    }


# ============================================
# Authentication endpoints
# ============================================
@app.post("/api/auth/register", response_model=TokenResponse)
async def register(user_data: UserRegister, db: AsyncSession = Depends(get_db)):
    """Register a new user."""
    # Check if user already exists
    result = await db.execute(select(User).where(User.email == user_data.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    result = await db.execute(select(User).where(User.username == user_data.username))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already taken"
        )
    
    # Create new user
    hashed_password = get_password_hash(user_data.password)
    new_user = User(
        email=user_data.email,
        username=user_data.username,
        hashed_password=hashed_password,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    # Create access token
    access_token = create_access_token(data={"sub": str(new_user.id)})
    
    return TokenResponse(
        access_token=access_token,
        user_id=str(new_user.id),
        username=new_user.username,
    )


@app.post("/api/auth/login", response_model=TokenResponse)
async def login(user_data: UserLogin, db: AsyncSession = Depends(get_db)):
    """Login and get access token."""
    try:
        result = await db.execute(select(User).where(User.email == user_data.email))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный email или пароль",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not verify_password(user_data.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный email или пароль",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Аккаунт отключён",
            )
        access_token = create_access_token(data={"sub": str(user.id)})
        return TokenResponse(
            access_token=access_token,
            user_id=str(user.id),
            username=user.username,
        )
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"⚠️ [auth/login] Error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Ошибка сервера. Попробуйте позже.")


@app.get("/api/auth/me")
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current user information."""
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "username": current_user.username,
        "is_active": current_user.is_active,
        "created_at": current_user.created_at.isoformat(),
    }


@app.post("/api/upload-image")
async def upload_image(file: UploadFile = File(...)):
    """Upload an image and return base64 encoded string."""
    try:
        contents = await file.read()
        base64_str = base64.b64encode(contents).decode('utf-8')
        mime_type = file.content_type or "image/png"
        return {
            "success": True,
            "base64": base64_str,
            "mime_type": mime_type,
            "filename": file.filename
        }
    except Exception as e:
        raise HTTPException(500, f"Error uploading image: {str(e)}")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "llm_providers": [n for n, p in llm_router.providers.items() if p.available],
        "sandboxes_count": len(docker_manager.list_sandboxes()),
        "telegram_bot": "active" if telegram_bot.application else "inactive",
    }


# ============================================
# Telegram Webhook (optional)
# ============================================
@app.post("/api/telegram/webhook")
async def telegram_webhook(request: dict):
    """Webhook endpoint for Telegram bot (if using webhook mode)."""
    if telegram_bot.application:
        from telegram import Update
        update = Update.de_json(request, telegram_bot.application.bot)
        await telegram_bot.application.process_update(update)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
