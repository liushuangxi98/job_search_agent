"""题目和已确认记忆的普通存取，不包含检索或记忆推理。"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Memory, Question

MODELS = {'questions': Question, 'memories': Memory}


class KnowledgeRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, kind: str, record_id: str):
        return self.session.get(MODELS[kind], record_id)

    def list(self, kind: str, query='', offset=0, limit=100):
        model = MODELS[kind]
        statement = select(model)
        if query:
            field = 'question' if kind == 'questions' else 'content'
            statement = statement.where(model.data[field].as_string().contains(query, autoescape=True))
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self.session.scalars(
            statement.order_by(model.created_at.desc(), model.id).offset(offset).limit(limit)
        ).all()
        return rows, total

    def save(self, kind: str, data: dict, row=None):
        row = row if row is not None else MODELS[kind]()
        row.data = data
        if kind == 'questions':
            row.document_id = data['document_id']
        self.session.add(row)
        self.session.flush()
        return row

    def delete(self, row):
        self.session.delete(row)
