"""岗位及来源关联的 SQL 操作。"""
from __future__ import annotations

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..models import Job, JobSource


class JobRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, job_id: str, *, lock=False):
        query = select(Job).where(Job.id == job_id)
        return self.session.scalar(query.with_for_update() if lock else query)

    def find_matches(self, company: str, title: str):
        return self.session.scalars(select(Job).where(
            Job.data['company'].as_string() == company,
            Job.data['title'].as_string() == title,
        ).with_for_update()).all()

    def list(self, query='', offset=0, limit=100):
        statement = select(Job)
        if query:
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
        row = Job(data=data)
        self.session.add(row)
        self.session.flush()
        # MySQL 的 DATETIME 会调整微秒精度，返回数据库实际保存的时间。
        self.session.refresh(row)
        return row

    def update(self, row: Job, data: dict):
        row.data = data
        self.session.flush()

    def source_ids(self, job_id: str):
        return list(self.session.scalars(
            select(JobSource.document_id).where(JobSource.job_id == job_id)
        ))

    def add_sources(self, job_id: str, source_ids: list[str]):
        self.session.add_all(JobSource(job_id=job_id, document_id=key) for key in source_ids)
        self.session.flush()

    def replace_sources(self, job_id: str, source_ids: list[str]):
        self.session.execute(delete(JobSource).where(JobSource.job_id == job_id))
        self.add_sources(job_id, source_ids)

    def delete(self, row: Job):
        self.session.delete(row)
