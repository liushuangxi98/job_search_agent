"""岗位校验、重复导入合并、来源关联和修订控制。"""
from copy import deepcopy

from sqlalchemy.orm import Session, sessionmaker

from ..exceptions import AppError
from ..repositories.document_repository import DocumentRepository
from ..repositories.job_repository import JobRepository
from ..schemas import JobData


class JobService:
    def __init__(self, session_factory: sessionmaker[Session]):
        """接收 Session 工厂，岗位与来源关联在同一次业务事务中处理。"""
        self.session_factory = session_factory

    @staticmethod
    def serialize(repository, row):
        """用当前仓库读取来源 ID，返回岗位字典；row 为 ORM 行，创建时间按 UTC 展示。"""
        return {
            'id': row.id,
            'revision': row.revision,
            'created_at': row.created_at.isoformat() + 'Z',
            'data': {**deepcopy(row.data), 'source_ids': repository.source_ids(row.id)},
        }

    def get(self, job_id: str):
        """按 job_id 返回岗位和来源字典；记录不存在时抛 AppError，而非返回 None。"""
        with self.session_factory() as session:
            repository = JobRepository(session)
            row = repository.get(job_id)
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            return self.serialize(repository, row)

    def list(self, query='', offset=0, limit=100):
        """按公司、岗位名或城市搜索，返回 items 和分页前总数 total。

        offset 为跳过的条数，limit 为最多条数；无匹配时 items 为空列表。
        """
        with self.session_factory() as session:
            repository = JobRepository(session)
            rows, total = repository.list(query, offset, limit)
            return {'items': [self.serialize(repository, row) for row in rows], 'total': total}

    def save(self, data: dict, job_id=None, revision=None):
        """保存岗位并返回其字典；job_id 为空时新增或合并完全相同的岗位。

        更新时必须提供读取时的 revision，source_ids 是更新后保留的完整来源列表。
        数据校验、来源缺失或修订冲突会抛出异常，岗位与来源关联一起回滚。
        """
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
                # 公司和岗位名只缩小候选范围；其余字段也完全相同时才复用已有岗位。
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
        """删除岗位及其来源关联，成功无返回值；岗位缺失抛 AppError，历史报告不改写。"""
        with self.session_factory.begin() as session:
            repository = JobRepository(session)
            row = repository.get(job_id)
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            repository.delete(row)
