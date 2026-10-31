import asyncio
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from backend.app import create_app
from backend.contracts import AgentResponse
from backend.runs import Run, RunManager
from test_runs_api import ready_profile, start


def upload_md(client, name):
    result = client.post('/api/documents', data={'kind': 'knowledge'},
                         files={'file': (name, ('# ' + name).encode(), 'text/markdown')})
    assert result.status_code == 201
    return result.json()['id']


def test_new_source_merge_invalidates_old_job_edit(client):
    a, b = upload_md(client, 'A.md'), upload_md(client, 'B.md')
    data = {'company': '来源合并公司', 'title': '开发', 'jd_text': '同一份描述', 'source_ids': [a]}
    original = client.post('/api/jobs', json=data).json()
    merged = client.post('/api/jobs', json={**data, 'source_ids': [b]}).json()
    assert merged['id'] == original['id']
    assert set(merged['data']['source_ids']) == {a, b}
    stale = client.put('/api/jobs/' + original['id'], json={'revision': original['revision'], 'data': data})
    assert stale.status_code == 409
    assert set(client.get('/api/jobs/' + original['id']).json()['data']['source_ids']) == {a, b}


def test_redaction_preserves_ids_and_business_snapshot(settings):
    config = settings.model_copy(update={'mysql_password': '123'})
    captured = []

    class Storage:
        def save_report(self, kind, data, snapshot, run_id, is_demo):
            captured.append(deepcopy(snapshot))
            return {'id': 'report123'}

    async def handle(command, services, emit):
        await emit('tool', 'password=123', {'api_key': 'sk-hidden-123456789'})
        return AgentResponse(kind='interview_report', data={'title': '第123次练习', 'dimensions': [], 'answers': []})

    manager = RunManager(config, Storage(), None, handle)
    run = Run(id='a123' + 'b' * 28, action='interview', input={}, context={'type': 'job', 'id': 'job123'},
              snapshot={'profile': {'data': {'summary': '参与123项目'}}}, is_demo=True)
    manager.runs[run.id] = run
    asyncio.run(manager.execute(run))
    result = manager.get(run.id)
    assert result['id'] == run.id
    assert result['report_id'] == 'report123'
    assert result['result']['data']['title'] == '第123次练习'
    assert captured[0]['profile']['data']['summary'] == '参与123项目'
    log = (config.data_dir / 'logs' / (run.id + '.jsonl')).read_text(encoding='utf-8')
    assert 'password=123' not in log
    assert 'sk-hidden-123456789' not in log


def test_pending_proposal_value_is_not_rewritten_by_redaction(settings):
    async def handle(command, services, emit):
        return AgentResponse(status='needs_input', kind='chat', data={'message': '修改简介'},
                             pending_input={'kind': 'proposal', 'prompt': '确认简介',
                                            'proposal': {'target': 'profile', 'revision': 1,
                                                         'changes': {'summary': '参与123项目'}}})
    manager = RunManager(settings.model_copy(update={'mysql_password': '123'}), None, None, handle)
    run = Run(id='a' * 32, action='chat', input={}, context={}, snapshot={}, is_demo=True)
    manager.runs[run.id] = run
    asyncio.run(manager.execute(run))
    assert run.pending_input['proposal']['changes']['summary'] == '参与123项目'


def test_event_protocol_fields_are_not_redacted(settings):
    async def handle(command, services, emit):
        return AgentResponse(kind='chat', data={'message': '正常回复'})
    manager = RunManager(settings.model_copy(update={'mysql_password': 'status'}), None, None, handle)
    run = Run(id='b' * 32, action='chat', input={}, context={}, snapshot={}, is_demo=True)
    manager.runs[run.id] = run
    asyncio.run(manager.execute(run))
    assert manager.get(run.id)['events'][0]['kind'] == 'status'


@pytest.mark.parametrize('kind,data', [
    ('interview_report', {'title': '错误结果', 'dimensions': 'not-an-array'}),
    ('interview_report', {'title': '错误结果', 'dimensions': [{'name': '技能', 'score': 999}]}),
    ('evaluation_report', {'title': '错误结果', 'jobs': [{'job_id': 'x', 'company': 'x', 'title': 'x', 'gaps': 'not-an-array'}]}),
])
def test_malformed_report_is_not_persisted(settings, kind, data):
    async def handle(command, services, emit):
        return AgentResponse(kind=kind, data=data)
    with TestClient(create_app(settings, handler=handle)) as client:
        run = start(client, 'chat', text='测试输出结构')
        assert run['status'] == 'failed'
        assert client.get('/api/reports').json()['total'] == 0


def test_proposal_cannot_clear_a_confirmed_profile(settings):
    async def handle(command, services, emit):
        return AgentResponse(status='needs_input', kind='chat', data={'message': '测试'},
                             pending_input={'kind': 'proposal', 'prompt': '清空简介', 'proposal': {
                                 'target': 'profile', 'revision': command.snapshot['profile']['revision'],
                                 'changes': {'summary': '', 'experience': '', 'skills': []}}})
    with TestClient(create_app(settings, handler=handle)) as client:
        ready_profile(client)
        before = client.get('/api/profile').json()
        run = start(client, 'chat', text='测试')
        response = client.post(f"/api/runs/{run['id']}/input", json={
            'input_id': run['pending_input']['id'], 'decision': 'confirm'})
        assert response.status_code == 422
        assert client.get('/api/profile').json() == before


@pytest.mark.parametrize('value', [{'bad': True}, ['text'], '   ', 123, 'x' * 50001])
def test_invalid_pasted_jd_is_rejected_before_handler(settings, value):
    called = []
    async def handle(command, services, emit):
        called.append(command)
        return AgentResponse(kind='chat', data={'message': '不应调用'})
    with TestClient(create_app(settings.model_copy(update={'agent_mode': 'real'}), handler=handle)) as client:
        response = client.post('/api/runs', json={'action': 'extract_jobs', 'input': {'text': value}})
        assert response.status_code == 422
        assert called == []


def test_interview_subject_is_available_after_page_reload(client):
    ready_profile(client)
    job = client.post('/api/jobs', json={'company': '面试上下文', 'title': '开发'}).json()
    run = start(client, 'interview', job_id=job['id'])
    current = client.get('/api/runs').json()['active']
    assert current['subject_job_id'] == job['id']
