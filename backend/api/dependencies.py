"""从当前应用获取服务；测试可用 dependency_overrides 替换这些依赖。"""
from typing import Annotated

from fastapi import Depends, Request

from ..services.candidate_service import CandidateService
from ..services.document_service import DocumentService
from ..services.job_service import JobService
from ..services.knowledge_service import KnowledgeService
from ..services.report_service import ReportService
from ..services.run_service import RunService
from ..services.system_service import SystemService


# create_app 将服务存入 app.state；FastAPI 传入的 Request 指向当前请求所属应用。
# 同一应用的请求复用这些服务，数据库会话则由业务服务按操作创建，不在请求间共享。
# 普通 def 路由和依赖由 FastAPI 在线程池中调用，服务内部的普通调用不会自动切换线程。
def get_candidate_service(request: Request) -> CandidateService:
    """返回应用共享的画像与条件服务。"""
    return request.app.state.candidate_service


def get_document_service(request: Request) -> DocumentService:
    """返回应用共享的资料服务。"""
    return request.app.state.document_service


def get_job_service(request: Request) -> JobService:
    """返回应用共享的岗位服务。"""
    return request.app.state.job_service


def get_knowledge_service(request: Request) -> KnowledgeService:
    """返回应用共享的题库与记忆服务。"""
    return request.app.state.knowledge_service


def get_report_service(request: Request) -> ReportService:
    """返回应用共享的报告服务。"""
    return request.app.state.report_service


def get_run_service(request: Request) -> RunService:
    """返回保存当前应用内存运行状态的共享服务。"""
    return request.app.state.run_service


def get_system_service(request: Request) -> SystemService:
    """返回应用共享的健康检查与演示服务。"""
    return request.app.state.system_service


# Annotated 同时声明服务类型和获取方式；Depends 让 FastAPI 先调用对应函数，再注入其返回值。
# 路由中的 service 由此取得，无须客户端传入；测试可用原函数作为键替换依赖。
CandidateServiceDep = Annotated[CandidateService, Depends(get_candidate_service)]
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
JobServiceDep = Annotated[JobService, Depends(get_job_service)]
KnowledgeServiceDep = Annotated[KnowledgeService, Depends(get_knowledge_service)]
ReportServiceDep = Annotated[ReportService, Depends(get_report_service)]
RunServiceDep = Annotated[RunService, Depends(get_run_service)]
SystemServiceDep = Annotated[SystemService, Depends(get_system_service)]
