"""接口层：路由只接收参数、调用业务服务并构造 HTTP 响应。"""
from fastapi import APIRouter

from . import candidates, documents, jobs, memories, reports, runs, system

# 各模块的 @router.get/post 等将 HTTP 方法和路径绑定到下方函数，供 FastAPI 按请求调用。
# include_router 拼接 /api、子路由前缀和接口路径，例如岗位详情为 /api/jobs/{key}。
# tags 只影响接口文档分组；汇总后的路由由 app.create_app 注册到应用。
router = APIRouter(prefix='/api')
# 接口返回的字典由 FastAPI 转成 JSON，默认状态为 200；显式 Response 则决定自身响应。
for module in (candidates, documents, jobs, memories, reports, runs, system):
    router.include_router(module.router)
