"""连接池和 Session 工厂；具体业务事务由 services 层管理。"""
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from .config import Settings
from .models import Base, Candidate
from .schemas import ProfileData


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
            connection.execute(select(Candidate.id).limit(1))

    def close(self):
        """释放连接池中的空闲连接，无返回值；不会删除数据库或清空资料。"""
        self.engine.dispose()


def merge_candidate_profile(engine):
    """升级旧库：先事务合并画像与条件，再删除旧列；重复执行可安全跳过。

    MySQL 的 ALTER TABLE 会隐式提交，因此先单独提交合并后的数据。
    若删除列失败，重跑时以 profile 已有值为准，避免旧条件覆盖后来修改的值。
    """
    if 'preferences' not in {column['name'] for column in inspect(engine).get_columns('candidate')}:
        return
    table = Table('candidate', MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        rows = connection.execute(select(table).with_for_update()).mappings().all()
        for row in rows:
            profile = {**(row['preferences'] or {}), **(row['profile'] or {})}
            ProfileData.model_validate(profile)
            connection.execute(table.update().where(table.c.id == row['id']).values(profile=profile))
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE candidate DROP COLUMN preferences'))


def initialize_database(settings: Settings, *, create_database=False):
    """补建缺失表和单用户初始记录，成功无返回值。

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
        merge_candidate_profile(database.engine)
        # begin() 在正常退出时提交、异常退出时回滚；仅缺失时插入初始画像记录。
        with database.session_factory.begin() as session:
            if session.get(Candidate, 1) is None:
                session.add(Candidate(id=1))
    finally:
        database.close()
