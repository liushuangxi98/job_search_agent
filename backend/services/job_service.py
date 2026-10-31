"""岗位校验、重复导入合并、来源关联和修订控制。"""
from copy import deepcopy

from sqlalchemy.orm import Session, sessionmaker

from ..exceptions import AppError
from ..repositories.document_repository import DocumentRepository
from ..repositories.job_repository import JobRepository
from ..schemas import JobData


class JobService:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    @staticmethod
    def serialize(repository, row):
        return {
            'id': row.id,
            'revision': row.revision,
            'created_at': row.created_at.isoformat() + 'Z',
            'data': {**deepcopy(row.data), 'source_ids': repository.source_ids(row.id)},
        }

    def get(self, job_id: str):
        with self.session_factory() as session:
            repository = JobRepository(session)
            row = repository.get(job_id)
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            return self.serialize(repository, row)

    def list(self, query='', offset=0, limit=100):
        with self.session_factory() as session:
            repository = JobRepository(session)
            rows, total = repository.list(query, offset, limit)
            return {'items': [self.serialize(repository, row) for row in rows], 'total': total}

    def save(self, data: dict, job_id=None, revision=None):
        data = JobData.model_validate(data).model_dump()
        source_ids = list(dict.fromkeys(data.pop('source_ids')))
        with self.session_factory.begin() as session:
            repository = JobRepository(session)
            documents = DocumentRepository(session)
            # 先确认全部来源有效；任何一步失败都会回滚岗位和来源关联。
            for source_id in source_ids:
                if documents.get(source_id) is None:
                    raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            if job_id:
                row = repository.get(job_id, lock=True)
                if row is None:
                    raise AppError(404, 'NOT_FOUND', '岗位不存在')
                if row.revision != revision:
                    raise AppError(409, 'REVISION_CONFLICT', '岗位已变化，请刷新后重新确认')
                row.revision += 1
                repository.update(row, data)
                repository.replace_sources(row.id, source_ids)
            else:
                candidates = repository.find_matches(data['company'], data['title'])
                row = next((item for item in candidates if item.data == data), None)
                if row is None:
                    row = repository.add(data)
                else:
                    existing = repository.source_ids(row.id)
                    source_ids = [key for key in source_ids if key not in existing]
                    # 合并来源也算修改，旧编辑表单必须重新读取修订号。
                    if source_ids:
                        row.revision += 1
                repository.add_sources(row.id, source_ids)
            return self.serialize(repository, row)

    def delete(self, job_id: str):
        with self.session_factory.begin() as session:
            repository = JobRepository(session)
            row = repository.get(job_id)
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            repository.delete(row)
