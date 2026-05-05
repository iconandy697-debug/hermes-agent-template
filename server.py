import os
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, RedirectResponse, HTMLResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles

# 读取刚才保存的 HTML 文件
def get_index_html():
    try:
        with open("templates/index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Error: templates/index.html not found</h1>"

# 1. 健康检查 (用于 Databricks 负载均衡)
async def health(request):
    return JSONResponse({"status": "ok"})

# 2. 根目录重定向到 /setup/
async def homepage(request):
    return RedirectResponse(url='/setup/')

# 3. 渲染 Setup 页面
async def setup_page(request):
    html_content = get_index_html()
    return HTMLResponse(content=html_content)

# 4. 模拟 API (供前端 Alpine.js 调用)
async def get_status(request):
    # 这里返回 gateway 的模拟状态，你可以根据实际进程逻辑修改
    return JSONResponse({
        "state": "running",
        "pendingCount": 0,
        "isSetupDone": True
    })

routes = [
    Route("/health", health),
    Route("/", homepage),
    Route("/setup/", setup_page),
    Route("/setup/api/status", get_status), # 匹配前端获取状态的请求
]

app = Starlette(debug=True, routes=routes)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    # 打印启动日志方便在 Databricks Logs 里查看
    print(f"Starting server on port {port}...")
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*"
    )
