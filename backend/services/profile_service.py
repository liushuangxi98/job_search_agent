"""画像和求职条件的保存、删除及修订控制。"""
from sqlalchemy.orm import Session, sessionmaker

from ..exceptions import AppError
from ..repositories.profile_repository import ProfileRepository
from ..schemas import ProfileData, current_profile_fields
from .material_validation import check_dict_missing


class ProfileService:
    def __init__(self, session_factory: sessionmaker[Session]):
        """接收应用的 Session 工厂；每次操作创建自己的 Session。"""
        self.session_factory = session_factory

    @staticmethod
    def serialize(row):
        profile = ProfileData.model_validate(current_profile_fields(row.profile)).model_dump()
        missing = check_dict_missing(profile, ProfileData)
        return {
            'id': row.id, 'revision': row.revision,
            'profile': profile, 'missing': missing,
        }

    def get(self):
        with self.session_factory() as session:
            row = ProfileRepository(session).get()
            return self.serialize(row)

    def delete(self, revision: int):
        """删除当前画像行并返回可重新填写的空表单。"""
        # 删除和保存共用行锁及修订号，过期页面不能清除刚更新的资料。
        with self.session_factory.begin() as session:
            repository = ProfileRepository(session)
            row = repository.get(lock=True)
            if row.revision != revision:
                raise AppError(409, 'REVISION_CONFLICT', '个人资料已更新，请刷新后重新删除')
            repository.delete(row)
            return self.serialize(repository.get())

    def save(self, data: dict, revision: int):
        """在同一事务内校验修订号和必填项，保存后返回当前画像。"""
        with self.session_factory.begin() as session:
            repository = ProfileRepository(session)
            row = repository.get(lock=True)
            if row.revision != revision:
                raise AppError(409, 'REVISION_CONFLICT', '个人资料已更新，请刷新后保存')
            profile = ProfileData.model_validate(data).model_dump()
            missing = check_dict_missing(profile, ProfileData)
            if missing:
                raise AppError(422, 'CANDIDATE_INCOMPLETE', '请补齐：' + '、'.join(item['label'] for item in missing))
            repository.update(row, profile)
            return self.serialize(row)
