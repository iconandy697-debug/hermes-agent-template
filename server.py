import os
import uvicorn
import json
from starlette.applications import Starlette
from starlette.responses import JSONResponse, RedirectResponse, HTMLResponse
from starlette.routing import Route

# 配置文件路径
ENV_FILE_PATH = "/data/.hermes/.env"

# 读取 HTML 文件
def get_index_html():
    try:
        # 确保路径正确，Databricks App 建议使用绝对路径
        template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Error: templates/index.html not found</h1>"

# 1. 健康检查
async def health(request):
    return JSONResponse({"status": "ok"})

# 2. 根目录重定向
async def homepage(request):
    return RedirectResponse(url='/setup/')

# 3. 渲染 Setup 页面
async def setup_page(request):
    html_content = get_index_html()
    return HTMLResponse(content=html_content)

# 4. 获取状态 API
async def get_status(request):
    # 检查配置是否存在以决定 isSetupDone
    setup_done = os.path.exists(ENV_FILE_PATH)
    return JSONResponse({
        "state": "running",
        "pendingCount": 0,
        "isSetupDone": setup_done
    })

# 5. 保存配置 API (解决 Save Failed)
async def save_config(request):
    try:
        payload = await request.json()
        config_vars = payload.get("vars", {})
        
        # 将字典转换为 .env 格式内容
        env_content = ""
        for key, value in config_vars.items():
            if value:  # 仅保存非空值
                env_content += f"{key}={value}\n"
        
        # 确保目录存在
        os.makedirs(os.path.dirname(ENV_FILE_PATH), exist_ok=True)
        
        # 写入文件
        with open(ENV_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(env_content)
            
        print(f"[server] Configuration saved to {ENV_FILE_PATH}")
        
        # 如果前端勾选了 "Restart gateway after save"
        if payload.get("restart", True):
            print("[server] Restart signal received (Implementation pending process manager)")
            # 这里可以触发你的 hermes 进程重启逻辑
            
        return JSONResponse({"ok": True})
    except Exception as e:
        print(f"[server] Save error: {str(e)}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

# 6. 重启接口 (对应前端 Restart 按钮)
async def restart_gateway(request):
    print("[server] Manually triggering gateway restart...")
    return JSONResponse({"ok": True})

routes = [
    Route("/health", health),
    Route("/", homepage),
    Route("/setup/", setup_page),
    Route("/setup/api/status", get_status),
    Route("/setup/api/save", save_config, methods=["POST"]),
    Route("/setup/api/restart", restart_gateway, methods=["POST"]),
]

app = Starlette(debug=True, routes=routes)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting server on port {port}...")
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*"
    )
