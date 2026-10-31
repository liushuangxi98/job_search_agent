"""接口层：路由只接收参数、调用业务服务并构造 HTTP 响应。"""
from fastapi import APIRouter

from . import candidates, documents, jobs, knowledge, reports, runs, system

router = APIRouter(prefix='/api')
for module in (candidates, documents, jobs, knowledge, reports, runs, system):
    router.include_router(module.router)
