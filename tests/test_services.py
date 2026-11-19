"""直接调用业务服务，确保 Agent 绕过 HTTP 后仍受到相同业务约束。"""
import asyncio
from io import BytesIO

from fastapi import UploadFile
import pytest
from sqlalchemy.exc import SQLAlchemyError

from backend.exceptions import AppError
from workbench_fixtures import complete_job, complete_profile


def test_service_cannot_clear_a_saved_profile(client):
    service = client.app.state.profile_service
    saved = service.save(complete_profile(summary='Python 开发'),
                           0)

    with pytest.raises(AppError) as error:
        service.save({}, saved['revision'])

    assert error.value.code == 'CANDIDATE_INCOMPLETE'
    assert service.get() == saved
    assert client.get('/api/profile').json() == saved


def test_service_rejects_stale_candidate_revision(client):
    service = client.app.state.profile_service
    saved = service.save(complete_profile(city='杭州'), 0)

    with pytest.raises(AppError) as error:
        service.save({'city': '深圳'}, 0)

    assert error.value.code == 'REVISION_CONFLICT'
    assert service.get() == saved


def test_service_failed_job_update_preserves_data_and_sources(client):
    documents = client.app.state.document_service
    source = asyncio.run(documents.save(UploadFile(BytesIO(b'\x89PNG\r\n\x1a\n'), filename='job.png'), 'image'))
    service = client.app.state.job_service
    saved = service.save(complete_job(company='事务公司', title='开发', evidence=[{'document_id': source['id']}]))

    with pytest.raises(AppError):
        service.save({**saved['data'], 'title': '不应保存', 'evidence': [{'document_id': 'missing'}]},
                     saved['id'], saved['revision'])

    assert service.get(saved['id']) == saved


def test_failed_document_metadata_write_removes_new_file(client, settings, monkeypatch):
    service = client.app.state.document_service

    def database_failure(*args, **kwargs):
        raise SQLAlchemyError('simulated database write failure')

    monkeypatch.setattr('backend.repositories.document_repository.DocumentRepository.add', database_failure)
    upload = UploadFile(BytesIO(b'%PDF-1.4'), filename='resume.pdf')
    with pytest.raises(SQLAlchemyError):
        asyncio.run(service.save(upload, 'resume'))

    assert service.list()['total'] == 0
    assert list((settings.data_dir / 'uploads').iterdir()) == []
    assert upload.file.closed


def test_database_initialization_preserves_existing_data(client, settings):
    from backend.database import initialize_database

    service = client.app.state.profile_service
    saved = service.save(complete_profile(summary='重启后应保留'),
                           0)
    initialize_database(settings)
    assert service.get() == saved
