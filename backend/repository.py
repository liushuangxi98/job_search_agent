from copy import deepcopy

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import sessionmaker

from .db import Candidate, Document, Job, JobSource, Memory, Question, Report, make_engine
from .errors import AppError
from .schemas import JobData, Preferences, ProfileData, ProfileUpdate, QuestionData, MemoryData


def require(session, model, key):
    value = session.get(model, key)
    if value is None:
        raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
    return value


def timestamp(row):
    return row.created_at.isoformat() + 'Z'


class Repository:
    def __init__(self, settings):
        self.engine = make_engine(settings)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def ping(self):
        with self.engine.connect() as connection:
            connection.execute(text('SELECT 1'))
            connection.execute(select(Candidate.id).limit(1))

    def candidate(self, section):
        with self.sessions() as session:
            row = require(session, Candidate, 1)
            schema = ProfileData if section == 'profile' else Preferences
            return {'revision': row.revision, 'confirmed': row.confirmed,
                    'data': schema.model_validate(getattr(row, section)).model_dump()}

    def update_candidate(self, section, data, revision, confirmed=None):
        with self.sessions.begin() as session:
            row = session.scalar(select(Candidate).where(Candidate.id == 1).with_for_update())
            if row is None:
                raise AppError(503, 'DATABASE_NOT_INITIALIZED', '请先初始化数据库')
            if row.revision != revision:
                raise AppError(409, 'REVISION_CONFLICT', '资料已发生变化，请刷新后重新确认')
            schema = ProfileData if section == 'profile' else Preferences
            if section == 'profile':
                data = ProfileUpdate(revision=revision, confirmed=row.confirmed if confirmed is None else confirmed,
                                     data=data).data.model_dump()
            setattr(row, section, schema.model_validate(data).model_dump())
            if confirmed is not None:
                row.confirmed = confirmed
            row.revision += 1
        return self.candidate(section)

    def _job(self, session, row):
        sources = session.scalars(select(JobSource.document_id).where(JobSource.job_id == row.id)).all()
        return {'id': row.id, 'revision': row.revision, 'created_at': timestamp(row),
                'data': {**deepcopy(row.data), 'source_ids': sources}}

    def job(self, key):
        with self.sessions() as session:
            return self._job(session, require(session, Job, key))

    def save_job(self, data, key=None, revision=None):
        data = JobData.model_validate(data).model_dump()
        sources = list(dict.fromkeys(data.pop('source_ids')))
        with self.sessions.begin() as session:
            for source in sources:
                require(session, Document, source)
            if key:
                row = session.scalar(select(Job).where(Job.id == key).with_for_update())
                if row is None:
                    raise AppError(404, 'NOT_FOUND', '岗位不存在')
                if row.revision != revision:
                    raise AppError(409, 'REVISION_CONFLICT', '岗位已变化，请刷新后重新确认')
                row.revision += 1
                row.data = data
                session.execute(delete(JobSource).where(JobSource.job_id == key))
            else:
                candidates = session.scalars(select(Job).where(
                    Job.data['company'].as_string() == data['company'],
                    Job.data['title'].as_string() == data['title']).with_for_update()).all()
                row = next((item for item in candidates if item.data == data), None)
                if row is None:
                    row = Job(data=data)
                    session.add(row)
                else:
                    existing = session.scalars(select(JobSource.document_id).where(JobSource.job_id == row.id)).all()
                    sources = [source for source in sources if source not in existing]
                    if sources:
                        row.revision += 1
            session.flush()
            key = row.id
            session.add_all(JobSource(job_id=key, document_id=source) for source in sources)
        return self.job(key)

    def delete_job(self, key):
        with self.sessions.begin() as session:
            session.delete(require(session, Job, key))

    def jobs(self, q='', offset=0, limit=100):
        with self.sessions() as session:
            query = select(Job)
            if q:
                query = query.where(or_(*[Job.data[field].as_string().contains(q, autoescape=True)
                                          for field in ('company', 'title', 'city')]))
            total = session.scalar(select(func.count()).select_from(query.subquery()))
            rows = session.scalars(query.order_by(Job.created_at.desc(), Job.id).offset(offset).limit(limit)).all()
            return {'items': [self._job(session, row) for row in rows], 'total': total}

    @staticmethod
    def _document(row):
        return {'id': row.id, 'filename': row.filename, 'kind': row.kind,
                'media_type': row.media_type, 'size': row.size, 'created_at': timestamp(row)}

    def add_document(self, **values):
        with self.sessions.begin() as session:
            row = Document(**values)
            session.add(row)
            session.flush()
            return self._document(row)

    def document(self, key, *, internal=False):
        with self.sessions() as session:
            row = require(session, Document, key)
            result = self._document(row)
            if internal:
                result['stored_name'] = row.stored_name
            return result

    def documents(self, kind=None):
        with self.sessions() as session:
            query = select(Document).order_by(Document.created_at.desc())
            if kind:
                query = query.where(Document.kind == kind)
            rows = session.scalars(query).all()
            return {'items': [self._document(row) for row in rows], 'total': len(rows)}

    def delete_document(self, key):
        with self.sessions.begin() as session:
            session.delete(require(session, Document, key))

    def records(self, kind, q='', offset=0, limit=100):
        model, field = (Question, 'question') if kind == 'questions' else (Memory, 'content')
        with self.sessions() as session:
            query = select(model)
            if q:
                query = query.where(model.data[field].as_string().contains(q, autoescape=True))
            total = session.scalar(select(func.count()).select_from(query.subquery()))
            rows = session.scalars(query.order_by(model.created_at.desc(), model.id).offset(offset).limit(limit)).all()
            return {'items': [{'id': row.id, 'data': row.data, 'created_at': timestamp(row)} for row in rows], 'total': total}

    def save_record(self, kind, data, key=None):
        model, schema = (Question, QuestionData) if kind == 'questions' else (Memory, MemoryData)
        data = schema.model_validate(data).model_dump()
        with self.sessions.begin() as session:
            row = require(session, model, key) if key else model()
            if kind == 'questions':
                document_id = data['document_id']
                if document_id:
                    require(session, Document, document_id)
                row.document_id = document_id
            row.data = data
            session.add(row)
            session.flush()
            return {'id': row.id, 'data': row.data, 'created_at': timestamp(row)}

    def delete_record(self, kind, key):
        model = Question if kind == 'questions' else Memory
        with self.sessions.begin() as session:
            session.delete(require(session, model, key))

    @staticmethod
    def _report(row):
        return {'id': row.id, 'kind': row.kind, 'run_id': row.run_id,
                'is_demo': row.is_demo, 'data': row.data, 'snapshot': row.snapshot,
                'created_at': timestamp(row)}

    def save_report(self, kind, data, snapshot, run_id, is_demo):
        with self.sessions.begin() as session:
            row = session.scalar(select(Report).where(Report.run_id == run_id))
            if row is None:
                row = Report(kind=kind, data=data, snapshot=snapshot, run_id=run_id, is_demo=is_demo)
                session.add(row)
                session.flush()
            return self._report(row)

    def reports(self):
        with self.sessions() as session:
            rows = session.scalars(select(Report).order_by(Report.created_at.desc())).all()
            return {'items': [self._report(row) for row in rows], 'total': len(rows)}

    def report(self, key):
        with self.sessions() as session:
            return self._report(require(session, Report, key))
