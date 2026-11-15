"""集中转换错误，避免返回连接密码或内部堆栈。"""
import traceback

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from ..exceptions import AppError


def register_exception_handlers(app: FastAPI):
    """由 create_app 调用，为 app 注册异常处理器，无返回值。
    统一响应含 error.code/message，校验错误另带 fields；未匹配的异常仍由框架处理。
    """
    @app.exception_handler(AppError)
    async def app_error(request, exc):
        """将应用异常 exc 的状态码和可公开信息转成 JSON 错误响应。"""
        # 控制台保留异常详情，页面使用下面的业务提示。
        traceback.print_exception(exc)
        return JSONResponse({'error': {'code': exc.code, 'message': exc.message}}, status_code=exc.status)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        """将数据库访问库 SQLAlchemy 的异常统一转为 503，隐藏原始连接信息和 SQL 细节。"""
        traceback.print_exception(exc)
        return JSONResponse({'error': {
            'code': 'DATABASE_UNAVAILABLE',
            'message': '数据库不可用，请检查 .env 并运行初始化命令；未切换其他数据库',
        }}, status_code=503)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_error(request, exc):
        """将请求或服务中的字段校验异常转为 422，并在 fields 中返回字段位置和原因。"""
        traceback.print_exception(exc)
        # 只提取位置和原因，避免直接序列化异常所携带的原始输入及上下文对象。
        errors = [
            {'field': '.'.join(str(x) for x in item['loc']), 'message': item['msg']}
            for item in exc.errors()
        ]
        return JSONResponse({'error': {
            'code': 'INVALID_INPUT', 'message': '输入格式不正确', 'fields': errors,
        }}, status_code=422)
