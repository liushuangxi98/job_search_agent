"""连接池和 Session 工厂；具体业务事务由 services 层管理。"""
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from .config import Settings
from .models import Base, Profile, ProfileState, Job, JobSource, Document
from .schemas import ProfileData, JobData, current_profile_fields, current_job_fields


def make_engine(settings: Settings, *, administrative=False):
    """按配置返回连接池引擎，创建对象时不立即连接 MySQL。

    administrative=True 时不选项目库，供建库前使用；连接及读写超时单位为秒。
    实际连接或执行 SQL 时，连接失败与数据库错误会向调用方抛出。
    """
    url = settings.database_url._replace(database=None) if administrative else settings.database_url
    return create_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={'connect_timeout': 3, 'read_timeout': 10, 'write_timeout': 10},
    )


class Database:
    """持有应用级连接池和 Session 工厂，不持有跨请求共享的 Session。"""
    def __init__(self, settings: Settings):
        """根据 settings 创建引擎和 Session 工厂，尚不检查数据库是否可用。"""
        self.engine = make_engine(settings)
        # 工厂可以共享；由它创建的 Session 只能供一次操作使用。
        # expire_on_commit=False 保留提交后的已加载字段，避免读字段时意外再查库。
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def ping(self):
        """检查连接和候选人表是否可查询；成功无返回值，数据库错误向上抛出。"""
        with self.engine.connect() as connection:
            connection.execute(text('SELECT 1'))
            connection.execute(select(Profile.id).limit(1))

    def close(self):
        """释放连接池中的空闲连接，无返回值；不会删除数据库或清空资料。"""
        self.engine.dispose()


def migrate_profile_fields(engine):
    """升级当前画像：合并旧条件、清理退役字段，历史报告仍保留原快照。

    MySQL 的 ALTER TABLE 会隐式提交，因此先单独提交合并后的数据。
    若删除列失败，重跑时以 profile 已有值为准，避免旧条件覆盖后来修改的值。
    """
    table = Table('candidate', MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        rows = connection.execute(select(table).with_for_update()).mappings().all()
        for row in rows:
            profile = current_profile_fields({**(row.get('preferences') or {}), **(row['profile'] or {})})
            ProfileData.model_validate(profile)
            if profile != row['profile']:
                connection.execute(table.update().where(table.c.id == row['id']).values(profile=profile))
    if 'preferences' in table.c:
        with engine.begin() as connection:
            connection.execute(text('ALTER TABLE candidate DROP COLUMN preferences'))


def migrate_job_evidence(database):
    """将旧岗位来源转换为依据；多来源或非截图来源须人工整理，失败时整批回滚。"""
    with database.session_factory.begin() as session:
        for row in session.scalars(select(Job).with_for_update()).all():
            ids = list(session.scalars(select(JobSource.document_id).where(JobSource.job_id == row.id)))
            if any(session.get(Document, key).kind != 'image' for key in ids):
                raise ValueError('旧岗位存在非截图来源，请先整理后迁移')
            data = current_job_fields(row.data, ids)
            JobData.model_validate(data)
            if data != row.data:
                row.data = data


def initialize_profile_storage(database):
    """为已有整数主键开启自增；保留现有画像的 ID、内容与修订号。"""
    engine = database.engine
    if engine.dialect.name == 'mysql':
        columns = {col['name']: col for col in inspect(engine).get_columns('candidate')}
        # 旧安装的状态列没有数据库默认值，删列后首次保存才能省略它。
        if 'confirmed' in columns:
            with engine.begin() as conn:
                conn.execute(text('ALTER TABLE candidate DROP COLUMN confirmed'))
        id_column = columns['id']
        if not id_column.get('autoincrement'):
            with engine.begin() as conn:
                conn.execute(text('ALTER TABLE candidate MODIFY COLUMN id INT NOT NULL AUTO_INCREMENT'))

    ProfileState.__table__.create(engine, checkfirst=True)
    with database.session_factory.begin() as session:
        state = session.get(ProfileState, 'current')
        if state is not None:
            return
        state = ProfileState(name='current', empty_revision=0)
        session.add(state)
        # 旧版首次初始化或删除后会留一条完全空白的占位记录。实际填写过的资料均保留。
        row = session.scalars(select(Profile).with_for_update()).one_or_none()
        if row is not None and row.profile in ({}, ProfileData().model_dump()):
            state.empty_revision = row.revision
            session.delete(row)


def initialize_database(settings: Settings, *, create_database=False):
    """补建缺失表并升级旧结构，首次保存前画像表为空。

    create_database=True 时先尝试建库；旧版个人资料会合并到 profile。
    连接、权限或 SQL 执行失败时抛出数据库异常，连接池仍会被释放。
    """
    if create_database:
        admin = make_engine(settings, administrative=True)
        try:
            with admin.begin() as connection:
                # 库名已由 Settings 限制为字母、数字及下划线。
                connection.execute(text(
                    f'CREATE DATABASE IF NOT EXISTS `{settings.mysql_database}` CHARACTER SET utf8mb4'
                ))
        finally:
            admin.dispose()
    database = Database(settings)
    try:
        Base.metadata.create_all(database.engine)
        migrate_profile_fields(database.engine)
        migrate_job_evidence(database)
        initialize_profile_storage(database)
    finally:
        database.close()
