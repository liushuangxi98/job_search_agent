"""应用装配入口：创建依赖、注册路由和管理资源，不处理具体业务。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agent_gateway import load_handler
from .api import router
from .api.exception_handlers import register_exception_handlers
from .api.middleware import BodyLimitMiddleware
from .config import ROOT, Settings
from .database import Database
from .services.agent_data_service import AgentDataService
from .services.candidate_service import CandidateService
from .services.document_service import DocumentService
from .services.job_service import JobService
from .services.knowledge_service import KnowledgeService
from .services.report_service import ReportService
from .services.run_service import RunService
from .services.system_service import SystemService


def create_app(settings: Settings | None = None, handler=None) -> FastAPI:
    settings = settings or Settings()
    database = Database(settings)
    candidates = CandidateService(database.session_factory)
    documents = DocumentService(settings.data_dir, database.session_factory)
    jobs = JobService(database.session_factory)
    knowledge = KnowledgeService(database.session_factory)
    reports = ReportService(database.session_factory)
    agent_data = AgentDataService(candidates, documents, jobs, knowledge, reports)
    handler = handler if handler is not None else load_handler(settings.agent_mode)
    runs = RunService(settings, agent_data, documents, handler)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            # 后台运行退出后再关闭连接池，避免任务使用已经释放的资源。
            try:
                await runs.close()
            finally:
                database.close()

    app = FastAPI(title='求职决策 Agent · 应用支撑', lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.candidate_service = candidates
    app.state.document_service = documents
    app.state.job_service = jobs
    app.state.knowledge_service = knowledge
    app.state.report_service = reports
    app.state.agent_data_service = agent_data
    app.state.run_service = runs
    app.state.system_service = SystemService(database, settings, handler)

    app.add_middleware(BodyLimitMiddleware)
    register_exception_handlers(app)
    app.include_router(router)

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'frontend' / 'index.html')

    app.mount('/static', StaticFiles(directory=ROOT / 'frontend', check_dir=False), name='frontend')
    return app
