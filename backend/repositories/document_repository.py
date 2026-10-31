"""只存取文件元数据；本地文件由 DocumentService 管理。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Document


class DocumentRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, document_id: str):
        return self.session.get(Document, document_id)

    def add(self, **values):
        row = Document(**values)
        self.session.add(row)
        self.session.flush()
        return row

    def list(self, kind=None):
        query = select(Document).order_by(Document.created_at.desc())
        if kind:
            query = query.where(Document.kind == kind)
        return self.session.scalars(query).all()

    def delete(self, row: Document):
        self.session.delete(row)
