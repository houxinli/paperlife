"""私有接口响应禁止共享缓存。"""

from __future__ import annotations

from typing import Callable

from fastapi import HTTPException, Request, Response
from fastapi.routing import APIRoute


class PrivateRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def private_response(request: Request) -> Response:
            try:
                response = await handler(request)
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), "Cache-Control": "private, no-store"}
                raise
            response.headers["Cache-Control"] = "private, no-store"
            return response

        return private_response
