"""岗位及来源关联的 SQL 操作。"""
from __future__ import annotations

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..models import Job, JobSource


class JobRepository:
    """服务提供 Session（数据库会话）并负责业务校验、冲突处理及事务提交或回滚。
    flush 发送会话内所有待写变更而不提交，SQLAlchemy 异常向上传播。
    """
    def __init__(self, session: Session):
        """使用调用方当前事务的 session，不新建会话或结束事务。"""
        self.session = session

    def get(self, job_id: str, *, lock=False):
        """按 job_id 返回岗位记录对象；不存在时返回 None。
        lock=True 请求数据库加锁，供服务检查修订号后更新；锁在当前事务提交或回滚时释放。
        """
        query = select(Job).where(Job.id == job_id)
        return self.session.scalar(query.with_for_update() if lock else query)

    def find_matches(self, company: str, title: str):
        """用 company 和 title 对岗位 JSON 中的公司和岗位名称做等值查询，返回记录列表，无匹配时为空。
        查询请求数据库加锁，供服务进一步比对完整数据；锁在当前事务提交或回滚时释放。
        实际锁范围由数据库的查询方式和事务设置决定，本方法不保证岗位唯一。
        """
        return self.session.scalars(select(Job).where(
            Job.data['company'].as_string() == company,
            Job.data['title'].as_string() == title,
        ).with_for_update()).all()

    def list(self, query='', offset=0, limit=100):
        """按 query 筛选公司、岗位名称或城市，按创建时间倒序、ID 升序返回 (rows, total)。
        rows 是跳过 offset 条后最多 limit 条的岗位记录列表；total 是筛选后分页前的总数。
        无匹配时返回 ([], 0)；当前页无记录时 total 仍可能大于零，query 为空时不筛选。
        """
        statement = select(Job)
        if query:
            # 从 JSON 取字符串后做子串匹配；autoescape 让 %、_ 及转义字符按字面值查找。
            statement = statement.where(or_(*[
                Job.data[field].as_string().contains(query, autoescape=True)
                for field in ('company', 'title', 'city')
            ]))
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self.session.scalars(
            statement.order_by(Job.created_at.desc(), Job.id).offset(offset).limit(limit)
        ).all()
        return rows, total

    def add(self, data: dict):
        """用已校验的完整岗位及 evidence 新建记录，flush 并重读后返回。"""
        row = Job(data=data)
        self.session.add(row)
        self.session.flush()
        # MySQL 的 DATETIME 会调整微秒精度，返回数据库实际保存的时间。
        self.session.refresh(row)
        return row

    def update(self, row: Job, data: dict):
        """用 data 整体替换当前会话岗位记录 row 的内容，flush 后返回 None。
        修订号检查与递增、截图关联修改均由服务另行处理。
        """
        row.data = data
        self.session.flush()

    def source_ids(self, job_id: str):
        """返回 job_id 对应的来源资料 ID 列表；无关联或岗位不存在时均返回空列表。"""
        return list(self.session.scalars(
            select(JobSource.document_id).where(JobSource.job_id == job_id)
        ))

    def add_sources(self, job_id: str, source_ids: list[str]):
        """为 job_id 追加 source_ids 中的资料关联，flush 后返回 None。
        服务应先校验来源存在并去重；这里只新增关联，不保存资料，也不递增岗位修订号。
        """
        self.session.add_all(JobSource(job_id=job_id, document_id=key) for key in source_ids)
        self.session.flush()

    def replace_sources(self, job_id: str, source_ids: list[str]):
        """在当前事务内删除 job_id 的全部旧来源关联，再由 add_sources 写入 source_ids，返回 None。
        空列表会清空关联，资料本身保留；服务负责来源校验、去重及岗位修订号。
        """
        self.session.execute(delete(JobSource).where(JobSource.job_id == job_id))
        self.add_sources(job_id, source_ids)

    def delete(self, row: Job):
        """将当前会话中的岗位记录 row 标记删除，返回 None，后续 flush 才执行删除。
        数据库按外键规则连带删除该岗位的来源关联，保留来源资料和历史报告快照。
        """
        self.session.delete(row)
