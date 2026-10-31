from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, Query, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from .config import ROOT, Settings
from .agent_gateway import load_handler
from .contracts import RunInput, RunStart
from .errors import AppError
from .files import FileStore
from .repository import Repository
from .runs import RunManager
from .report_export import markdown
from .schemas import JobData, JobUpdate, MemoryData, PreferencesUpdate, ProfileUpdate, QuestionData


class BodyLimit:
    """Bound bytes before multipart parsing, including requests without Content-Length."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in {'POST', 'PUT', 'PATCH'}:
            return await self.app(scope, receive, send)
        maximum = 48 * 1024 * 1024
        chunks, size = [], 0
        header = dict(scope['headers']).get(b'content-length', b'0')
        try:
            too_large = int(header) > maximum
        except ValueError:
            too_large = False
        if not too_large:
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                body = message.get('body', b'')
                size += len(body)
                if size > maximum:
                    too_large = True
                    break
                chunks.append(body)
                if not message.get('more_body', False):
                    break
        if too_large:
            response = JSONResponse({'error': {'code': 'REQUEST_TOO_LARGE', 'message': '请求超过 48 MiB；本次操作失败，未自动重试'}}, status_code=413)
            return await response(scope, receive, send)
        body, replayed = b''.join(chunks), False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()
        await self.app(scope, replay, send)


def create_app(settings=None, handler=None):
    settings = settings or Settings()
    repository = Repository(settings)
    files = FileStore(settings.data_dir, repository)
    handler = handler or load_handler(settings.agent_mode)
    runs = RunManager(settings, repository, files, handler)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await runs.close()
        repository.engine.dispose()

    app = FastAPI(title='求职决策 Agent · 应用支撑', lifespan=lifespan)
    app.add_middleware(BodyLimit)
    app.state.repository, app.state.settings, app.state.files = repository, settings, files
    app.state.runs = runs

    @app.exception_handler(AppError)
    async def app_error(request, exc):
        return JSONResponse({'error': {'code': exc.code, 'message': exc.message}}, status_code=exc.status)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        return JSONResponse({'error': {'code': 'DATABASE_UNAVAILABLE', 'message': '数据库不可用，请检查 .env 并运行初始化命令；未切换其他数据库'}}, status_code=503)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_error(request, exc):
        errors = [{'field': '.'.join(str(x) for x in item['loc']), 'message': item['msg']} for item in exc.errors()]
        return JSONResponse({'error': {'code': 'INVALID_INPUT', 'message': '输入格式不正确', 'fields': errors}}, status_code=422)

    @app.get('/api/health')
    def health():
        repository.ping()
        return {'database': 'ok', 'mode': settings.agent_mode, 'agent_ready': handler is not None}

    @app.get('/api/profile')
    def profile():
        return repository.candidate('profile')

    @app.put('/api/profile')
    def update_profile(body: ProfileUpdate):
        return repository.update_candidate('profile', body.data.model_dump(), body.revision, body.confirmed)

    @app.get('/api/preferences')
    def preferences():
        return repository.candidate('preferences')

    @app.put('/api/preferences')
    def update_preferences(body: PreferencesUpdate):
        return repository.update_candidate('preferences', body.data.model_dump(), body.revision)

    @app.get('/api/documents')
    def documents(kind: str | None = None):
        return repository.documents(kind)

    @app.post('/api/documents', status_code=201)
    async def upload_document(kind: str = Form(...), file: UploadFile = File(...)):
        return await files.save(file, kind)

    @app.get('/api/documents/{key}/content')
    def document_content(key: str):
        item = repository.document(key)
        return FileResponse(files.resolve(key), media_type=item['media_type'], filename=item['filename'], content_disposition_type='inline')

    @app.delete('/api/documents/{key}', status_code=204)
    def delete_document(key: str):
        files.delete(key)
        return Response(status_code=204)

    @app.get('/api/jobs')
    def jobs(q: str = '', offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        return repository.jobs(q, offset, limit)

    @app.post('/api/jobs', status_code=201)
    def create_job(body: JobData):
        return repository.save_job(body.model_dump())

    @app.get('/api/jobs/{key}')
    def get_job(key: str):
        return repository.job(key)

    @app.put('/api/jobs/{key}')
    def update_job(key: str, body: JobUpdate):
        return repository.save_job(body.data.model_dump(), key, body.revision)

    @app.delete('/api/jobs/{key}', status_code=204)
    def delete_job(key: str):
        repository.delete_job(key)
        return Response(status_code=204)

    @app.get('/api/knowledge/questions')
    def questions(q: str = '', offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        return repository.records('questions', q, offset, limit)

    @app.post('/api/knowledge/questions', status_code=201)
    def create_question(body: QuestionData):
        return repository.save_record('questions', body.model_dump())

    @app.put('/api/knowledge/questions/{key}')
    def update_question(key: str, body: QuestionData):
        return repository.save_record('questions', body.model_dump(), key)

    @app.delete('/api/knowledge/questions/{key}', status_code=204)
    def delete_question(key: str):
        repository.delete_record('questions', key)
        return Response(status_code=204)

    @app.get('/api/memories')
    def memories():
        return repository.records('memories')

    @app.post('/api/memories', status_code=201)
    def create_memory(body: MemoryData):
        return repository.save_record('memories', body.model_dump())

    @app.put('/api/memories/{key}')
    def update_memory(key: str, body: MemoryData):
        return repository.save_record('memories', body.model_dump(), key)

    @app.delete('/api/memories/{key}', status_code=204)
    def delete_memory(key: str):
        repository.delete_record('memories', key)
        return Response(status_code=204)

    @app.get('/api/reports')
    def reports():
        return repository.reports()

    @app.get('/api/reports/{key}')
    def report(key: str):
        return repository.report(key)

    @app.get('/api/reports/{key}/export')
    def export_report(key: str, format: str = Query('md', pattern='^(md|json)$')):
        import json
        result = repository.report(key)
        content = markdown(result) if format == 'md' else json.dumps(result, ensure_ascii=False, indent=2)
        return Response(content, media_type='text/markdown' if format == 'md' else 'application/json',
                        headers={'Content-Disposition': f'attachment; filename="report-{key}.{format}"'})

    @app.post('/api/runs', status_code=202)
    async def start_run(body: RunStart):
        return await runs.start(body)

    @app.get('/api/runs')
    def current_run():
        active = next((run for run in list(runs.runs.values()) if run.status in {'running', 'needs_input'}), None)
        return {'active': runs.get(active.id) if active else None}

    @app.get('/api/runs/{key}')
    def get_run(key: str, after: int = Query(0, ge=0)):
        return runs.get(key, after)

    @app.post('/api/runs/{key}/input', status_code=202)
    async def submit_input(key: str, body: RunInput):
        return await runs.respond(key, body)

    @app.get('/api/demo/examples')
    def demo_examples():
        if settings.agent_mode != 'demo':
            raise AppError(404, 'NOT_FOUND', '当前不是演示模式')
        from .demo import examples
        return examples()

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'frontend' / 'index.html')

    app.mount('/static', StaticFiles(directory=ROOT / 'frontend', check_dir=False), name='frontend')
    return app
