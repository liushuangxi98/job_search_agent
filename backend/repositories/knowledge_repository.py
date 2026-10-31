"""题目和已确认记忆的普通存取，不包含检索或记忆推理。"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Memory, Question

MODELS = {'questions': Question, 'memories': Memory}


class KnowledgeRepository:
    """服务提供 Session（数据库会话）并负责业务校验、冲突处理及事务提交或回滚。
    flush 发送会话内所有待写变更而不提交，SQLAlchemy 异常向上传播。
    """
    def __init__(self, session: Session):
        """使用调用方当前事务的 session，不新建会话或结束事务。"""
        self.session = session

    def get(self, kind: str, record_id: str):
        """按 record_id 返回题目或记忆记录对象，不存在时返回 None。
        kind 为 questions（题目）或 memories（记忆）；其他值会抛出 KeyError。
        """
        return self.session.get(MODELS[kind], record_id)

    def list(self, kind: str, query='', offset=0, limit=100):
        """按 kind 返回 (rows, total)；kind 为 questions（题目）或 memories（记忆），其他值抛出 KeyError。
        query 匹配题目 question 或记忆 content，为空时不筛选；记录列表 rows 按创建时间倒序、ID 升序排列。
        offset 是跳过条数，limit 是最多条数；total 为分页前的匹配数，空页仍可能有非零 total。
        无匹配时返回 ([], 0)。
        """
        model = MODELS[kind]
        statement = select(model)
        if query:
            field = 'question' if kind == 'questions' else 'content'
            # 从 JSON 取字符串后做子串匹配；autoescape 让 %、_ 及转义字符按字面值查找。
            statement = statement.where(model.data[field].as_string().contains(query, autoescape=True))
        total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self.session.scalars(
            statement.order_by(model.created_at.desc(), model.id).offset(offset).limit(limit)
        ).all()
        return rows, total

    def save(self, kind: str, data: dict, row=None):
        """保存已校验的 data，flush 后返回记录对象；kind 为 questions（题目）或 memories（记忆）。
        row=None 时新建，否则整体替换已有记录的 data；row 应属于当前会话且与 kind 对应。
        题目还同步 document_id 来源关联，None 表示无来源，字段缺失会抛出 KeyError。
        本方法不更新检索向量。
        """
        row = row if row is not None else MODELS[kind]()
        row.data = data
        if kind == 'questions':
            row.document_id = data['document_id']
        self.session.add(row)
        self.session.flush()
        return row

    def delete(self, row):
        """将当前会话中的题目或记忆记录 row 标记删除，返回 None，后续 flush 才执行删除。
        原资料及检索向量不在此删除。
        """
        self.session.delete(row)
