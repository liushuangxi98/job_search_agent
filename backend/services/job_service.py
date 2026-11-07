"""岗位校验、重复导入合并、来源关联和修订控制。"""
from copy import deepcopy

from sqlalchemy.orm import Session, sessionmaker

from ..exceptions import AppError
from ..repositories.document_repository import DocumentRepository
from ..repositories.job_repository import JobRepository
from ..schemas import JobData, current_job_fields
from .material_validation import FOLDED_JOB_MESSAGE, check_dict_missing


class JobService:
    def __init__(self, session_factory: sessionmaker[Session]):
        """接收 Session 工厂，岗位与来源关联在同一次业务事务中处理。"""
        self.session_factory = session_factory

    @staticmethod
    def serialize(repository, row):
        """返回带原文依据的岗位；旧来源关联在读取时转换，创建时间按 UTC 展示。"""
        data = JobData.model_validate(current_job_fields(deepcopy(row.data), repository.source_ids(row.id))).model_dump()
        return {
            'id': row.id,
            'revision': row.revision,
            'created_at': row.created_at.isoformat() + 'Z',
            'data': data,
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

        更新时必须提供读取时的 revision，evidence 是更新后完整的依据列表。
        数据校验、来源缺失或修订冲突会抛出异常，岗位与来源关联一起回滚。
        """
        data = JobData.model_validate(data).model_dump()
        if data['complete'] is None:
            raise AppError(422, 'JOB_TEXT_FOLDED', FOLDED_JOB_MESSAGE)
        missing = check_dict_missing(data, JobData)
        if missing:
            raise AppError(422, 'JOB_INCOMPLETE', '请补齐岗位信息：' + '、'.join(item['label'] for item in missing))
        source_ids = list({item['document_id'] for item in data['evidence'] if item['document_id']})
        with self.session_factory.begin() as session:
            repository = JobRepository(session)
            documents = DocumentRepository(session)
            # 先确认全部来源有效；任何一步失败都会回滚岗位和来源关联。
            for source_id in source_ids:
                source = documents.get(source_id)
                if source is None:
                    raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
                if source.kind != 'image':
                    raise AppError(422, 'EVIDENCE_SOURCE_KIND', '岗位依据只能引用已上传的招聘截图')
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
                row = None
                for item in candidates:
                    existing = self.serialize(repository, item)['data']
                    same_fields = {k: v for k, v in existing.items() if k != 'evidence'} == {
                        k: v for k, v in data.items() if k != 'evidence'
                    }
                    existing_sources = {entry['document_id'] for entry in existing['evidence'] if entry['document_id']}
                    if same_fields and existing_sources == set(source_ids):
                        row = item
                        # 同一截图重复导入可补充摘录，另一张截图单独保留岗位记录。
                        merged = existing['evidence'] + [entry for entry in data['evidence'] if entry not in existing['evidence']]
                        if merged != existing['evidence']:
                            data['evidence'] = merged
                            data = JobData.model_validate(data).model_dump()
                            row.revision += 1
                            repository.update(row, data)
                        break
                if row is None:
                    row = repository.add(data)
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
