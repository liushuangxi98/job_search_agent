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


def get_candidate_service(request: Request) -> CandidateService:
    return request.app.state.candidate_service


def get_document_service(request: Request) -> DocumentService:
    return request.app.state.document_service


def get_job_service(request: Request) -> JobService:
    return request.app.state.job_service


def get_knowledge_service(request: Request) -> KnowledgeService:
    return request.app.state.knowledge_service


def get_report_service(request: Request) -> ReportService:
    return request.app.state.report_service


def get_run_service(request: Request) -> RunService:
    return request.app.state.run_service


def get_system_service(request: Request) -> SystemService:
    return request.app.state.system_service


CandidateServiceDep = Annotated[CandidateService, Depends(get_candidate_service)]
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
JobServiceDep = Annotated[JobService, Depends(get_job_service)]
KnowledgeServiceDep = Annotated[KnowledgeService, Depends(get_knowledge_service)]
ReportServiceDep = Annotated[ReportService, Depends(get_report_service)]
RunServiceDep = Annotated[RunService, Depends(get_run_service)]
SystemServiceDep = Annotated[SystemService, Depends(get_system_service)]
