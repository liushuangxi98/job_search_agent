"""集中转换错误，避免返回连接密码或内部堆栈。"""
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from ..exceptions import AppError


def register_exception_handlers(app: FastAPI):
    @app.exception_handler(AppError)
    async def app_error(request, exc):
        return JSONResponse({'error': {'code': exc.code, 'message': exc.message}}, status_code=exc.status)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        return JSONResponse({'error': {
            'code': 'DATABASE_UNAVAILABLE',
            'message': '数据库不可用，请检查 .env 并运行初始化命令；未切换其他数据库',
        }}, status_code=503)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_error(request, exc):
        errors = [
            {'field': '.'.join(str(x) for x in item['loc']), 'message': item['msg']}
            for item in exc.errors()
        ]
        return JSONResponse({'error': {
            'code': 'INVALID_INPUT', 'message': '输入格式不正确', 'fields': errors,
        }}, status_code=422)
