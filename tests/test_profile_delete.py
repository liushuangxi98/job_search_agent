"""通过真实画像 API 和临时数据库验证删除、版本冲突及重新确认。"""
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from backend import app as app_module
from backend.config import Settings
from backend.database import initialize_database
from backend.models import Base, Profile, ProfileState, Document, Job, Memory, Report
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
            ProfileState(name='current', empty_revision=0),
            Profile(id=1, revision=3, profile=original),
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
    response = client.delete('/api/profile?revision=3')
    assert response.status_code == 200
    empty = response.json()
    assert empty['id'] is None
    assert empty['revision'] == 4
    assert empty['profile'] == ProfileData().model_dump() and empty['missing']
    assert client.get('/api/profile').json() == empty
    # 只删除当前画像行，历史快照、来源文档及其他业务记录继续存在。
    with sessions() as session:
        assert session.scalars(select(Profile)).all() == []
        assert session.get(Report, 'report').snapshot == {'profile': original}
        assert session.get(Document, 'resume') and session.get(Job, 'job') and session.get(Memory, 'memory')
    # 删除递增版本，旧表单保存和重复的旧删除请求都应冲突。
    assert client.put('/api/profile', json={'revision': 3, 'profile': original}).status_code == 409
    assert client.delete('/api/profile?revision=3').status_code == 409
    for action in ('evaluate', 'interview'):
        params = {'job_ids': ['job']} if action == 'evaluate' else {'job_id': 'job'}
        assert client.post('/api/runs', json={'action': action, 'input': params}).status_code == 409
    saved = client.put('/api/profile', json={'revision': 4, 'profile': complete_profile(name='新画像')})
    assert saved.status_code == 200 and not saved.json()['missing']
    assert saved.json()['revision'] == 5 and saved.json()['profile']['name'] == '新画像'
    new_id = saved.json()['id']
    assert new_id != 1
    edited = client.put('/api/profile', json={'revision': 5, 'profile': original})
    assert edited.json()['id'] == new_id and edited.json()['revision'] == 6
    assert client.delete('/api/profile?revision=3').status_code == 409
    with sessions() as session:
        assert list(session.scalars(select(Profile.id))) == [new_id]


def test_stale_delete_and_failed_commit_preserve_profile(profile_client):
    client, sessions, _ = profile_client
    before = client.get('/api/profile').json()
    assert client.delete('/api/profile').status_code == 422
    assert client.delete('/api/profile?revision=2').status_code == 409
    # 在提交前注入数据库错误，检查画像和修订号一起回滚。
    def fail(session):
        raise SQLAlchemyError('模拟提交失败')
    event.listen(sessions, 'before_commit', fail)
    try:
        assert client.delete('/api/profile?revision=3').status_code == 503
    finally:
        event.remove(sessions, 'before_commit', fail)
    assert client.get('/api/profile').json() == before


def test_first_saves_share_one_profile_and_delete_survives_restart(client, settings):
    sessions = client.app.state.database.session_factory
    with sessions() as session:
        assert session.scalars(select(Profile.id)).all() == []
    empty = client.get('/api/profile').json()
    assert empty['id'] is None and empty['revision'] == 0
    barrier = Barrier(2)

    def save(name):
        barrier.wait(timeout=10)
        return client.put('/api/profile', json={'revision': 0, 'profile': complete_profile(name=name)})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save, ['甲', '乙']))
    assert sorted(r.status_code for r in responses) == [200, 409]
    saved = next(r.json() for r in responses if r.status_code == 200)
    with sessions() as session:
        assert session.scalars(select(Profile.id)).all() == [saved['id']]
    deleted = client.delete(f"/api/profile?revision={saved['revision']}").json()
    initialize_database(settings)
    assert client.get('/api/profile').json() == deleted
    with sessions() as session:
        assert session.scalars(select(Profile.id)).all() == []
