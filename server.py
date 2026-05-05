"""
Hermes Agent — Databricks Optimized Admin Server.

主要修改点：
1. 增加 hermes 绝对路径探测，解决 Databricks 环境下的 Errno 2 错误。
2. 强化目录权限检查，确保持久化配置可读写。
3. 适配 Databricks 的环境变量继承机制。
"""

import asyncio
import json
import os
import re
import secrets
import signal
import time
import shutil
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import websockets
import websockets.exceptions
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Route, WebSocketRoute
from starlette.templating import Jinja2Templates
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

# --- 路径与环境初始化 (Databricks 适配) ---
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# 优先使用环境变量中的 HERMES_HOME，否则使用默认值
HERMES_HOME = os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
ENV_FILE = Path(HERMES_HOME) / ".env"
PAIRING_DIR = Path(HERMES_HOME) / "pairing"

# 核心修复：自动寻找 hermes 命令的绝对路径
# 在 Databricks 中，如果通过 pip/uv 安装，路径可能在 /databricks/python/bin/ 或 /usr/local/bin/
HERMES_BIN = shutil.which("hermes")
if not HERMES_BIN:
    # 备选常见路径
    for p in ["/usr/local/bin/hermes", "/usr/bin/hermes", "/app/.local/bin/hermes"]:
        if os.path.exists(p):
            HERMES_BIN = p
            break

print(f"[server] Databricks 适配自检:", flush=True)
print(f"         - 命令路径 (HERMES_BIN): {HERMES_BIN or '⚠ 未找到 hermes 命令'}", flush=True)
print(f"         - 数据目录 (HERMES_HOME): {HERMES_HOME}", flush=True)

# 确保目录存在并可写
os.makedirs(HERMES_HOME, exist_ok=True)
os.makedirs(PAIRING_DIR, exist_ok=True)

# ── 配置常量 ──────────────────────────────────────────────────────────────────
HERMES_DASHBOARD_HOST = "127.0.0.1"
HERMES_DASHBOARD_PORT = int(os.environ.get("HERMES_DASHBOARD_PORT", "9119"))
HERMES_DASHBOARD_URL = f"http://{HERMES_DASHBOARD_HOST}:{HERMES_DASHBOARD_PORT}"

# 从环境变量获取管理凭据，若无则使用默认
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "123456aa")

# 环境变量注册表 (保持原样)
ENV_VARS = [
    ("LLM_MODEL", "Model", "model", False),
    ("OPENROUTER_API_KEY", "OpenRouter", "provider", True),
    ("DEEPSEEK_API_KEY", "DeepSeek", "provider", True),
    ("DASHSCOPE_API_KEY", "DashScope", "provider", True),
    ("GLM_API_KEY", "GLM / Z.AI", "provider", True),
    ("KIMI_API_KEY", "Kimi", "provider", True),
    ("MINIMAX_API_KEY", "MiniMax", "provider", True),
    ("HF_TOKEN", "Hugging Face", "provider", True),
    ("NVIDIA_API_KEY", "NVIDIA NIM", "provider", True),
    ("ARCEE_API_KEY", "Arcee AI", "provider", True),
    ("STEPFUN_API_KEY", "Step Plan", "provider", True),
    ("AI_GATEWAY_API_KEY", "Vercel AI Gateway", "provider", True),
    ("GEMINI_API_KEY", "Google AI Studio", "provider", True),
    ("PARALLEL_API_KEY", "Parallel (search)", "tool", True),
    ("FIRECRAWL_API_KEY", "Firecrawl (scrape)", "tool", True),
    ("TAVILY_API_KEY", "Tavily (search)", "tool", True),
    ("FAL_KEY", "FAL (image gen)", "tool", True),
    ("BROWSERBASE_API_KEY", "Browserbase key", "tool", True),
    ("BROWSERBASE_PROJECT_ID", "Browserbase project", "tool", False),
    ("GITHUB_TOKEN", "GitHub token", "tool", True),
    ("VOICE_TOOLS_OPENAI_KEY", "OpenAI (voice/TTS)", "tool", True),
    ("HONCHO_API_KEY", "Honcho (memory)", "tool", True),
    ("TELEGRAM_BOT_TOKEN", "Bot Token", "telegram", True),
    ("TELEGRAM_ALLOWED_USERS", "Allowed User IDs", "telegram", False),
    ("DISCORD_BOT_TOKEN", "Bot Token", "discord", True),
    ("DISCORD_ALLOWED_USERS", "Allowed User IDs", "discord", False),
    ("SLACK_BOT_TOKEN", "Bot Token (xoxb-...)", "slack", True),
    ("SLACK_APP_TOKEN", "App Token (xapp-...)", "slack", True),
    ("WHATSAPP_ENABLED", "Enable WhatsApp", "whatsapp", False),
    ("EMAIL_ADDRESS", "Email Address", "email", False),
    ("EMAIL_PASSWORD", "Email Password", "email", True),
    ("EMAIL_IMAP_HOST", "IMAP Host", "email", False),
    ("EMAIL_SMTP_HOST", "SMTP Host", "email", False),
    ("MATTERMOST_URL", "Server URL", "mattermost", False),
    ("MATTERMOST_TOKEN", "Bot Token", "mattermost", True),
    ("MATRIX_HOMESERVER", "Homeserver URL", "matrix", False),
    ("MATRIX_ACCESS_TOKEN", "Access Token", "matrix", True),
    ("MATRIX_USER_ID", "User ID", "matrix", False),
    ("GATEWAY_ALLOW_ALL_USERS", "Allow all users", "gateway", False),
    ("ADMIN_USERNAME", "Admin username", "admin", False),
    ("ADMIN_PASSWORD", "Admin password", "admin", True),
]

SECRET_KEYS = {k for k, _, _, s in ENV_VARS if s}
PROVIDER_KEYS = [k for k, _, c, _ in ENV_VARS if c == "provider"]
CHANNEL_MAP = {
    "Telegram": "TELEGRAM_BOT_TOKEN",
    "Discord": "DISCORD_BOT_TOKEN",
    "Slack": "SLACK_BOT_TOKEN",
    "WhatsApp": "WHATSAPP_ENABLED",
    "Email": "EMAIL_ADDRESS",
    "Mattermost": "MATTERMOST_TOKEN",
    "Matrix": "MATRIX_ACCESS_TOKEN",
}

# ── 辅助函数 ──────────────────────────────────────────────────────────────────
def read_env(path: Path) -> dict[str, str]:
    if not path.exists(): return {}
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        v = v.strip().strip('"').strip("'")
        out[k.strip()] = v
    return out

def write_config_yaml(data: dict[str, str]) -> None:
    model = data.get("LLM_MODEL", "")
    config_path = Path(HERMES_HOME) / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(f"""\
model:
  default: "{model}"
  provider: "auto"
terminal:
  backend: "local"
  timeout: 60
  cwd: "/tmp"
agent:
  max_iterations: 50
data_dir: "{HERMES_HOME}"
""")

def write_env(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in data.items() if v]
    path.write_text("\n".join(lines))

def mask(data: dict[str, str]) -> dict[str, str]:
    return {k: (v[:8] + "***" if len(v) > 8 else "***") if k in SECRET_KEYS and v else v for k, v in data.items()}

def unmask(new: dict[str, str], existing: dict[str, str]) -> dict[str, str]:
    return {k: (existing.get(k, "") if k in SECRET_KEYS and v.endswith("***") else v) for k, v in new.items()}

# ── Auth (Cookie-based) ───────────────────────────────────────────────────────
COOKIE_NAME = "hermes_auth"
COOKIE_MAX_AGE = 7 * 86400
COOKIE_SECRET = secrets.token_bytes(32)

def _make_auth_token() -> str:
    expires = str(int(time.time()) + COOKIE_MAX_AGE)
    sig = hmac.new(COOKIE_SECRET, expires.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"

import hmac, hashlib
def _verify_auth_token(token: str) -> bool:
    try:
        expires_s, sig = token.rsplit(".", 1)
        if int(expires_s) < time.time(): return False
        expected = hmac.new(COOKIE_SECRET, expires_s.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except: return False

def guard(request: Request) -> Response | None:
    if _verify_auth_token(request.cookies.get(COOKIE_NAME, "")): return None
    return RedirectResponse(f"/login?returnTo={request.url.path}", status_code=302)

# ── Gateway 管理器 ─────────────────────────────────────────────────────────────
class Gateway:
    def __init__(self):
        self.proc: asyncio.subprocess.Process | None = None
        self.state = "stopped"
        self.logs: deque[str] = deque(maxlen=500)
        self.started_at: float | None = None
        self.restarts = 0

    async def start(self):
        if self.proc and self.proc.returncode is None: return
        if not HERMES_BIN:
            self.state = "error"
            self.logs.append("[error] hermes binary not found in PATH")
            return

        self.state = "starting"
        try:
            env = {**os.environ, "HERMES_HOME": HERMES_HOME}
            env.update(read_env(ENV_FILE))
            write_config_yaml(read_env(ENV_FILE))
            
            # 使用探测到的绝对路径启动
            self.proc = await asyncio.create_subprocess_exec(
                HERMES_BIN, "gateway",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            self.state = "running"
            self.started_at = time.time()
            asyncio.create_task(self._drain())
        except Exception as e:
            self.state = "error"
            self.logs.append(f"[error] Failed to start: {e}")

    async def stop(self):
        if not self.proc or self.proc.returncode is not None:
            self.state = "stopped"
            return
        self.proc.terminate()
        await self.proc.wait()
        self.state = "stopped"

    async def restart(self):
        await self.stop()
        self.restarts += 1
        await self.start()

    async def _drain(self):
        assert self.proc and self.proc.stdout
        async for raw in self.proc.stdout:
            line = ANSI_ESCAPE.sub("", raw.decode(errors="replace").rstrip())
            self.logs.append(line)

    def status(self) -> dict:
        uptime = int(time.time() - self.started_at) if self.started_at and self.state == "running" else None
        return {"state": self.state, "uptime": uptime, "restarts": self.restarts}

gw = Gateway()

# ── Dashboard 子进程 ──────────────────────────────────────────────────────────
class Dashboard:
    def __init__(self):
        self.proc: asyncio.subprocess.Process | None = None

    async def start(self):
        if self.proc and self.proc.returncode is None: return
        if not HERMES_BIN: return
        try:
            self.proc = await asyncio.create_subprocess_exec(
                HERMES_BIN, "dashboard",
                "--host", HERMES_DASHBOARD_HOST,
                "--port", str(HERMES_DASHBOARD_PORT),
                "--no-open", "--tui",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env={**os.environ, "HERMES_HOME": HERMES_HOME}
            )
        except Exception as e:
            print(f"[dashboard] spawn error: {e}")

dash = Dashboard()

# ── API & 路由定义 ───────────────────────────────────────────────────────────
async def api_config_get(request: Request):
    if err := guard(request): return err
    data = read_env(ENV_FILE)
    defs = [{"key": k, "label": l, "category": c, "secret": s} for k, l, c, s in ENV_VARS]
    return JSONResponse({"vars": mask(data), "defs": defs})

async def api_config_put(request: Request):
    if err := guard(request): return err
    body = await request.json()
    new_vars = body.get("vars", {})
    existing = read_env(ENV_FILE)
    merged = unmask(new_vars, existing)
    write_env(ENV_FILE, merged)
    write_config_yaml(merged)
    if body.get("_restart"): asyncio.create_task(gw.restart())
    return JSONResponse({"ok": True})

async def api_status(request: Request):
    if err := guard(request): return err
    return JSONResponse({"gateway": gw.status()})

async def api_logs(request: Request):
    if err := guard(request): return err
    return JSONResponse({"lines": list(gw.logs)})

# ── 生命周期管理 ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: Starlette):
    # 启动时
    await dash.start()
    if os.path.exists(ENV_FILE):
        await gw.start()
    yield
    # 关闭时
    await gw.stop()
    if dash.proc: dash.proc.terminate()

# ── 路由映射 ──────────────────────────────────────────────────────────────────
routes = [
    Route("/", lambda r: RedirectResponse("/setup/")),
    Route("/health", lambda r: JSONResponse({"status":"ok"})),
    Route("/setup/api/config", api_config_get, methods=["GET"]),
    Route("/setup/api/config", api_config_put, methods=["POST"]),
    Route("/setup/api/status", api_status),
    Route("/setup/api/logs", api_logs),
    Route("/setup/api/gw/start", lambda r: asyncio.create_task(gw.start()) or JSONResponse({"ok":True}), methods=["POST"]),
    Route("/setup/api/gw/stop", lambda r: asyncio.create_task(gw.stop()) or JSONResponse({"ok":True}), methods=["POST"]),
]

app = Starlette(debug=True, routes=routes, lifespan=lifespan)

if __name__ == "__main__":
    import uvicorn
    # Databricks Apps 默认通常使用 8000 或 8080
    # 这里的修改确保了即使环境变量没读到，也优先尝试 start.sh 指定的 8000
    port = int(os.environ.get("PORT", 8000))
    
    print(f"[server] Starting Uvicorn on http://0.0.0.0:{port}", flush=True)
    
    uvicorn.run(
        "server:app",  # 使用字符串导入模式，支持热重载（如果需要）
        host="0.0.0.0", 
        port=port, 
        log_level="info",
        proxy_headers=True, # 在 Databricks 反向代理环境下获取正确 IP
        forwarded_allow_ips="*" 
    )
