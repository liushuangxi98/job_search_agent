"""连接池和 Session 工厂；具体业务事务由 services 层管理。"""
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from .config import Settings
from .models import Base, Candidate


def make_engine(settings: Settings, *, administrative=False):
    url = settings.database_url._replace(database=None) if administrative else settings.database_url
    return create_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={'connect_timeout': 3, 'read_timeout': 10, 'write_timeout': 10},
    )


class Database:
    def __init__(self, settings: Settings):
        self.engine = make_engine(settings)
        # 工厂可以共享；由它创建的 Session 只能供一次操作使用。
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def ping(self):
        with self.engine.connect() as connection:
            connection.execute(text('SELECT 1'))
            connection.execute(select(Candidate.id).limit(1))

    def close(self):
        self.engine.dispose()


def initialize_database(settings: Settings, *, create_database=False):
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
        with database.session_factory.begin() as session:
            if session.get(Candidate, 1) is None:
                session.add(Candidate(id=1))
    finally:
        database.close()
