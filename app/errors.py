"""Expected failures, answered as ``{"error": {"code", "message"}}``."""

from __future__ import annotations


class AppError(Exception):
    status_code = 400

    def __init__(self, code: str, message: str, status_code: int | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class NotFound(AppError):
    status_code = 404


class Invalid(AppError):
    status_code = 422


class Conflict(AppError):
    status_code = 409


class Unauthorized(AppError):
    status_code = 401
