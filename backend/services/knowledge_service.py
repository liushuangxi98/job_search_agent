"""题目和已确认记忆的业务校验；RAG 与记忆生成留给用户 Agent。"""
from sqlalchemy.orm import Session, sessionmaker

from ..exceptions import AppError
from ..repositories.document_repository import DocumentRepository
from ..repositories.knowledge_repository import KnowledgeRepository
from ..schemas import MemoryData, QuestionData

SCHEMAS = {'questions': QuestionData, 'memories': MemoryData}


class KnowledgeService:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    @staticmethod
    def serialize(row):
        return {'id': row.id, 'data': row.data, 'created_at': row.created_at.isoformat() + 'Z'}

    def list(self, kind: str, query='', offset=0, limit=100):
        with self.session_factory() as session:
            rows, total = KnowledgeRepository(session).list(kind, query, offset, limit)
            return {'items': [self.serialize(row) for row in rows], 'total': total}

    def save(self, kind: str, data: dict, record_id=None):
        data = SCHEMAS[kind].model_validate(data).model_dump()
        with self.session_factory.begin() as session:
            repository = KnowledgeRepository(session)
            row = repository.get(kind, record_id) if record_id else None
            if record_id and row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            if kind == 'questions' and data['document_id']:
                if DocumentRepository(session).get(data['document_id']) is None:
                    raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            return self.serialize(repository.save(kind, data, row))

    def delete(self, kind: str, record_id: str):
        with self.session_factory.begin() as session:
            repository = KnowledgeRepository(session)
            row = repository.get(kind, record_id)
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            repository.delete(row)
