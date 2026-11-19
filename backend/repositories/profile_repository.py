"""候选人画像和条件的数据库读写。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Profile, ProfileState


class ProfileRepository:
    """服务提供 Session（数据库会话）并负责业务校验、冲突处理及事务提交或回滚。
    flush 发送会话内所有待写变更而不提交，SQLAlchemy 异常向上传播。
    """
    def __init__(self, session: Session):
        """使用调用方当前事务的 session，不新建会话或结束事务。"""
        self.session = session

    def get(self, *, lock=False):
        """空表时只构造内存中的空表单；直到调用 update 才真正插入。"""
        query = select(Profile)
        if lock:
            # 空表没有画像行可锁。先锁修订号记录，让两个标签页的首次保存顺序执行。
            self._state(lock=True)
            query = query.with_for_update()
        row = self.session.scalars(query).one_or_none()
        if row is not None:
            return row
        state = self._state()
        return Profile(revision=state.empty_revision, profile={})

    def _state(self, *, lock=False):
        query = select(ProfileState).where(ProfileState.name == 'current')
        return self.session.scalars(query.with_for_update() if lock else query).one()

    def update(self, row: Profile, data: dict):
        """用 data 整体替换当前会话记录 row 的完整画像，递增共用修订号并 flush，返回 None。
        """
        row.profile = data
        row.revision += 1
        # 从空表单首次保存时 INSERT；已加载的持久化实例只更新原行。
        self.session.add(row)
        self.session.flush()

    def delete(self, row):
        if row.id is not None:
            state = self._state()
            state.empty_revision = row.revision + 1
            self.session.delete(row)
            self.session.flush()
