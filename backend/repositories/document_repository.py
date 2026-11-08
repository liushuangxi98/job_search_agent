"""只存取文件元数据；本地文件由 DocumentService 管理。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Document


class DocumentRepository:
    """服务提供 Session（数据库会话）并负责业务校验、冲突处理及事务提交或回滚。
    flush 发送会话内所有待写变更而不提交，SQLAlchemy 异常向上传播。
    """
    def __init__(self, session: Session):
        """使用调用方当前事务的 session，不新建会话或结束事务。"""
        self.session = session

    def get(self, document_id: str):
        """按 document_id 返回资料元数据记录对象；不存在时返回 None。"""
        return self.session.get(Document, document_id)

    def add(self, **values):
        """用 values 中的文件名、存储名、类型、大小及校验摘要等 Document 字段新增元数据。
        flush 后返回记录对象；本方法不保存或校验本地文件。
        """
        row = Document(**values)
        self.session.add(row)
        self.session.flush()
        return row

    def list(self, kind=None):
        """按创建时间从新到旧返回元数据记录列表，无记录时返回空列表。
        kind 筛选 image（图片）或 resume（简历），None 或空字符串表示全部。
        """
        query = select(Document).order_by(Document.created_at.desc())
        if kind:
            query = query.where(Document.kind == kind)
        return self.session.scalars(query).all()

    def delete(self, row: Document):
        """将当前会话中的资料记录 row 标记删除，返回 None，后续 flush 才执行删除。
        数据库按外键规则连带删除岗位来源关联；岗位、记忆和本地文件不在此删除。
        """
        self.session.delete(row)
