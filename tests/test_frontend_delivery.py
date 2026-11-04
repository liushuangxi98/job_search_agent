from fastapi.testclient import TestClient

from backend.app import create_app
from workbench_fixtures import complete_job


def test_frontend_entry_and_module_are_served_without_exposing_project_files(settings):
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        assert client.get('/').status_code == 200
        javascript = client.get('/static/app.js')
        assert javascript.status_code == 200
        assert 'javascript' in javascript.headers['content-type']
        assert client.get('/.env').status_code == 404
        assert client.get('/static/%2e%2e/.env').status_code == 404


def test_active_run_can_be_rediscovered_after_page_reload(client):
    from test_runs_api import start
    run = start(client, 'chat', text='补充', demo_scenario='needs_input')
    current = client.get('/api/runs').json()
    assert current['active']['id'] == run['id']
    assert current['active']['pending_input']['id'] == run['pending_input']['id']


def test_exact_duplicate_job_reuses_record_and_distinct_jd_is_preserved(client):
    data = complete_job(company='同一公司', title='开发', address='同一地址', jd_text='原始要求')
    first = client.post('/api/jobs', json=data).json()
    again = client.post('/api/jobs', json=data).json()
    assert first['id'] == again['id']
    assert client.get('/api/jobs').json()['total'] == 1
    changed = client.post('/api/jobs', json={**data, 'jd_text': '新的要求'}).json()
    assert changed['id'] != first['id']


def test_importing_application_factory_does_not_load_user_agent(tmp_path):
    import os
    import subprocess
    import sys
    code = """
import backend.agent_gateway as gateway
def forbidden(mode):
    raise AssertionError('Importing the factory must not load a user Agent')
gateway.load_handler = forbidden
import backend.app
"""
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code],
                            env={**os.environ, 'AGENT_MODE': 'real', 'DATA_DIR': str(tmp_path)},
                            capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
