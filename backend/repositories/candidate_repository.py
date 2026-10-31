"""候选人资料的数据库读写。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Candidate


class CandidateRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, *, lock=False):
        query = select(Candidate).where(Candidate.id == 1)
        if lock:
            query = query.with_for_update()
        return self.session.scalar(query)

    def update(self, row: Candidate, section: str, data: dict, confirmed=None):
        setattr(row, section, data)
        if confirmed is not None:
            row.confirmed = confirmed
        row.revision += 1
        self.session.flush()
