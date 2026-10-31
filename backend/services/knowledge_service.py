"""题目和已确认记忆的业务校验；RAG 与记忆生成留给用户 Agent。"""
from sqlalchemy.orm import Session, sessionmaker

from ..exceptions import AppError
from ..repositories.document_repository import DocumentRepository
from ..repositories.knowledge_repository import KnowledgeRepository
from ..schemas import MemoryData, QuestionData

SCHEMAS = {'questions': QuestionData, 'memories': MemoryData}


class KnowledgeService:
    def __init__(self, session_factory: sessionmaker[Session]):
        """接收 Session 工厂，保存题目时在同一事务内检查资料来源。"""
        self.session_factory = session_factory

    @staticmethod
    def serialize(row):
        """将题目或记忆 ORM 行转换为 id、data 和 UTC 创建时间组成的字典。"""
        return {'id': row.id, 'data': row.data, 'created_at': row.created_at.isoformat() + 'Z'}

    def list(self, kind: str, query='', offset=0, limit=100):
        """查询 questions 或 memories，返回 items 与分页前的 total。

        query 搜索题干或记忆正文；offset、limit 按记录条数计，无匹配返回空列表。
        """
        with self.session_factory() as session:
            rows, total = KnowledgeRepository(session).list(kind, query, offset, limit)
            return {'items': [self.serialize(row) for row in rows], 'total': total}

    def save(self, kind: str, data: dict, record_id=None):
        """校验并保存题目或记忆，返回记录字典；record_id 为空表示新增。

        题目 document_id 可为空，但已提供的来源必须存在；数据非法或记录缺失会抛异常。
        仅操作 MySQL 元数据，不建立向量索引，也不替调用方执行记忆确认。
        """
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
        """删除 questions 或 memories 中的记录；缺失抛 AppError，成功无返回值且不清理向量。"""
        with self.session_factory.begin() as session:
            repository = KnowledgeRepository(session)
            row = repository.get(kind, record_id)
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            repository.delete(row)
