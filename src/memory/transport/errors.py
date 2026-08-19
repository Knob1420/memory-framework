"""统一错误格式：所有非 2xx 响应长成 {"error": {"code", "message"}}（http-api.md 全局约定）。

- HTTPException（含 FastAPI 校验失败的 422）：转成统一格式
- 未捕获异常：500 INTERNAL，不泄漏堆栈给调用方（打日志）
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("memory.errors")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exc(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "HTTP_ERROR", "message": str(exc.detail)}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exc(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "BAD_REQUEST", "message": str(exc.errors()[:3])}},
        )

    @app.exception_handler(Exception)
    async def unhandled_exc(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL", "message": "内部错误，见服务端日志"}},
        )
