"""定义 SQLAlchemy 表模型及外键关系，不负责业务校验或事务提交。"""
from datetime import datetime, UTC
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    """返回不带时区标记的 UTC 时间，供 MySQL DATETIME 存储，不能按本地时间解读。"""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    """收集各表定义的 ORM 基类；数据库初始化根据它的 metadata 创建缺失表。"""
    pass


class Record:
    """复用字符串主键和 UTC 创建时间，本类自身不对应一张表。"""
    # Mapped 声明 ORM 属性，mapped_column 描述数据库列；默认值在插入时产生。
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    # DATETIME 未指定小数精度；未从数据库重读时，Python 中的微秒可能与持久化值不同。
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Candidate(Base):
    # 单用户应用只有一行资料，画像与条件共用 revision，避免相互覆盖旧状态。
    __tablename__ = 'candidate'
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(default=0)
    confirmed: Mapped[bool] = mapped_column(default=False)
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)


class Document(Record, Base):
    """保存上传文件的引用和元数据；原始文件在本地磁盘，size 的单位是字节。"""
    __tablename__ = 'documents'
    filename: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(20))
    media_type: Mapped[str] = mapped_column(String(100))
    stored_name: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))


class Job(Record, Base):
    # JSON 保存岗位字段；来源 ID 由 JobSource 维护，读取时才合并到返回数据。
    __tablename__ = 'jobs'
    revision: Mapped[int] = mapped_column(default=1)
    data: Mapped[dict] = mapped_column(JSON)


class JobSource(Base):
    """用联合主键避免重复关联；删除岗位或资料会级联删除关联行，不删除另一端。"""
    __tablename__ = 'job_sources'
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id', ondelete='CASCADE'), primary_key=True)


class Question(Record, Base):
    # document_id 为 None 表示没有资料来源；删除来源会级联删除相关题目行。
    __tablename__ = 'questions'
    document_id: Mapped[str | None] = mapped_column(ForeignKey('documents.id', ondelete='CASCADE'), nullable=True)
    data: Mapped[dict] = mapped_column(JSON)


class Memory(Record, Base):
    __tablename__ = 'memories'
    data: Mapped[dict] = mapped_column(JSON)


class Report(Record, Base):
    # 报告保存当次输入快照，之后修改岗位不会改变历史报告。
    __tablename__ = 'reports'
    kind: Mapped[str] = mapped_column(String(20))
    # 唯一约束防止同一运行保存多条报告；并发冲突仍可能抛出数据库异常。
    run_id: Mapped[str] = mapped_column(String(32), unique=True)
    is_demo: Mapped[bool] = mapped_column(Boolean)
    data: Mapped[dict] = mapped_column(JSON)
    snapshot: Mapped[dict] = mapped_column(JSON)
