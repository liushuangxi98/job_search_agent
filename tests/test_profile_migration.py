"""只在隔离库验证旧版个人资料的合并、失败恢复与历史快照保留。"""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, text

from backend.database import Database, initialize_database
from backend.models import Profile, Report
from workbench_fixtures import complete_profile


def legacy_record(database, *, invalid=False):
    profile = complete_profile(name='迁移求职者 🧑‍💻')
    keys = ('direction', 'city', 'salary_min', 'salary_max', 'commute_origin')
    preferences = {key: profile.pop(key) for key in keys}
    profile['headline'] = '旧版求职标题'
    preferences['commute_limit_minutes'] = 45
    if invalid:
        preferences['salary_max'] = 1
    with database.session_factory.begin() as session:
        session.add(Profile(id=1))  # 构造旧版本初始化时留下的记录。
    with database.engine.begin() as connection:
        connection.execute(text('ALTER TABLE candidate ADD COLUMN preferences JSON NULL'))
        connection.execute(text(
            'UPDATE candidate SET profile=:profile, preferences=:preferences, revision=7 WHERE id=1'
        ), {'profile': json.dumps(profile, ensure_ascii=False),
            'preferences': json.dumps(preferences, ensure_ascii=False)})
    return profile, preferences


def test_legacy_profile_merges_once_and_preserves_reports(settings):
    database = Database(settings)
    try:
        profile, preferences = legacy_record(database)
        snapshot = {'profile': {'data': profile}, 'preferences': {'data': preferences}}
        with database.session_factory.begin() as session:
            report = Report(kind='evaluation', data={'title': '旧报告'}, snapshot=deepcopy(snapshot),
                            run_id='migration-report', is_demo=True)
            session.add(report)
            session.flush()
            report_id = report.id
        initialize_database(settings)
        assert 'preferences' not in {column['name'] for column in inspect(database.engine).get_columns('candidate')}
        with database.session_factory.begin() as session:
            row = session.get(Profile, 1)
            assert row.profile == complete_profile(name='迁移求职者 🧑‍💻')
            assert row.revision == 7
            assert session.get(Report, report_id).snapshot == snapshot
            row.profile = {**row.profile, 'city': '上海'}
        initialize_database(settings)
        with database.session_factory() as session:
            assert session.get(Profile, 1).profile['city'] == '上海'
    finally:
        database.close()


def test_upgrade_enables_auto_increment_without_replacing_existing_profile(client, settings):
    engine = client.app.state.database.engine
    original = complete_profile(evidence=[])
    with engine.begin() as connection:
        # 模拟旧 MySQL 表：主键只接受应用提供的值。
        connection.execute(text('DROP TABLE candidate_state'))
        connection.execute(text('ALTER TABLE candidate MODIFY COLUMN id INT NOT NULL'))
        connection.execute(text('ALTER TABLE candidate ADD COLUMN confirmed BOOLEAN NOT NULL'))
        connection.execute(text('INSERT INTO candidate (id, profile, revision, confirmed) VALUES (17, :p, 4, 1)'),
                           {'p': json.dumps(original, ensure_ascii=False)})
    initialize_database(settings)
    initialize_database(settings)
    saved = client.get('/api/profile').json()
    assert 'confirmed' not in {c['name'] for c in inspect(engine).get_columns('candidate')}
    assert saved == {'id': 17, 'revision': 4, 'profile': original, 'missing': []}
    assert client.delete('/api/profile?revision=4').status_code == 200
    new = client.put('/api/profile', json={'revision': 5, 'profile': original}).json()
    assert new['id'] > 17


def test_invalid_legacy_values_leave_both_original_columns(settings):
    database = Database(settings)
    try:
        profile, preferences = legacy_record(database, invalid=True)
        with pytest.raises(ValidationError):
            initialize_database(settings)
        with database.engine.connect() as connection:
            row = connection.execute(text('SELECT profile, preferences FROM candidate WHERE id=1')).one()
            assert json.loads(row.profile) == profile
            assert json.loads(row.preferences) == preferences
    finally:
        database.close()


def test_retry_after_merge_preserves_new_profile_values(settings):
    database = Database(settings)
    try:
        profile, preferences = legacy_record(database)
        # 模拟合并已提交、删旧列前进程退出；重新迁移以完整 profile 为准。
        merged = {**complete_profile(name=profile['name']), 'city': '上海'}
        with database.engine.begin() as connection:
            connection.execute(text('UPDATE candidate SET profile=:value WHERE id=1'),
                               {'value': json.dumps(merged, ensure_ascii=False)})
        initialize_database(settings)
        with database.session_factory() as session:
            assert session.get(Profile, 1).profile == merged
    finally:
        database.close()


def test_retired_profile_fields_are_readable_before_migration(client, settings):
    from workbench_fixtures import profile_payload

    old = complete_profile(name='旧画像', education='本科，计算机相关')
    old.pop('major')
    evidence = [{'locator': '第 1 页', 'text': '参与自动化测试开发'}]
    old.update(headline='旧职位标题', commute_limit_minutes=45, evidence=evidence)
    with client.app.state.database.session_factory.begin() as session:
        row = Profile(id=1, profile=old)
        session.add(row)
        row.revision = 3

    result = client.get('/api/profile').json()
    assert result['id'] is not None
    assert result['missing'] == [{'field': 'profile.major', 'label': '大学专业'}]
    assert result['profile']['major'] == ''
    assert result['profile']['education'] == old['education']
    assert result['profile']['evidence'] == evidence
    assert 'headline' not in result['profile'] and 'commute_limit_minutes' not in result['profile']

    initialize_database(settings)
    with client.app.state.database.session_factory() as session:
        row = session.get(Profile, 1)
        assert row.profile == {k: v for k, v in old.items() if k not in ('headline', 'commute_limit_minutes')}
        assert row.revision == 3

    for key in ('headline', 'commute_limit_minutes'):
        invalid = profile_payload(3)
        invalid['profile'][key] = old[key]
        assert client.put('/api/profile', json=invalid).status_code == 422
    saved = client.put('/api/profile', json={
        'revision': 3, 'profile': {**result['profile'], 'major': '软件工程'},
    }).json()
    assert saved['profile']['major'] == '软件工程'
    assert saved['profile']['evidence'] == evidence


@pytest.mark.parametrize('old,new', [('public_transit', '公共交通'), ('driving', '驾车')])
def test_commute_mode_migration_preserves_other_data_and_revision(client, settings, old, new):
    profile = complete_profile(commute_mode=old)
    with client.app.state.database.session_factory.begin() as session:
        row = Profile(id=1, profile=profile)
        session.add(row)
        row.revision = 5
    assert client.get('/api/profile').json()['profile']['commute_mode'] == new
    initialize_database(settings)
    initialize_database(settings)
    with client.app.state.database.session_factory() as session:
        row = session.get(Profile, 1)
        assert row.profile == {**profile, 'commute_mode': new}
        assert row.revision == 5
