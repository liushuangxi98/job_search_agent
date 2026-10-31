"""只追加和读取报告，不随当前资料变更改写报告。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Report


class ReportRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, report_id: str):
        return self.session.get(Report, report_id)

    def get_by_run(self, run_id: str):
        return self.session.scalar(select(Report).where(Report.run_id == run_id))

    def list(self):
        return self.session.scalars(select(Report).order_by(Report.created_at.desc())).all()

    def add(self, **values):
        row = Report(**values)
        self.session.add(row)
        self.session.flush()
        return row
