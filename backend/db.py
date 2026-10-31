from datetime import datetime, UTC
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import Settings


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Record:
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Candidate(Base):
    __tablename__ = 'candidate'
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(default=0)
    confirmed: Mapped[bool] = mapped_column(default=False)
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)


class Document(Record, Base):
    __tablename__ = 'documents'
    filename: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(20))
    media_type: Mapped[str] = mapped_column(String(100))
    stored_name: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))


class Job(Record, Base):
    __tablename__ = 'jobs'
    revision: Mapped[int] = mapped_column(default=1)
    data: Mapped[dict] = mapped_column(JSON)


class JobSource(Base):
    __tablename__ = 'job_sources'
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id', ondelete='CASCADE'), primary_key=True)


class Question(Record, Base):
    __tablename__ = 'questions'
    document_id: Mapped[str | None] = mapped_column(ForeignKey('documents.id', ondelete='CASCADE'), nullable=True)
    data: Mapped[dict] = mapped_column(JSON)


class Memory(Record, Base):
    __tablename__ = 'memories'
    data: Mapped[dict] = mapped_column(JSON)


class Report(Record, Base):
    __tablename__ = 'reports'
    kind: Mapped[str] = mapped_column(String(20))
    run_id: Mapped[str] = mapped_column(String(32), unique=True)
    is_demo: Mapped[bool] = mapped_column(Boolean)
    data: Mapped[dict] = mapped_column(JSON)
    snapshot: Mapped[dict] = mapped_column(JSON)


def make_engine(settings: Settings, *, administrative=False):
    url = settings.database_url._replace(database=None) if administrative else settings.database_url
    return create_engine(url, pool_pre_ping=True, hide_parameters=True,
                         connect_args={'connect_timeout': 3, 'read_timeout': 10, 'write_timeout': 10})


def initialize_database(settings: Settings, *, create_database=False):
    if create_database:
        admin = make_engine(settings, administrative=True)
        try:
            with admin.begin() as connection:
                connection.execute(text(f'CREATE DATABASE IF NOT EXISTS `{settings.mysql_database}` CHARACTER SET utf8mb4'))
        finally:
            admin.dispose()
    engine = make_engine(settings)
    try:
        Base.metadata.create_all(engine)
        with sessionmaker(engine).begin() as session:
            if session.get(Candidate, 1) is None:
                session.add(Candidate(id=1))
    finally:
        engine.dispose()
