"""应用装配入口：创建依赖、注册路由和管理资源，不处理具体业务。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agent_gateway import load_handler
from .api import router
from .api.exception_handlers import register_exception_handlers
from .api.middleware import BodyLimitMiddleware, revalidate_static_assets
from .config import ROOT, Settings
from .database import Database
from .services.agent_data_service import AgentDataService
from .services.profile_service import ProfileService
from .services.document_service import DocumentService
from .services.job_service import JobService
from .services.memory_service import MemoryService
from .services.report_service import ReportService
from .services.run_service import RunService
from .services.system_service import SystemService


def create_app(settings: Settings | None = None, handler=None) -> FastAPI:
    """装配并返回 FastAPI 应用，不在这里监听网络端口。

    settings 为 None 时读取项目配置，handler 为 None 时按模式加载 Agent 入口。
    创建上传、日志目录及连接池对象；实际数据库操作由后续业务请求触发。
    """
    settings = settings or Settings()
    database = Database(settings)
    profiles = ProfileService(database.session_factory)
    documents = DocumentService(settings.data_dir, database.session_factory)
    jobs = JobService(database.session_factory)
    memories = MemoryService(database.session_factory)
    reports = ReportService(database.session_factory)
    agent_data = AgentDataService(profiles, documents, jobs, memories, reports)
    handler = handler if handler is not None else load_handler(settings.agent_mode)
    runs = RunService(settings, agent_data, documents, handler)

    # FastAPI 进入这个上下文后提供服务，退出时执行 yield 后的资源清理。
    @asynccontextmanager
    async def lifespan(app):
        """提供应用生命周期上下文；关闭时先等待后台运行退出，再释放连接池。"""
        try:
            yield
        finally:
            # 后台运行退出后再关闭连接池，避免任务使用已经释放的资源。
            try:
                await runs.close()
            finally:
                database.close()

    app = FastAPI(title='求职决策 Agent · 应用支撑', lifespan=lifespan)
    # 服务对象在当前应用内复用；共享服务不代表共享一次数据库操作的 Session。
    app.state.settings = settings
    app.state.database = database
    app.state.profile_service = profiles
    app.state.document_service = documents
    app.state.job_service = jobs
    app.state.memory_service = memories
    app.state.report_service = reports
    app.state.agent_data_service = agent_data
    app.state.run_service = runs
    app.state.system_service = SystemService(database, settings, handler)

    app.add_middleware(BodyLimitMiddleware)
    app.middleware('http')(revalidate_static_assets)
    register_exception_handlers(app)
    app.include_router(router)

    @app.get('/')
    def index():
        """响应 GET /，返回前端页面文件；浏览器再从 /static 加载脚本和样式。"""
        return FileResponse(ROOT / 'frontend' / 'index.html')

    app.mount('/static', StaticFiles(directory=ROOT / 'frontend', check_dir=False), name='frontend')
    return app
