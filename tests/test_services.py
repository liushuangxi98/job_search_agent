"""直接调用业务服务，确保 Agent 绕过 HTTP 后仍受到相同业务约束。"""
import asyncio
from io import BytesIO

from fastapi import UploadFile
from pydantic import ValidationError
import pytest
from sqlalchemy.exc import SQLAlchemyError

from backend.exceptions import AppError
from workbench_fixtures import complete_job, complete_preferences, complete_profile


def test_service_cannot_clear_a_confirmed_profile(client):
    service = client.app.state.candidate_service
    preferences = service.update('preferences', complete_preferences(), 0)
    saved = service.update('profile', complete_profile(summary='Python 开发'),
                           preferences['revision'], confirmed=True)

    with pytest.raises(ValidationError):
        service.update('profile', {}, saved['revision'])

    assert service.get('profile') == saved
    assert client.get('/api/profile').json() == saved


def test_service_rejects_stale_candidate_revision(client):
    service = client.app.state.candidate_service
    saved = service.update('preferences', {'city': '杭州'}, 0)

    with pytest.raises(AppError) as error:
        service.update('preferences', {'city': '深圳'}, 0)

    assert error.value.code == 'REVISION_CONFLICT'
    assert service.get('preferences') == saved


def test_service_failed_job_update_preserves_data_and_sources(client):
    documents = client.app.state.document_service
    source = asyncio.run(documents.save(UploadFile(BytesIO(b'# RAG'), filename='questions.md'), 'knowledge'))
    service = client.app.state.job_service
    saved = service.save(complete_job(company='事务公司', title='开发', source_ids=[source['id']]))

    with pytest.raises(AppError):
        service.save({**saved['data'], 'title': '不应保存', 'source_ids': ['missing']},
                     saved['id'], saved['revision'])

    assert service.get(saved['id']) == saved


def test_failed_document_metadata_write_removes_new_file(client, settings, monkeypatch):
    service = client.app.state.document_service

    def database_failure(*args, **kwargs):
        raise SQLAlchemyError('simulated database write failure')

    monkeypatch.setattr('backend.repositories.document_repository.DocumentRepository.add', database_failure)
    upload = UploadFile(BytesIO('# 测试题库'.encode()), filename='questions.md')
    with pytest.raises(SQLAlchemyError):
        asyncio.run(service.save(upload, 'knowledge'))

    assert service.list()['total'] == 0
    assert list((settings.data_dir / 'uploads').iterdir()) == []
    assert upload.file.closed


def test_service_failed_question_update_preserves_original_record(client):
    service = client.app.state.knowledge_service
    saved = service.save('questions', {'question': '什么是 RAG？'})
    before = service.list('questions')

    with pytest.raises(AppError):
        service.save('questions', {'question': '不应保存', 'document_id': 'missing'}, saved['id'])

    assert service.list('questions') == before


def test_database_initialization_preserves_existing_data(client, settings):
    from backend.database import initialize_database

    service = client.app.state.candidate_service
    preferences = service.update('preferences', complete_preferences(), 0)
    saved = service.update('profile', complete_profile(summary='重启后应保留'),
                           preferences['revision'], confirmed=True)
    initialize_database(settings)
    assert service.get('profile') == saved
