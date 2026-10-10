"""业务异常基类：API 层抛出 AppException 即可返回统一结构化错误。"""
from __future__ import annotations

from typing import Any, Optional


class AppException(Exception):
    status_code: int = 400
    code: str = "bad_request"
    message: str = "请求无效"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        details: Optional[Any] = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details
        super().__init__(self.message)
