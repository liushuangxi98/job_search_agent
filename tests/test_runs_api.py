import time

from fastapi.testclient import TestClient

from backend.app import create_app
from workbench_fixtures import candidate_payload, complete_job


def ready_profile(client):
    current = client.get('/api/candidate')
    assert current.status_code == 200, current.text
    saved = client.put('/api/candidate', json=candidate_payload(current.json()['revision']))
    assert saved.status_code == 200, saved.text
    return saved.json()


def wait_run(client, key):
    for _ in range(100):
        response = client.get('/api/runs/' + key)
        assert response.status_code == 200
        result = response.json()
        if result['status'] != 'running':
            return result
        time.sleep(0.02)
    raise AssertionError('Run did not yield a response')


def start(client, action, **values):
    result = client.post('/api/runs', json={'action': action, 'input': values})
    assert result.status_code == 202, result.text
    return wait_run(client, result.json()['id'])


def test_completed_report_survives_restart_without_runtime(client, settings):
    ready_profile(client)
    job = client.post('/api/jobs', json=complete_job(company='示例公司', title='Agent 开发', jd_text='原始 JD')).json()
    run = start(client, 'evaluate', job_ids=[job['id']])
    assert run['status'] == 'completed'
    assert run['is_demo'] is True
    assert run['events'] and run['report_id']
    report_id = run['report_id']
    client.delete('/api/jobs/' + job['id'])
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get('/api/runs/' + run['id']).status_code == 404
        report = restarted.get('/api/reports/' + report_id).json()
        assert 'preferences' not in report['snapshot']
        assert report['snapshot']['profile']['data']['city'] == '杭州'
        assert report['snapshot']['jobs'][0]['data']['jd_text'] == '原始 JD'
        assert report['is_demo'] is True
        assert restarted.get(f'/api/reports/{report_id}/export?format=json').status_code == 200
        markdown = restarted.get(f'/api/reports/{report_id}/export?format=md')
        assert markdown.status_code == 200
        assert '模拟' in markdown.text


def test_confirmation_applies_once_and_locks_other_runs(client):
    before = client.get('/api/profile').json()
    run = start(client, 'chat', text='演示补充信息', demo_scenario='needs_input')
    assert run['status'] == 'needs_input'
    assert client.post('/api/runs', json={'action': 'chat', 'input': {'text': 'another'}}).status_code == 409
    body = {'input_id': run['pending_input']['id'], 'decision': 'confirm'}
    accepted = client.post(f"/api/runs/{run['id']}/input", json=body)
    assert accepted.status_code == 202
    assert wait_run(client, run['id'])['status'] == 'completed'
    saved = client.get('/api/profile').json()
    assert saved['data']['city'] == '杭州'
    assert saved['revision'] == before['revision'] + 1
    assert client.post(f"/api/runs/{run['id']}/input", json=body).status_code == 409
    assert client.get('/api/profile').json() == saved


def test_reject_and_revision_conflict_do_not_overwrite_preferences(client):
    before = client.get('/api/profile').json()
    run = start(client, 'chat', text='演示', demo_scenario='needs_input')
    body = {'input_id': run['pending_input']['id'], 'decision': 'reject'}
    assert client.post(f"/api/runs/{run['id']}/input", json=body).status_code == 202
    wait_run(client, run['id'])
    assert client.get('/api/profile').json() == before
    run = start(client, 'chat', text='演示', demo_scenario='needs_input')
    client.put('/api/profile', json={'revision': before['revision'], 'confirmed': False, 'data': {'city': '深圳'}})
    body = {'input_id': run['pending_input']['id'], 'decision': 'confirm'}
    assert client.post(f"/api/runs/{run['id']}/input", json=body).status_code == 409
    assert client.get('/api/profile').json()['data']['city'] == '深圳'


def test_demo_failure_and_partial_states_are_visible(client):
    assert start(client, 'chat', text='演示', demo_scenario='fail')['status'] == 'failed'
    ready_profile(client)
    job = client.post('/api/jobs', json=complete_job(company='例子', title='开发')).json()
    run = start(client, 'evaluate', job_ids=[job['id']], demo_scenario='partial')
    assert run['status'] == 'partial'
    result = run['result']['data']['jobs'][0]
    assert result['match_score'] is not None
    assert result['commutes']['public_transit']['duration_minutes'] is None
    assert result['missing']
    assert run['report_id']
    assert client.get('/api/reports/' + run['report_id']).json()['data']['jobs'][0] == result


def test_real_mode_never_falls_back_to_demo(settings, monkeypatch):
    # Keep this test independent when the user later adds their real Agent module.
    monkeypatch.setattr('backend.app.load_handler', lambda mode: None)
    with TestClient(create_app(settings.model_copy(update={'agent_mode': 'real'}))) as client:
        response = client.post('/api/runs', json={'action': 'chat', 'input': {'text': '你好'}})
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'AGENT_NOT_CONNECTED'
        assert '模拟' not in response.text


def test_interview_answers_stay_in_memory_until_report(client):
    ready_profile(client)
    job = client.post('/api/jobs', json=complete_job(company='示例', title='开发')).json()
    run = start(client, 'interview', job_id=job['id'])
    assert run['pending_input']['kind'] == 'question'
    assert client.get('/api/reports').json()['total'] == 0
    response = client.post(f"/api/runs/{run['id']}/input", json={
        'input_id': run['pending_input']['id'], 'decision': 'answer', 'text': '我的回答'})
    assert response.status_code == 202
    run = wait_run(client, run['id'])
    assert run['result']['data']['round'] == 2
    client.post(f"/api/runs/{run['id']}/input", json={
        'input_id': run['pending_input']['id'], 'decision': 'finish'})
    run = wait_run(client, run['id'])
    assert run['report_id']
    assert run['result']['data']['answers'][0]['answer'] == '我的回答'


def test_event_and_exception_redaction(client, settings):
    async def handler(command, services, emit):
        await emit('tool', 'secret=' + settings.mysql_password,
                   {'api_key': 'sk-not-for-display-123456', 'password': 'another-secret'})
        raise RuntimeError('secret=' + settings.mysql_password)

    with TestClient(create_app(settings, handler=handler)) as other:
        run = start(other, 'chat', text='测试')
        text = other.get('/api/runs/' + run['id']).text
        assert run['status'] == 'failed'
        assert settings.mysql_password not in text
        assert 'sk-not-for-display-123456' not in text
        assert 'another-secret' not in text
        log = (settings.data_dir / 'logs' / (run['id'] + '.jsonl')).read_text(encoding='utf-8')
        assert settings.mysql_password not in log


def test_request_body_ceiling_rejects_without_retry(client):
    response = client.post('/api/runs', content=b'{}', headers={
        'content-length': str(49 * 1024 * 1024), 'content-type': 'application/json'})
    assert response.status_code == 413


def test_finishing_clarification_does_not_continue_evaluation(client):
    ready_profile(client)
    job = client.post('/api/jobs', json=complete_job(company='结束场景', title='开发')).json()
    run = start(client, 'evaluate', job_ids=[job['id']], demo_scenario='needs_input')
    assert run['status'] == 'needs_input'
    response = client.post(f"/api/runs/{run['id']}/input", json={
        'input_id': run['pending_input']['id'], 'decision': 'finish'})
    assert response.status_code == 202
    ended = wait_run(client, run['id'])
    assert ended['report_id'] is None
    assert client.get('/api/reports').json()['total'] == 0
