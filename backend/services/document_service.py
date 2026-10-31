"""上传资料的业务服务：文件校验、元数据事务和失败清理。"""
import hashlib
from pathlib import Path, PureWindowsPath
import uuid
import zipfile

from sqlalchemy.orm import Session, sessionmaker

from ..exceptions import AppError
from ..repositories.document_repository import DocumentRepository

MIB = 1024 * 1024
TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
         '.webp': 'image/webp', '.pdf': 'application/pdf',
         '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
         '.md': 'text/markdown'}
ALLOWED = {'image': {'.png', '.jpg', '.jpeg', '.webp'}, 'resume': {'.pdf', '.docx'}, 'knowledge': {'.md'}}


class DocumentService:
    def __init__(self, data_dir: Path, session_factory: sessionmaker[Session]):
        self.directory = (Path(data_dir) / 'uploads').resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.session_factory = session_factory

    @staticmethod
    def serialize(row, *, internal=False):
        result = {
            'id': row.id, 'filename': row.filename, 'kind': row.kind,
            'media_type': row.media_type, 'size': row.size,
            'created_at': row.created_at.isoformat() + 'Z',
        }
        if internal:
            result['stored_name'] = row.stored_name
        return result

    def get(self, document_id: str, *, internal=False):
        with self.session_factory() as session:
            row = DocumentRepository(session).get(document_id)
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            return self.serialize(row, internal=internal)

    def list(self, kind=None):
        with self.session_factory() as session:
            rows = DocumentRepository(session).list(kind)
            return {'items': [self.serialize(row) for row in rows], 'total': len(rows)}

    def add_metadata(self, **values):
        with self.session_factory.begin() as session:
            row = DocumentRepository(session).add(**values)
            return self.serialize(row)

    def delete_metadata(self, document_id: str):
        with self.session_factory.begin() as session:
            repository = DocumentRepository(session)
            row = repository.get(document_id)
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            repository.delete(row)

    def resolve(self, document_id):
        item = self.get(document_id, internal=True)
        path = (self.directory / item['stored_name']).resolve()
        # Agent 只能按数据库中的文件 ID 读取，不能自行指定任意系统路径。
        if not path.is_relative_to(self.directory) or not path.is_file():
            raise AppError(404, 'FILE_NOT_FOUND', '资料文件不存在')
        return path

    async def save(self, upload, kind):
        filename = PureWindowsPath(upload.filename or '').name
        extension = Path(filename).suffix.lower()
        if kind not in ALLOWED or extension not in ALLOWED[kind] or len(filename) > 255:
            raise AppError(422, 'FILE_TYPE', '图片支持 PNG/JPG/WebP，简历支持 PDF/DOCX，题库支持 MD')
        limit = (8 if kind == 'image' else 16) * MIB
        stored_name = uuid.uuid4().hex + extension
        path = self.directory / stored_name
        size, digest = 0, hashlib.sha256()
        try:
            with path.open('xb') as target:
                while chunk := await upload.read(64 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise AppError(413, 'UPLOAD_TOO_LARGE', f'文件超过 {limit // MIB} MiB 上限；未自动重试')
                    digest.update(chunk)
                    target.write(chunk)
            if size == 0:
                raise AppError(422, 'EMPTY_FILE', '文件为空')
            self._validate(path, extension)
            return self.add_metadata(
                filename=filename, kind=kind, media_type=TYPES[extension],
                stored_name=stored_name, size=size, sha256=digest.hexdigest(),
            )
        except BaseException:
            # 文件系统不参与 MySQL 事务，元数据保存失败或任务取消时主动清理。
            path.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()

    @staticmethod
    def _validate(path, extension):
        with path.open('rb') as source:
            head = source.read(12)
        valid = True
        if extension == '.png':
            valid = head.startswith(b'\x89PNG\r\n\x1a\n')
        elif extension in {'.jpg', '.jpeg'}:
            valid = head.startswith(b'\xff\xd8\xff')
        elif extension == '.webp':
            valid = head.startswith(b'RIFF') and head[8:12] == b'WEBP'
        elif extension == '.pdf':
            valid = head.startswith(b'%PDF-')
        elif extension == '.docx':
            try:
                with zipfile.ZipFile(path) as archive:
                    valid = 'word/document.xml' in archive.namelist()
            except zipfile.BadZipFile:
                valid = False
        elif extension == '.md':
            try:
                content = path.read_text(encoding='utf-8-sig')
                valid = '\x00' not in content
            except UnicodeError:
                valid = False
        if not valid:
            raise AppError(422, 'FILE_CONTENT', '文件内容与扩展名不符，或 MD 不是 UTF-8 文本')

    def delete(self, document_id):
        path = self.resolve(document_id)
        self.delete_metadata(document_id)
        path.unlink(missing_ok=True)
