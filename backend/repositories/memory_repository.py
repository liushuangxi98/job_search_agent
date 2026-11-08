"""已确认记忆的查询和写入；事务由业务服务管理。"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Memory


class MemoryRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, record_id: str):
        return self.session.get(Memory, record_id)

    def list(self, query='', offset=0, limit=100):
        statement = select(Memory)
        if query:
            statement = statement.where(Memory.data['content'].as_string().contains(query, autoescape=True))
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self.session.scalars(statement.order_by(Memory.created_at.desc(), Memory.id)
                                   .offset(offset).limit(limit)).all()
        return rows, total

    def save(self, data: dict, row=None):
        row = row if row is not None else Memory()
        row.data = data
        self.session.add(row)
        self.session.flush()
        return row

    def delete(self, row):
        self.session.delete(row)
