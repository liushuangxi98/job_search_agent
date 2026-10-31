"""画像和求职条件的校验、确认及修订控制。"""
from sqlalchemy.orm import Session, sessionmaker

from ..exceptions import AppError
from ..repositories.candidate_repository import CandidateRepository
from ..schemas import Preferences, ProfileData, ProfileUpdate

SCHEMAS = {'profile': ProfileData, 'preferences': Preferences}


class CandidateService:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    @staticmethod
    def serialize(row, section):
        return {
            'revision': row.revision,
            'confirmed': row.confirmed,
            'data': SCHEMAS[section].model_validate(getattr(row, section)).model_dump(),
        }

    def get(self, section: str):
        with self.session_factory() as session:
            row = CandidateRepository(session).get()
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            return self.serialize(row, section)

    def update(self, section: str, data: dict, revision: int, confirmed=None):
        with self.session_factory.begin() as session:
            repository = CandidateRepository(session)
            # 检查和更新放在同一行锁内，避免两个请求同时使用同一个旧版本。
            row = repository.get(lock=True)
            if row is None:
                raise AppError(503, 'DATABASE_NOT_INITIALIZED', '请先初始化数据库')
            if row.revision != revision:
                raise AppError(409, 'REVISION_CONFLICT', '资料已发生变化，请刷新后重新确认')
            if section == 'profile':
                data = ProfileUpdate(
                    revision=revision,
                    confirmed=row.confirmed if confirmed is None else confirmed,
                    data=data,
                ).data.model_dump()
            data = SCHEMAS[section].model_validate(data).model_dump()
            repository.update(row, section, data, confirmed)
            return self.serialize(row, section)
