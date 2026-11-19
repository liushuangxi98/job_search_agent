"""定义 SQLAlchemy 表模型及外键关系，不负责业务校验或事务提交。"""
from datetime import datetime, UTC
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String
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


class Profile(Base):
    # 单用户最多保存一份画像，首次保存由数据库分配 ID。
    __tablename__ = 'candidate'
    __table_args__ = {'sqlite_autoincrement': True}
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    revision: Mapped[int] = mapped_column(default=0)
    profile: Mapped[dict] = mapped_column(JSON, default=dict)


class ProfileState(Base):
    """只保存空表的修订号；同一把行锁也用于串行化并发首次保存。"""
    __tablename__ = 'candidate_state'
    name: Mapped[str] = mapped_column(String(16), primary_key=True)
    empty_revision: Mapped[int] = mapped_column(default=0)


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
    # JSON 保存岗位及 evidence；JobSource 为其中的单张截图维护存在性关联。
    __tablename__ = 'jobs'
    revision: Mapped[int] = mapped_column(default=1)
    data: Mapped[dict] = mapped_column(JSON)


class JobSource(Base):
    """用联合主键避免重复关联；删除岗位或资料会级联删除关联行，不删除另一端。"""
    __tablename__ = 'job_sources'
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id', ondelete='CASCADE'), primary_key=True)


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
    # 搜索正文单独保存，普通报告查询不读取；完整 JSON 导出时才按需加载。
    source_contents: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True, deferred=True)


# 全部资料和按类型筛选的资料，都按创建时间倒序展示。
Index('ix_documents_created_at', Document.created_at.desc())
Index('ix_documents_kind_created_at', Document.kind, Document.created_at.desc())
# 分页列表采用时间倒序、ID 升序，索引方向与查询一致。
Index('ix_jobs_created_at_id', Job.created_at.desc(), Job.id)
Index('ix_memories_created_at_id', Memory.created_at.desc(), Memory.id)
# 报告列表以及某类最新报告查询，使用各自的排序索引。
Index('ix_reports_created_at_id', Report.created_at.desc(), Report.id.desc())
Index('ix_reports_kind_demo_created_at_id', Report.kind, Report.is_demo, Report.created_at.desc(), Report.id.desc())
