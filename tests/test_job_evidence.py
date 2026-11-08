"""岗位依据的单截图约束、来源有效性、重复导入与旧记录迁移。"""
from copy import deepcopy

import pytest

from backend.database import initialize_database
from backend.contracts import AgentResponse
from backend.exceptions import AppError
from backend.models import Job, JobSource, Report
from workbench_fixtures import complete_job, upload_image


def test_one_image_can_supply_multiple_quotes_and_other_image_stays_separate(client):
    a, b = upload_image(client, 'A.png'), upload_image(client, 'B.png')
    evidence = [{'document_id': a, 'locator': '薪资', 'text': '20K—30K'},
                {'document_id': a, 'locator': '地址', 'text': '测试园区'}]
    first = client.post('/api/jobs', json=complete_job(evidence=evidence))
    assert first.status_code == 201
    first = first.json()
    assert first['data']['evidence'] == evidence
    assert 'source_ids' not in first['data']
    again = client.post('/api/jobs', json=complete_job(evidence=evidence)).json()
    assert again == first
    other = client.post('/api/jobs', json=complete_job(evidence=[{**item, 'document_id': b} for item in evidence]))
    assert other.status_code == 201 and other.json()['id'] != first['id']


def test_mixed_images_and_non_image_sources_are_rejected_before_writing(client):
    a, b = upload_image(client, 'A.png'), upload_image(client, 'B.png')
    mixed = [{'document_id': a}, {'document_id': b}]
    assert client.post('/api/jobs', json=complete_job(evidence=mixed)).status_code == 422
    resume = client.post('/api/documents', data={'kind': 'resume'}, files={'file': ('简历.pdf', b'%PDF-1.4')}).json()
    assert client.post('/api/jobs', json=complete_job(evidence=[{'document_id': resume['id']}])).status_code == 422
    assert client.get('/api/jobs').json()['total'] == 0


def test_chat_proposal_cannot_rewrite_job_evidence(client):
    saved = client.post('/api/jobs', json=complete_job()).json()
    with pytest.raises(AppError) as error:
        client.app.state.run_service.apply_proposal({
            'target': 'job', 'target_id': saved['id'], 'revision': saved['revision'],
            'changes': {'evidence': [{'text': '替换依据'}]},
        })
    assert error.value.code == 'INVALID_PROPOSAL'
    assert client.get('/api/jobs/' + saved['id']).json() == saved


def test_pasted_quote_and_deleted_image_remain_editable(client):
    quote = {'document_id': None, 'locator': '粘贴原文', 'text': 'Python 开发'}
    assert client.post('/api/jobs', json=complete_job(evidence=[quote])).status_code == 201
    source = upload_image(client)
    image_job = client.post('/api/jobs', json=complete_job(evidence=[{**quote, 'document_id': source}])).json()
    assert client.delete('/api/documents/' + source).status_code == 204
    result = client.get('/api/jobs/' + image_job['id']).json()
    assert result['data']['evidence'] == [quote]
    update = client.put('/api/jobs/' + result['id'], json={
        'revision': result['revision'], 'data': {**result['data'], 'job_requirement': '更新任职要求'},
    })
    assert update.status_code == 200
    assert update.json()['data']['evidence'] == [quote]


def test_legacy_single_source_migrates_without_fabricating_quotes_or_changing_reports(client, settings):
    source = upload_image(client)
    saved = client.post('/api/jobs', json=complete_job(evidence=[{'document_id': source}])).json()
    with client.app.state.database.session_factory.begin() as session:
        row = session.get(Job, saved['id'])
        old_data = {key: value for key, value in row.data.items() if key != 'evidence'}
        row.data = old_data
        snapshot = {'job': {**old_data, 'source_ids': [source]}}
        report = Report(kind='evaluation', data={'title': '旧报告'}, snapshot=deepcopy(snapshot),
                        run_id='legacy-evidence', is_demo=True)
        session.add(report)
        session.flush()
        report_id = report.id
    initialize_database(settings)
    initialize_database(settings)
    result = client.get('/api/jobs/' + saved['id']).json()
    assert result['data']['evidence'] == [{'document_id': source, 'locator': '', 'text': ''}]
    assert result['revision'] == saved['revision']
    assert client.get('/api/reports/' + report_id).json()['snapshot'] == snapshot


def test_legacy_multiple_sources_require_explicit_resolution(client, settings):
    a, b = upload_image(client, 'A.png'), upload_image(client, 'B.png')
    saved = client.post('/api/jobs', json=complete_job(evidence=[{'document_id': a}])).json()
    with client.app.state.database.session_factory.begin() as session:
        row = session.get(Job, saved['id'])
        row.data = {key: value for key, value in row.data.items() if key != 'evidence'}
        session.add(JobSource(job_id=saved['id'], document_id=b))
    with pytest.raises(ValueError, match='多张'):
        initialize_database(settings)
    with client.app.state.database.session_factory() as session:
        assert 'evidence' not in session.get(Job, saved['id']).data


def test_legacy_job_fields_migrate_without_guessing_requirements(client, settings):
    old = {
        'company': '旧公司', 'title': '开发', 'jd_text': '负责系统开发，要求熟悉 Python',
        'notes': '旧备注', 'salary_note': '13 薪', 'blind_spots': {'address': '未披露'},
    }
    with client.app.state.database.session_factory.begin() as session:
        row = Job(data=old, revision=4)
        session.add(row)
        session.flush()
        key = row.id
    before = client.get('/api/jobs/' + key).json()
    assert before['data']['job_description'] == old['jd_text']
    assert before['data']['job_requirement'] == ''
    assert before['data']['education'] == ''
    assert before['data']['experience_years'] is None
    assert not {'notes', 'jd_text', 'salary_note', 'blind_spots'} & before['data'].keys()
    initialize_database(settings)
    assert client.get('/api/jobs/' + key).json() == before
    with client.app.state.database.session_factory() as session:
        assert not {'notes', 'jd_text', 'salary_note', 'blind_spots'} & session.get(Job, key).data.keys()


@pytest.mark.parametrize('change,code', [
    ({'complete': None}, 'JOB_TEXT_FOLDED'), ({'title': ' 未披露 '}, 'JOB_INCOMPLETE'),
])
def test_unusable_job_cannot_be_saved(client, change, code):
    response = client.post('/api/jobs', json=complete_job(**change))
    assert response.status_code == 422 and response.json()['error']['code'] == code
    assert client.get('/api/jobs').json()['total'] == 0


def test_explicitly_unrestricted_experience_is_valid(client):
    response = client.post('/api/jobs', json=complete_job(experience_years=0, evidence=[]))
    assert response.status_code == 201
    assert response.json()['data']['experience_years'] == 0


@pytest.mark.parametrize('waiting', [False, True])
def test_folded_image_fails_before_preview_or_clarification(client, waiting, monkeypatch):
    from test_runs_api import start

    async def handler(command, services, emit):
        draft = complete_job(complete=None)
        if waiting:
            return AgentResponse(status='needs_input', kind='chat', data={'message': '请补充'},
                                 pending_input={'kind': 'question', 'prompt': '请补充'}, state=draft)
        return AgentResponse(kind='job_drafts', data=draft)

    monkeypatch.setattr(client.app.state.run_service, 'handler', handler)
    run = start(client, 'extract_jobs', document_id=upload_image(client))
    assert run['status'] == 'failed' and run['pending_input'] is None
    assert run['result']['data']['code'] == 'JOB_TEXT_FOLDED'
    assert client.get('/api/jobs').json()['total'] == 0
