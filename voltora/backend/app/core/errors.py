from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Domain error rendered as {"error": {"code", "message", "details"}}."""

    status = 400

    def __init__(self, code: str, message: str, status: int | None = None, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        if status is not None:
            self.status = status
        self.details = details


class NotFound(AppError):
    status = 404

    def __init__(self, what: str = "Resource"):
        super().__init__("not_found", f"{what} not found")


class Forbidden(AppError):
    status = 403

    def __init__(self, message: str = "You do not have permission for this action", code: str = "forbidden"):
        super().__init__(code, message)


class Unauthorized(AppError):
    status = 401

    def __init__(self, message: str = "Sign in required"):
        super().__init__("unauthorized", message)


class Conflict(AppError):
    status = 409

    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(code, message, details=details)


def _body(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status)


async def validation_error_handler(request: Request, exc: RequestValidationError):
    details = [
        {"loc": [str(p) for p in e.get("loc", [])], "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()
    ]
    return JSONResponse(_body("validation_error", "Some fields are invalid", details), status_code=422)
