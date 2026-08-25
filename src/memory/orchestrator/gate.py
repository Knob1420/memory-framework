"""编排门：所有请求的统一准入关卡。

只做三件事：X-Workspace 有没有、认不认识、挂 request.state.workspace。
端点个性的解析在 transport，共性的关卡在这里（见 docs/contracts/http-api.md）。
"""

from fastapi import Request
from fastapi.responses import JSONResponse

EXEMPT_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
}  # 免检清单


def workspace_gate(workspaces: list[str]):
    """注册表由 config 提供，中间件不自带名单。"""

    async def _gate(request: Request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        ws = request.headers.get("X-Workspace")
        if not ws:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "WORKSPACE_REQUIRED", "message": "缺少 X-Workspace 头"}},
            )
        if ws not in workspaces:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "BAD_REQUEST", "message": f"未注册的 workspace: {ws}"}},
            )
        request.state.workspace = ws
        return await call_next(request)

    return _gate
