import hashlib
from pathlib import Path, PureWindowsPath
import uuid
import zipfile

from .errors import AppError

MIB = 1024 * 1024
TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
         '.webp': 'image/webp', '.pdf': 'application/pdf',
         '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
         '.md': 'text/markdown'}
ALLOWED = {'image': {'.png', '.jpg', '.jpeg', '.webp'}, 'resume': {'.pdf', '.docx'}, 'knowledge': {'.md'}}


class FileStore:
    def __init__(self, data_dir, repository):
        self.directory = (Path(data_dir) / 'uploads').resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.repository = repository

    def resolve(self, document_id):
        item = self.repository.document(document_id, internal=True)
        path = (self.directory / item['stored_name']).resolve()
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
            return self.repository.add_document(filename=filename, kind=kind, media_type=TYPES[extension],
                                                stored_name=stored_name, size=size, sha256=digest.hexdigest())
        except BaseException:
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
        self.repository.delete_document(document_id)
        path.unlink(missing_ok=True)
