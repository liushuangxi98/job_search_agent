"""画像和求职条件的校验、确认及修订控制。"""
from sqlalchemy.orm import Session, sessionmaker

from ..exceptions import AppError
from ..repositories.candidate_repository import CandidateRepository
from ..schemas import ProfileData, ProfileUpdate, current_profile_fields
from .material_validation import check_dict_missing


class CandidateService:
    def __init__(self, session_factory: sessionmaker[Session]):
        """接收应用的 Session 工厂；每次操作创建自己的 Session。"""
        self.session_factory = session_factory

    # staticmethod 让此转换方法无需服务实例即可调用，不会自动传入 self。
    @staticmethod
    def serialize(row):
        """将完整画像整理为含修订号、确认状态和 data 的字典。"""
        return {
            'revision': row.revision,
            'confirmed': row.confirmed,
            'data': ProfileData.model_validate(current_profile_fields(row.profile)).model_dump(),
        }

    def get(self):
        """读取完整画像，返回当前资料字典；缺失初始记录时抛 AppError。"""
        with self.session_factory() as session:
            row = CandidateRepository(session).get()
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            return self.serialize(row)

    @staticmethod
    def workbench_data(row):
        profile = ProfileData.model_validate(current_profile_fields(row.profile)).model_dump()
        missing = check_dict_missing(profile, ProfileData)
        return {
            'revision': row.revision, 'confirmed': row.confirmed and not missing,
            'profile': profile, 'missing': missing,
        }

    def get_all(self):
        with self.session_factory() as session:
            row = CandidateRepository(session).get()
            if row is None:
                raise AppError(503, 'DATABASE_NOT_INITIALIZED', '请先初始化数据库')
            return self.workbench_data(row)

    def delete(self, revision: int):
        """清空当前画像和求职条件，保留单用户记录以便重新填写。"""
        # 删除和保存共用行锁及修订号，过期页面不能清除刚更新的资料。
        with self.session_factory.begin() as session:
            repository = CandidateRepository(session)
            row = repository.get(lock=True)
            if row is None:
                raise AppError(503, 'DATABASE_NOT_INITIALIZED', '请先初始化数据库')
            if row.revision != revision:
                raise AppError(409, 'REVISION_CONFLICT', '个人资料已更新，请刷新后重新删除')
            # 使用标准空画像清除字段和提取依据，并取消确认状态。
            repository.update(row, ProfileData().model_dump(), confirmed=False)
            # 沿用更新方法递增版本，旧表单或聊天提案随后会发生版本冲突。
            return self.workbench_data(row)

    def confirm(self, profile: dict, revision: int):
        """个人资料须完整；校验在写入前完成，整份资料用一个事务。"""
        profile = ProfileData.model_validate(profile).model_dump()
        missing = check_dict_missing(profile, ProfileData)
        if missing:
            raise AppError(422, 'CANDIDATE_INCOMPLETE', '请补齐：' + '、'.join(item['label'] for item in missing))
        with self.session_factory.begin() as session:
            repository = CandidateRepository(session)
            row = repository.get(lock=True)
            if row is None:
                raise AppError(503, 'DATABASE_NOT_INITIALIZED', '请先初始化数据库')
            if row.revision != revision:
                raise AppError(409, 'REVISION_CONFLICT', '个人资料已更新，请重新读取后确认')
            repository.update(row, profile, confirmed=True)
            return self.workbench_data(row)

    def update(self, data: dict, revision: int, confirmed=None):
        """校验并保存包含求职条件的完整画像，返回更新后的资料及修订号。

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
            should_confirm = row.confirmed if confirmed is None else confirmed
            data = ProfileUpdate(
                revision=revision,
                confirmed=should_confirm,
                data=data,
            ).data.model_dump()
            # 已确认资料的后续修改也必须保持完整，避免聊天把核心字段清空。
            if should_confirm:
                missing = check_dict_missing(data, ProfileData)
                if missing:
                    raise AppError(422, 'CANDIDATE_INCOMPLETE', '请补齐：' + '、'.join(item['label'] for item in missing))
            repository.update(row, data, confirmed)
            # 即使在 with 内 return，也会先退出上下文并提交；提交失败则不会返回成功结果。
            return self.serialize(row)
