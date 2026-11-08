"""已确认的个人记忆，在一次事务中新增、编辑或移除。"""
from sqlalchemy.orm import Session, sessionmaker

from ..exceptions import AppError
from ..repositories.memory_repository import MemoryRepository
from ..schemas import MemoryData


class MemoryService:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    @staticmethod
    def serialize(row):
        return {'id': row.id, 'data': row.data, 'created_at': row.created_at.isoformat() + 'Z'}

    def list(self, query='', offset=0, limit=100):
        with self.session_factory() as session:
            rows, total = MemoryRepository(session).list(query, offset, limit)
            return {'items': [self.serialize(row) for row in rows], 'total': total}

    def save(self, data: dict, record_id=None):
        data = MemoryData.model_validate(data).model_dump()
        with self.session_factory.begin() as session:
            repository = MemoryRepository(session)
            row = repository.get(record_id) if record_id else None
            if record_id and row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            return self.serialize(repository.save(data, row))

    def delete(self, record_id: str):
        with self.session_factory.begin() as session:
            repository = MemoryRepository(session)
            row = repository.get(record_id)
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            repository.delete(row)
