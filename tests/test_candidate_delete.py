"""通过真实画像 API 和临时数据库验证删除、版本冲突及重新确认。"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from backend import app as app_module
from backend.config import Settings
from backend.models import Base, Candidate, Document, Job, Memory, Report
from backend.schemas import ProfileData
from workbench_fixtures import complete_profile


@pytest.fixture
def profile_client(tmp_path, monkeypatch):
    # 独立文件数据库用于 HTTP 线程访问，避免连接本机用户 MySQL。
    engine = create_engine('sqlite:///' + (tmp_path / 'profile.db').as_posix())
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    original = complete_profile()
    with sessions.begin() as session:
        session.add_all([
            Candidate(id=1, revision=3, confirmed=True, profile=original),
            Document(id='resume', filename='resume.pdf', kind='resume', media_type='application/pdf',
                     stored_name='resume.pdf', size=4, sha256='a' * 64),
            Job(id='job', data={'title': '开发'}),
            Memory(id='memory', data={'content': '已确认的记忆'}),
            Report(id='report', kind='interview', run_id='old-run', is_demo=False,
                   data={}, snapshot={'profile': original}),
        ])
    database = SimpleNamespace(session_factory=sessions, close=engine.dispose)
    monkeypatch.setattr(app_module, 'Database', lambda settings: database)
    app = app_module.create_app(Settings(_env_file=None, data_dir=tmp_path / 'data', agent_mode='demo'))
    with TestClient(app) as client:
        yield client, sessions, original


def test_delete_profile_and_confirm_again(profile_client):
    client, sessions, original = profile_client
    response = client.delete('/api/candidate?revision=3')
    assert response.status_code == 200
    empty = response.json()
    assert empty['revision'] == 4 and empty['confirmed'] is False
    assert empty['profile'] == ProfileData().model_dump() and empty['missing']
    assert client.get('/api/candidate').json() == empty
    assert client.get('/api/profile').json()['data'] == empty['profile']
    # 只清除当前资料，历史快照、来源文档及其他业务记录继续存在。
    with sessions() as session:
        assert session.get(Report, 'report').snapshot == {'profile': original}
        assert session.get(Document, 'resume') and session.get(Job, 'job') and session.get(Memory, 'memory')
    # 删除递增版本，旧表单保存和重复的旧删除请求都应冲突。
    assert client.put('/api/candidate', json={'revision': 3, 'profile': original}).status_code == 409
    assert client.delete('/api/candidate?revision=3').status_code == 409
    for action in ('evaluate', 'interview'):
        params = {'job_ids': ['job']} if action == 'evaluate' else {'job_id': 'job'}
        assert client.post('/api/runs', json={'action': action, 'input': params}).status_code == 409
    saved = client.put('/api/candidate', json={'revision': 4, 'profile': complete_profile(name='新画像')})
    assert saved.status_code == 200 and saved.json()['confirmed']
    assert saved.json()['revision'] == 5 and saved.json()['profile']['name'] == '新画像'


def test_stale_delete_and_failed_commit_preserve_profile(profile_client):
    client, sessions, _ = profile_client
    before = client.get('/api/candidate').json()
    assert client.delete('/api/candidate').status_code == 422
    assert client.delete('/api/candidate?revision=2').status_code == 409
    # 在提交前注入数据库错误，检查画像、确认状态和修订号一起回滚。
    def fail(session):
        raise SQLAlchemyError('模拟提交失败')
    event.listen(sessions, 'before_commit', fail)
    try:
        assert client.delete('/api/candidate?revision=3').status_code == 503
    finally:
        event.remove(sessions, 'before_commit', fail)
    assert client.get('/api/candidate').json() == before
