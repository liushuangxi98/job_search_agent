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
         '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
ALLOWED = {'image': {'.png', '.jpg', '.jpeg', '.webp'}, 'resume': {'.pdf', '.docx'}}


class DocumentService:
    def __init__(self, data_dir: Path, session_factory: sessionmaker[Session]):
        """在 data_dir 下准备上传目录并保留 Session 工厂；不会创建数据库记录。"""
        self.directory = (Path(data_dir) / 'uploads').resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.session_factory = session_factory

    @staticmethod
    def serialize(row, *, internal=False):
        """返回文件元数据字典，size 为字节；internal=True 才包含磁盘存储名。"""
        result = {
            'id': row.id, 'filename': row.filename, 'kind': row.kind,
            'media_type': row.media_type, 'size': row.size,
            'created_at': row.created_at.isoformat() + 'Z',
        }
        if internal:
            result['stored_name'] = row.stored_name
        return result

    def get(self, document_id: str, *, internal=False):
        """按资料 ID 返回元数据，internal 控制是否带存储名；记录缺失时抛 AppError。"""
        with self.session_factory() as session:
            row = DocumentRepository(session).get(document_id)
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            return self.serialize(row, internal=internal)

    def list(self, kind=None):
        """按资料类型返回 items 与 total；kind 为空时不过滤，无资料时 items 为空列表。"""
        with self.session_factory() as session:
            rows = DocumentRepository(session).list(kind)
            return {'items': [self.serialize(row) for row in rows], 'total': len(rows)}

    def add_metadata(self, **values):
        """将文件名、类型、存储名、字节数和摘要写入数据库，返回元数据字典。

        不创建或校验磁盘文件；数据库写入失败向上抛出，由上传流程负责清理文件。
        """
        with self.session_factory.begin() as session:
            row = DocumentRepository(session).add(**values)
            return self.serialize(row)

    def delete_metadata(self, document_id: str):
        """只删除资料元数据，成功无返回值，记录缺失抛 AppError。

        外键会清理岗位来源行，但不删除磁盘文件或改写报告快照。
        """
        with self.session_factory.begin() as session:
            repository = DocumentRepository(session)
            row = repository.get(document_id)
            if row is None:
                raise AppError(404, 'NOT_FOUND', '记录不存在或已删除')
            repository.delete(row)

    def resolve(self, document_id):
        """将已登记的资料 ID 转为上传目录内的现存 Path；记录、文件缺失或越界时抛 AppError。"""
        item = self.get(document_id, internal=True)
        path = (self.directory / item['stored_name']).resolve()
        # Agent 只能按数据库中的文件 ID 读取，不能自行指定任意系统路径。
        if not path.is_relative_to(self.directory) or not path.is_file():
            raise AppError(404, 'FILE_NOT_FOUND', '资料文件不存在')
        return path

    async def save(self, upload, kind):
        """保存具有 filename、异步 read/close 的上传对象，返回元数据字典。

        kind 为 image 或 resume；图片上限 8 MiB，其余为 16 MiB。
        超限抛 413，格式错误抛 422；进入写入阶段后失败会清理新文件并关闭上传对象。
        类型提前拒绝发生在清理块之前，此时本方法不会关闭 upload。
        """
        filename = PureWindowsPath(upload.filename or '').name
        extension = Path(filename).suffix.lower()
        if kind not in ALLOWED or extension not in ALLOWED[kind] or len(filename) > 255:
            raise AppError(422, 'FILE_TYPE', '图片支持 PNG/JPG/WebP，简历支持 PDF/DOCX')
        limit = (8 if kind == 'image' else 16) * MIB
        stored_name = uuid.uuid4().hex + extension
        path = self.directory / stored_name
        size, digest = 0, hashlib.sha256()
        try:
            with path.open('xb') as target:
                # await 等待上传数据；本地写文件和下面的数据库操作仍是同步的。
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
        """只检查基本文件标识；不承担文档解析、OCR 或完整图片解码。"""
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
        if not valid:
            raise AppError(422, 'FILE_CONTENT', '文件内容与扩展名不符')

    def delete(self, document_id):
        """先删除资料元数据，再移除磁盘文件；成功无返回值，资料或文件缺失抛 AppError。

        文件删除不属于 MySQL 事务；若磁盘删除失败，已提交的元数据删除不会自动恢复。
        """
        path = self.resolve(document_id)
        self.delete_metadata(document_id)
        path.unlink(missing_ok=True)
