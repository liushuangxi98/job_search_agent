"""保留 Agent 的 services.repository 接口，所有操作转交给业务服务。

这里是应用接入适配器，不是 Agent 实现，也不直接访问数据库。
"""
from .candidate_service import CandidateService
from .document_service import DocumentService
from .job_service import JobService
from .knowledge_service import KnowledgeService
from .report_service import ReportService


class AgentDataService:
    def __init__(self, candidates: CandidateService, documents: DocumentService,
                 jobs: JobService, knowledge: KnowledgeService, reports: ReportService):
        self.candidates = candidates
        self.document_service = documents
        self.job_service = jobs
        self.knowledge = knowledge
        self.report_service = reports

    def candidate(self, section):
        return self.candidates.get(section)

    def update_candidate(self, section, data, revision, confirmed=None):
        return self.candidates.update(section, data, revision, confirmed)

    def job(self, key):
        return self.job_service.get(key)

    def jobs(self, q='', offset=0, limit=100):
        return self.job_service.list(q, offset, limit)

    def save_job(self, data, key=None, revision=None):
        return self.job_service.save(data, key, revision)

    def delete_job(self, key):
        return self.job_service.delete(key)

    def document(self, key, *, internal=False):
        return self.document_service.get(key, internal=internal)

    def documents(self, kind=None):
        return self.document_service.list(kind)

    def add_document(self, **values):
        return self.document_service.add_metadata(**values)

    def delete_document(self, key):
        return self.document_service.delete_metadata(key)

    def records(self, kind, q='', offset=0, limit=100):
        return self.knowledge.list(kind, q, offset, limit)

    def save_record(self, kind, data, key=None):
        return self.knowledge.save(kind, data, key)

    def delete_record(self, kind, key):
        return self.knowledge.delete(kind, key)

    def save_report(self, kind, data, snapshot, run_id, is_demo):
        return self.report_service.save(kind, data, snapshot, run_id, is_demo)

    def report(self, key):
        return self.report_service.get(key)

    def reports(self):
        return self.report_service.list()
