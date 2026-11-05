"""候选人画像和条件的数据库读写。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Candidate


class CandidateRepository:
    """服务提供 Session（数据库会话）并负责业务校验、冲突处理及事务提交或回滚。
    flush 发送会话内所有待写变更而不提交，SQLAlchemy 异常向上传播。
    """
    def __init__(self, session: Session):
        """使用调用方当前事务的 session，不新建会话或结束事务。"""
        self.session = session

    def get(self, *, lock=False):
        """返回 ID 为 1 的画像和条件记录对象；不存在时返回 None。
        lock=True 请求数据库加锁，供服务检查修订号后更新；锁在当前事务提交或回滚时释放。
        """
        query = select(Candidate).where(Candidate.id == 1)
        if lock:
            query = query.with_for_update()
        return self.session.scalar(query)

    def update(self, row: Candidate, data: dict, confirmed=None):
        """用 data 整体替换当前会话记录 row 的完整画像，递增共用修订号并 flush，返回 None。
        confirmed=None 时保留画像确认状态，否则一并更新确认状态。
        """
        row.profile = data
        if confirmed is not None:
            row.confirmed = confirmed
        row.revision += 1
        self.session.flush()
