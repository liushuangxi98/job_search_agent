from fastapi.testclient import TestClient

from backend.app import create_app
from workbench_fixtures import complete_job, complete_profile


def test_profile_persists_json_and_rejects_stale_update(client, settings):
    initial = client.get('/api/profile').json()
    payload = {'revision': initial['revision'], 'confirmed': True,
               'data': complete_profile(name='求职者 🧑‍💻', summary='四年 Python 测试开发')}
    saved = client.put('/api/profile', json=payload)
    assert saved.status_code == 200
    assert saved.json()['revision'] == initial['revision'] + 1
    assert client.put('/api/profile', json=payload).status_code == 409
    with TestClient(create_app(settings)) as restarted:
        result = restarted.get('/api/profile').json()
        assert result['confirmed'] is True
        assert result['data']['skills'] == ['Python', '自动化测试']
        assert result['data']['name'] == '求职者 🧑‍💻'


def test_invalid_preferences_do_not_change_database(client):
    before = client.get('/api/profile').json()
    bad = {'revision': before['revision'], 'confirmed': False, 'data': {'salary_min': 30000, 'salary_max': 20000}}
    assert client.put('/api/profile', json=bad).status_code == 422
    assert client.get('/api/profile').json() == before


def test_job_sources_unknown_id_rolls_back_and_search_works(client):
    job = complete_job(company='星河科技', job_description='Python 与 RAG', evidence=[{'document_id': 'missing'}])
    assert client.post('/api/jobs', json=job).status_code == 404
    assert client.get('/api/jobs').json()['total'] == 0
    job['evidence'] = []
    saved = client.post('/api/jobs', json=job)
    assert saved.status_code == 201
    item = saved.json()
    assert item['data']['salary_min'] == job['salary_min']
    assert client.get('/api/jobs?q=星河').json()['total'] == 1
    update = {'revision': item['revision'], 'data': {**job, 'address': '杭州软件园'}}
    assert client.put('/api/jobs/' + item['id'], json=update).status_code == 200
    assert client.put('/api/jobs/' + item['id'], json=update).status_code == 409
    assert client.delete('/api/jobs/' + item['id']).status_code == 204
    assert client.get('/api/jobs/' + item['id']).status_code == 404


def test_document_upload_read_delete_and_path_validation(client, settings):
    upload = client.post('/api/documents', data={'kind': 'knowledge'},
                         files={'file': ('../../题库.md', '# 题目\n什么是 RAG？'.encode(), 'text/markdown')})
    assert upload.status_code == 201
    item = upload.json()
    assert '/' not in item['filename']
    assert client.get(f"/api/documents/{item['id']}/content").content.startswith('# 题目'.encode())
    assert client.get('/api/documents/not-real/content').status_code == 404
    assert client.delete('/api/documents/' + item['id']).status_code == 204
    assert client.get(f"/api/documents/{item['id']}/content").status_code == 404
    assert not list((settings.data_dir / 'uploads').iterdir())


def test_bad_file_and_over_limit_upload_leave_no_files(client, settings):
    wrong = client.post('/api/documents', data={'kind': 'image'},
                        files={'file': ('bad.png', b'not an image', 'image/png')})
    assert wrong.status_code == 422
    large = client.post('/api/documents', data={'kind': 'image'},
                        files={'file': ('large.png', b'\x89PNG\r\n\x1a\n' + b'x' * (8 * 1024 * 1024), 'image/png')})
    assert large.status_code == 413
    assert client.get('/api/documents').json()['total'] == 0
    assert not list((settings.data_dir / 'uploads').iterdir())


def test_question_and_memory_crud(client):
    question = client.post('/api/knowledge/questions', json={
        'question': '解释召回与重排', 'answer': '两阶段检索', 'tags': ['RAG']})
    assert question.status_code == 201
    assert client.get('/api/knowledge/questions?q=召回').json()['total'] == 1
    memory = client.post('/api/memories', json={'content': '偏好杭州', 'category': 'preference', 'evidence': '用户确认'})
    assert memory.status_code == 201
    assert client.delete('/api/memories/' + memory.json()['id']).status_code == 204
    assert client.get('/api/memories').json()['total'] == 0


def test_no_secrets_in_health_or_errors(client, settings):
    result = client.get('/api/health')
    assert result.status_code == 200
    assert result.json()['database'] == 'ok'
    assert settings.mysql_password not in result.text
    assert settings.mysql_password not in client.get('/api/jobs/unknown').text
