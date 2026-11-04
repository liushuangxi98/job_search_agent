"""画像和求职条件的校验、确认及修订控制。"""
from sqlalchemy.orm import Session, sessionmaker

from ..exceptions import AppError
from ..repositories.candidate_repository import CandidateRepository
from ..schemas import Preferences, ProfileData, ProfileUpdate
from .material_validation import candidate_missing

SCHEMAS = {'profile': ProfileData, 'preferences': Preferences}


class CandidateService:
    def __init__(self, session_factory: sessionmaker[Session]):
        """接收应用的 Session 工厂；每次操作创建自己的 Session。"""
        self.session_factory = session_factory

    # staticmethod 让此转换方法无需服务实例即可调用，不会自动传入 self。
    @staticmethod
    def serialize(row, section):
        """将候选人行的 profile 或 preferences 整理为含修订号、确认状态和 data 的字典。"""
        return {
            'revision': row.revision,
            'confirmed': row.confirmed,
            'data': SCHEMAS[section].model_validate(getattr(row, section)).model_dump(),
        }

    def get(self, section: str):
        """读取 profile 或 preferences，返回当前资料字典；缺失初始记录时抛 AppError。"""
        with self.session_factory() as session:
            row = CandidateRepository(session).get()
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            return self.serialize(row, section)

    @staticmethod
    def combined(row):
        profile = ProfileData.model_validate(row.profile).model_dump()
        preferences = Preferences.model_validate(row.preferences).model_dump()
        missing = candidate_missing(profile, preferences)
        return {
            'revision': row.revision, 'confirmed': row.confirmed and not missing,
            'profile': profile, 'preferences': preferences, 'missing': missing,
        }

    def get_all(self):
        with self.session_factory() as session:
            row = CandidateRepository(session).get()
            if row is None:
                raise AppError(503, 'DATABASE_NOT_INITIALIZED', '请先初始化数据库')
            return self.combined(row)

    def confirm(self, profile: dict, preferences: dict, revision: int):
        """个人资料须完整；校验在写入前完成，两部分只用一个事务。"""
        profile = ProfileData.model_validate(profile).model_dump()
        preferences = Preferences.model_validate(preferences).model_dump()
        missing = candidate_missing(profile, preferences)
        if missing:
            raise AppError(422, 'CANDIDATE_INCOMPLETE', '请补齐：' + '、'.join(item['label'] for item in missing))
        with self.session_factory.begin() as session:
            repository = CandidateRepository(session)
            row = repository.get(lock=True)
            if row is None:
                raise AppError(503, 'DATABASE_NOT_INITIALIZED', '请先初始化数据库')
            if row.revision != revision:
                raise AppError(409, 'REVISION_CONFLICT', '个人资料已更新，请重新读取后确认')
            repository.confirm(row, profile, preferences)
            return self.combined(row)

    def update(self, section: str, data: dict, revision: int, confirmed=None):
        """校验并保存画像或条件，返回更新后的资料及修订号。

        revision 必须是调用方读取时的值；confirmed=None 保留当前确认状态。
        修订冲突、未初始化或资料校验失败会抛出异常，事务内的修改不会提交。
        """
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
            # 已确认资料的后续修改也必须保持完整，避免聊天把核心字段清空。
            should_confirm = row.confirmed if confirmed is None else confirmed
            if should_confirm:
                profile = data if section == 'profile' else row.profile
                preferences = data if section == 'preferences' else row.preferences
                missing = candidate_missing(profile, preferences)
                if missing:
                    raise AppError(422, 'CANDIDATE_INCOMPLETE', '请补齐：' + '、'.join(item['label'] for item in missing))
            repository.update(row, section, data, confirmed)
            # 即使在 with 内 return，也会先退出上下文并提交；提交失败则不会返回成功结果。
            return self.serialize(row, section)
