import os
import uvicorn
import json
from starlette.applications import Starlette
from starlette.responses import JSONResponse, RedirectResponse, HTMLResponse
from starlette.routing import Route

# 路径配置：优先使用 /data 挂载点，否则使用当前目录
STORAGE_DIR = "/data" if os.path.exists("/data") else "."
ENV_FILE_PATH = os.path.join(STORAGE_DIR, ".env")

def get_index_html():
    try:
        # 确保 templates 文件夹在 server.py 同级目录
        template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Error: templates/index.html not found</h1><p>Please check your file structure.</p>"

# --- API 处理函数 ---

async def health(request):
    return JSONResponse({"status": "ok"})

async def homepage(request):
    return RedirectResponse(url='/setup/')

async def setup_page(request):
    return HTMLResponse(content=get_index_html())

async def get_status(request):
    setup_done = os.path.exists(ENV_FILE_PATH)
    return JSONResponse({
        "state": "running",
        "pendingCount": 0,
        "isSetupDone": setup_done
    })

async def get_logs(request):
    return JSONResponse({
        "logs": ["System started...", "Waiting for configuration..."]
    })

async def get_pairing_pending(request):
    return JSONResponse([]) 

async def get_pairing_approved(request):
    return JSONResponse([]) 

async def save_config(request):
    try:
        payload = await request.json()
        config_vars = payload.get("vars", {})
        env_content = "\n".join([f"{k}={v}" for k, v in config_vars.items() if v])
        
        # 确保目录存在
        os.makedirs(os.path.dirname(ENV_FILE_PATH), exist_ok=True)
        
        with open(ENV_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(env_content)
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

# --- 路由映射 ---

routes = [
    Route("/health", health),
    Route("/", homepage),
    Route("/setup/", setup_page),
    Route("/setup/api/status", get_status),
    Route("/setup/api/logs", get_logs),
    Route("/setup/api/pairing/pending", get_pairing_pending),
    Route("/setup/api/pairing/approved", get_pairing_approved),
    Route("/setup/api/save", save_config, methods=["POST"]),
]

app = Starlette(debug=True, routes=routes)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
