"""三入口工作台的 HTTP 行为与契约回归；仅使用固定样例，不调用真实模型。"""
import asyncio
from copy import deepcopy
from threading import Event

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError

from backend.app import create_app
from backend.contracts import AgentResponse, validate_result
from backend.models import Candidate, Job, Report
from test_runs_api import ready_profile, start, wait_run
from workbench_fixtures import candidate_payload, complete_job, complete_preferences, complete_profile


CANDIDATE_MISSING = [
    ('profile', 'name', ''),
    ('profile', 'education', ''),
    ('profile', 'experience_years', None),
    ('profile', 'skills', []),
    ('profile', 'experience', ''),
    ('profile', 'projects', ''),
    ('preferences', 'direction', ''),
    ('preferences', 'city', ''),
    ('preferences', 'commute_origin', ''),
    ('preferences', 'salary_min', None),
    ('preferences', 'salary_max', None),
]
JOB_MISSING = [
    ('company', ''), ('title', ''), ('city', ''), ('address', ''), ('jd_text', ''),
    ('salary_min', None), ('salary_max', None),
]
OLD_SCORES = {'salary_score', 'commute_score', 'total_score'}


def test_candidate_combines_sections_and_increments_revision_once(client, settings):
    initial = client.get('/api/candidate')
    assert initial.status_code == 200
    before = initial.json()
    assert before['confirmed'] is False
    assert before['missing']
    assert all(item['label'] for item in before['missing'])
    assert {item['field'] for item in before['missing']} <= {
        section + '.' + field for section, field, _ in CANDIDATE_MISSING
    }
    payload = candidate_payload(before['revision'], profile=complete_profile(experience_years=0))
    response = client.put('/api/candidate', json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['revision'] == before['revision'] + 1
    assert saved['confirmed'] is True
    assert saved['missing'] == []
    assert saved['profile']['experience_years'] == 0
    for section in ('profile', 'preferences'):
        assert {key: saved[section][key] for key in payload[section]} == payload[section]
        legacy = client.get('/api/' + section).json()
        assert legacy['revision'] == saved['revision']
        assert legacy['data'] == saved[section]
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get('/api/candidate').json() == saved


@pytest.mark.parametrize('section,field,value', CANDIDATE_MISSING + [
    ('profile', 'name', '   '), ('profile', 'skills', ['   ']),
])
def test_candidate_missing_field_rejects_both_sections_atomically(client, section, field, value):
    before = ready_profile(client)
    payload = candidate_payload(before['revision'], profile=complete_profile(name='不应写入'),
                                preferences=complete_preferences(city='上海'))
    payload[section][field] = value
    response = client.put('/api/candidate', json=payload)
    assert response.status_code == 422, response.text
    assert client.get('/api/candidate').json() == before
    assert client.get('/api/profile').json()['data'] == before['profile']
    assert client.get('/api/preferences').json()['data'] == before['preferences']


@pytest.mark.parametrize('section,field,value', [
    ('profile', 'experience_years', -1),
    ('profile', 'experience_years', 'NaN'),
    ('profile', 'experience_years', 'Infinity'),
    pytest.param('profile', 'projects', '项' * 20001, id='projects-too-long'),
    ('preferences', 'salary_min', 0),
    ('preferences', 'salary_min', 40000),
])
def test_candidate_invalid_values_do_not_write(client, section, field, value):
    before = ready_profile(client)
    payload = candidate_payload(before['revision'])
    payload[section][field] = value
    assert client.put('/api/candidate', json=payload).status_code == 422
    assert client.get('/api/candidate').json() == before


def test_stale_candidate_revision_preserves_both_saved_sections(client):
    original = ready_profile(client)
    winner = client.put('/api/candidate', json=candidate_payload(
        original['revision'], profile=complete_profile(name='最新姓名'),
        preferences=complete_preferences(city='上海'),
    ))
    assert winner.status_code == 200
    saved = winner.json()
    assert saved['revision'] == original['revision'] + 1
    stale = candidate_payload(original['revision'], profile=complete_profile(name='旧表单'),
                              preferences=complete_preferences(city='深圳'))
    assert client.put('/api/candidate', json=stale).status_code == 409
    assert client.get('/api/candidate').json() == saved
    assert client.put('/api/profile', json={
        'revision': original['revision'], 'confirmed': True, 'data': stale['profile'],
    }).status_code == 409
    assert client.put('/api/preferences', json={
        'revision': original['revision'], 'data': stale['preferences'],
    }).status_code == 409
    assert client.get('/api/candidate').json() == saved


def test_candidate_omitted_direction_is_not_silently_confirmed(client):
    before = client.get('/api/candidate').json()
    payload = candidate_payload(before['revision'])
    del payload['preferences']['direction']
    response = client.put('/api/candidate', json=payload)
    assert response.status_code == 422
    assert client.get('/api/candidate').json() == before


def test_legacy_job_reimport_reuses_id_and_merges_sources(client):
    from test_review_regressions import upload_md

    first_source = upload_md(client, '旧岗位来源.md')
    second_source = upload_md(client, '新岗位来源.md')
    response = client.post('/api/jobs', json=complete_job(source_ids=[first_source]))
    assert response.status_code == 201
    original = response.json()
    with client.app.state.database.session_factory.begin() as session:
        row = session.get(Job, original['id'])
        legacy = dict(row.data)
        legacy.pop('blind_spots')
        row.data = legacy
    result = client.post('/api/jobs', json=complete_job(source_ids=[second_source]))
    assert result.status_code == 201
    merged = result.json()
    assert merged['id'] == original['id']
    assert set(merged['data']['source_ids']) == {first_source, second_source}
    assert merged['revision'] == original['revision'] + 1
    assert client.get('/api/jobs').json()['total'] == 1


def test_candidate_database_failure_rolls_back_both_sections_and_revision(client):
    before = ready_profile(client)
    payload = candidate_payload(before['revision'], profile=complete_profile(name='事务中的姓名'),
                                preferences=complete_preferences(city='上海'))
    factory = client.app.state.database.session_factory
    flushed = []

    def fail_commit(session):
        # 在真实事务已 flush 后失败，验证数据库回滚，而非仅请求入参被拒绝。
        session.flush()
        row = session.get(Candidate, 1)
        flushed.append((row.profile['name'], row.preferences['city'], row.revision))
        raise SQLAlchemyError('simulated commit failure')

    event.listen(factory, 'before_commit', fail_commit)
    try:
        response = client.put('/api/candidate', json=payload)
    finally:
        event.remove(factory, 'before_commit', fail_commit)
    assert response.status_code == 503
    assert flushed == [('事务中的姓名', '上海', before['revision'] + 1)]
    assert client.get('/api/candidate').json() == before


def test_legacy_candidate_without_new_json_keys_is_readable_without_rewriting_storage(client):
    profile = {'summary': '旧版已确认简介', 'skills': ['Python']}
    preferences = {'city': '杭州'}
    with client.app.state.database.session_factory.begin() as session:
        row = session.get(Candidate, 1)
        row.profile = deepcopy(profile)
        row.preferences = deepcopy(preferences)
        row.revision = 3
        row.confirmed = True
    response = client.get('/api/candidate')
    assert response.status_code == 200
    candidate = response.json()
    assert candidate['revision'] == 3
    assert candidate['confirmed'] is False
    assert candidate['profile']['experience_years'] is None
    assert candidate['profile']['projects'] == ''
    assert {'profile.experience_years', 'profile.projects', 'preferences.commute_origin'} <= {
        item['field'] for item in candidate['missing']
    }
    assert client.get('/api/profile').json()['data']['summary'] == profile['summary']
    assert client.get('/api/preferences').json()['data']['city'] == preferences['city']
    with client.app.state.database.session_factory() as session:
        row = session.get(Candidate, 1)
        assert row.profile == profile
        assert row.preferences == preferences
        assert row.revision == 3


@pytest.mark.parametrize('section,field,value', CANDIDATE_MISSING)
def test_old_confirmation_marker_cannot_bypass_missing_candidate_fields(client, section, field, value):
    payload = candidate_payload(7)
    payload[section][field] = value
    # 只在兼容性用例中直接构造旧记录，模拟升级前已确认但不完整的资料。
    with client.app.state.database.session_factory.begin() as session:
        row = session.get(Candidate, 1)
        row.profile = payload['profile']
        row.preferences = payload['preferences']
        row.revision = 7
        row.confirmed = True
    old = client.get('/api/candidate').json()
    assert old['confirmed'] is False
    assert {item['field'] for item in old['missing']} == {section + '.' + field}
    assert all(item['label'] for item in old['missing'])
    assert client.get('/api/profile').status_code == 200
    assert client.get('/api/preferences').status_code == 200
    assert client.put('/api/profile', json={
        'revision': old['revision'], 'confirmed': True, 'data': old['profile'],
    }).status_code == 422
    job = client.post('/api/jobs', json=complete_job())
    assert job.status_code == 201
    for action, values in [('evaluate', {'job_ids': [job.json()['id']]}),
                           ('interview', {'job_id': job.json()['id']})]:
        response = client.post('/api/runs', json={'action': action, 'input': values})
        assert response.status_code == 409, response.text
    assert client.app.state.run_service.runs == {}
    assert client.get('/api/reports').json()['total'] == 0
    assert client.get('/api/candidate').json() == old
    repaired = client.put('/api/candidate', json=candidate_payload(old['revision']))
    assert repaired.status_code == 200
    assert repaired.json()['confirmed'] is True
    assert repaired.json()['missing'] == []
    assert repaired.json()['revision'] == 8


@pytest.mark.parametrize('section,field', [('profile', 'projects'), ('preferences', 'commute_origin')])
def test_personal_missing_fields_cannot_be_waived_with_blind_spots(client, section, field):
    before = client.get('/api/candidate').json()
    payload = candidate_payload(before['revision'])
    payload[section][field] = ''
    payload[section]['blind_spots'] = {field: '暂时无法提供，申请跳过'}
    assert client.put('/api/candidate', json=payload).status_code == 422
    assert client.get('/api/candidate').json() == before
    assert client.get('/api/profile').json()['confirmed'] is False


@pytest.mark.parametrize('field,value', JOB_MISSING)
def test_job_missing_field_rejects_create_and_preserves_existing_update(client, field, value):
    response = client.post('/api/jobs', json=complete_job())
    assert response.status_code == 201
    saved = response.json()
    before = client.get('/api/jobs').json()
    incomplete = complete_job(**{field: value})
    assert client.post('/api/jobs', json=incomplete).status_code == 422
    assert client.put('/api/jobs/' + saved['id'], json={
        'revision': saved['revision'], 'data': incomplete,
    }).status_code == 422
    assert client.get('/api/jobs').json() == before
    assert client.get('/api/jobs/' + saved['id']).json() == saved


@pytest.mark.parametrize('field,value', JOB_MISSING)
def test_explicit_job_blind_spot_saves_known_values_without_guessing(client, field, value):
    blind_key = 'salary' if field.startswith('salary_') else field
    payload = complete_job(**{field: value}, blind_spots={blind_key: '已核对原文，招聘方未披露'})
    response = client.post('/api/jobs', json=payload)
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved['ready'] is True
    assert saved['missing'] == []
    assert saved['data'][field] == value
    assert saved['data']['blind_spots'] == payload['blind_spots']
    for key in ('salary_min', 'salary_max'):
        assert saved['data'][key] == payload[key]
    assert client.get('/api/jobs/' + saved['id']).json() == saved
    assert client.get('/api/jobs').json()['items'] == [saved]
    updated = client.put('/api/jobs/' + saved['id'], json={
        'revision': saved['revision'], 'data': {**payload, 'notes': '后续向招聘方核实'},
    })
    assert updated.status_code == 200
    assert updated.json()['ready'] is True
    assert updated.json()['missing'] == []
    assert updated.json()['data'][field] == value


@pytest.mark.parametrize('blind_spots', [
    {}, {'address': ''}, {'address': '   '}, {'notes': '未披露地址'},
    {'salary': '未披露薪资'},
])
def test_blind_spot_requires_allowed_key_and_nonempty_matching_reason(client, blind_spots):
    payload = complete_job(address='', blind_spots=blind_spots)
    assert client.post('/api/jobs', json=payload).status_code == 422
    assert client.get('/api/jobs').json()['total'] == 0


@pytest.mark.parametrize('key', ['notes', 'salary_min'])
def test_complete_job_still_rejects_unsupported_blind_spot_keys(client, key):
    response = client.post('/api/jobs', json=complete_job(blind_spots={key: '招聘方未披露'}))
    assert response.status_code == 422
    assert client.get('/api/jobs').json()['total'] == 0


@pytest.mark.parametrize('salary', [
    {'salary_min': 0, 'salary_max': None},
    {'salary_min': -1, 'salary_max': None},
    {'salary_min': None, 'salary_max': 10000001},
    {'salary_min': 30000, 'salary_max': 20000},
])
def test_salary_blind_spot_does_not_waive_numeric_constraints(client, salary):
    response = client.post('/api/jobs', json=complete_job(
        **salary, blind_spots={'salary': '招聘方未披露完整范围'},
    ))
    assert response.status_code == 422
    assert client.get('/api/jobs').json()['total'] == 0


@pytest.mark.parametrize('unknown_fields', [('company',), ('title',), ('company', 'title')])
def test_unknown_company_or_title_never_merges_distinct_imports(client, unknown_fields):
    payload = complete_job(**dict.fromkeys(unknown_fields, ''),
                           blind_spots=dict.fromkeys(unknown_fields, '招聘方未披露'))
    first = client.post('/api/jobs', json=payload)
    second = client.post('/api/jobs', json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()['id'] != second.json()['id']
    assert first.json()['ready'] is second.json()['ready'] is True
    assert client.get('/api/jobs').json()['total'] == 2


def test_invalid_job_source_never_partially_creates_merges_or_updates(client):
    sources = []
    for name in ('A.md', 'B.md'):
        upload = client.post('/api/documents', data={'kind': 'knowledge'},
                             files={'file': (name, b'# source', 'text/markdown')})
        assert upload.status_code == 201
        sources.append(upload.json()['id'])
    response = client.post('/api/jobs', json=complete_job(source_ids=[sources[0]]))
    assert response.status_code == 201
    saved = response.json()
    before = client.get('/api/jobs').json()
    bad_sources = [sources[1], 'missing-source']
    for payload in (complete_job(company='不应新增', source_ids=bad_sources),
                    complete_job(source_ids=bad_sources)):
        assert client.post('/api/jobs', json=payload).status_code == 404
        assert client.get('/api/jobs').json() == before
    assert client.put('/api/jobs/' + saved['id'], json={
        'revision': saved['revision'],
        'data': complete_job(title='不应覆盖', source_ids=bad_sources),
    }).status_code == 404
    assert client.get('/api/jobs/' + saved['id']).json() == saved
    assert client.get('/api/documents').json()['total'] == 2


def test_legacy_job_is_readable_but_cannot_run_until_completed(client):
    ready_profile(client)
    old_data = {'company': '旧公司', 'title': '开发', 'jd_text': '原有招聘描述'}
    with client.app.state.database.session_factory.begin() as session:
        row = Job(data=old_data)
        session.add(row)
        session.flush()
        key = row.id
    response = client.get('/api/jobs/' + key)
    assert response.status_code == 200
    old = response.json()
    assert old['ready'] is False
    assert {item['field'] for item in old['missing']} == {'city', 'address', 'salary'}
    assert all(item['label'] for item in old['missing'])
    assert {field: old['data'][field] for field in old_data} == old_data
    assert client.get('/api/jobs').json()['items'] == [old]
    for action, values in [('evaluate', {'job_ids': [key]}), ('interview', {'job_id': key})]:
        assert client.post('/api/runs', json={'action': action, 'input': values}).status_code == 409
    assert client.app.state.run_service.runs == {}
    repaired = client.put('/api/jobs/' + key, json={
        'revision': old['revision'], 'data': complete_job(**old_data),
    })
    assert repaired.status_code == 200
    assert repaired.json()['ready'] is True
    assert repaired.json()['missing'] == []
    assert start(client, 'evaluate', job_ids=[key])['status'] == 'completed'


def test_explicit_job_blind_spots_allow_evaluation_and_interview(client):
    ready_profile(client)
    blind_spots = {'address': '招聘方未披露办公地址', 'salary': '招聘方未披露薪资'}
    response = client.post('/api/jobs', json=complete_job(
        address='', salary_min=None, salary_max=None, blind_spots=blind_spots,
    ))
    assert response.status_code == 201
    job = response.json()
    run = start(client, 'evaluate', job_ids=[job['id']])
    assert run['status'] == 'partial'
    result = run['result']['data']['jobs'][0]
    assert result['match_score'] is not None
    assert set(blind_spots.values()) <= set(result['missing'])
    assert result['commutes']['public_transit']['duration_minutes'] is None
    assert result['commutes']['driving']['duration_minutes'] is None
    report = client.get('/api/reports/' + run['report_id']).json()
    assert report['snapshot']['jobs'][0]['data']['blind_spots'] == blind_spots
    interview = start(client, 'interview', job_id=job['id'])
    assert interview['status'] == 'needs_input'
    assert interview['pending_input']['kind'] == 'question'
    assert client.post(f"/api/runs/{interview['id']}/input", json={
        'input_id': interview['pending_input']['id'], 'decision': 'finish',
    }).status_code == 202
    assert wait_run(client, interview['id'])['report_id']


@pytest.mark.parametrize('match_score', [82, None])
def test_new_evaluation_preserves_match_evidence_and_routes_without_old_scores(settings, match_score):
    matches = [
        {'requirement': 'Python', 'evidence': '维护接口平台', 'assessment': 'matched'},
        {'requirement': 'RAG', 'evidence': '题库检索项目', 'assessment': 'partial'},
        {'requirement': '生产部署', 'evidence': '尚无相关经历', 'assessment': 'gap'},
        {'requirement': '团队规模', 'evidence': ''},
    ]
    commutes = {
        'public_transit': {'duration_minutes': None if match_score is None else 45,
                           'distance_km': None if match_score is None else 18,
                           'route_summary': '路线待核实' if match_score is None else '步行到站，乘地铁后步行到园区'},
        'driving': {'duration_minutes': 30, 'distance_km': 16, 'route_summary': '经文一路和城市快速路到园区'},
    }

    async def handle(command, services, emit):
        job = command.snapshot['jobs'][0]
        return AgentResponse(status='partial' if match_score is None else 'completed',
                             kind='evaluation_report', data={
            'title': '匹配报告', 'summary': '固定测试结果', 'jobs': [{
                'job_id': job['id'], 'company': job['data']['company'], 'title': job['data']['title'],
                'match_score': match_score, 'matches': deepcopy(matches),
                'strengths': ['Python 自动化经验'], 'gaps': ['缺少生产部署经历'],
                'missing': ['匹配依据与公交路线待补充'] if match_score is None else [],
                'recommendation': '补充部署案例', 'evidence': [{'source': '简历', 'text': '维护接口平台'}],
                'commutes': deepcopy(commutes),
                'salary_score': 100, 'commute_score': 90, 'total_score': 88,
            }],
        })

    with TestClient(create_app(settings, handler=handle)) as client:
        ready_profile(client)
        response = client.post('/api/jobs', json=complete_job())
        assert response.status_code == 201
        run = start(client, 'evaluate', job_ids=[response.json()['id']])
        assert run['status'] == ('partial' if match_score is None else 'completed')
        result = run['result']['data']['jobs'][0]
        assert result['match_score'] == match_score
        assert not OLD_SCORES.intersection(result)
        assert result['matches'] == [dict(item, assessment=item.get('assessment', 'unknown')) for item in matches]
        assert result['strengths'] == ['Python 自动化经验']
        assert result['gaps'] == ['缺少生产部署经历']
        assert result['commutes'] == commutes
        assert result['recommendation'] == '补充部署案例'
        assert result['evidence'] == [{'source': '简历', 'text': '维护接口平台'}]
        assert bool(result['missing']) is (match_score is None)
        report = client.get('/api/reports/' + run['report_id']).json()
        assert report['data'] == run['result']['data']
        assert client.get('/api/reports').json()['items'] == [report]
        assert client.get(f"/api/reports/{report['id']}/export?format=json").json() == report
        markdown = client.get(f"/api/reports/{report['id']}/export?format=md")
        assert markdown.status_code == 200
        assert '匹配' in markdown.text and commutes['driving']['route_summary'] in markdown.text
        assert '总分' not in markdown.text
        assert all(field not in markdown.text for field in OLD_SCORES)


@pytest.mark.parametrize('changes', [
    {'match_score': -1}, {'match_score': 101}, {'match_score': float('inf')},
    {'matches': [{'requirement': 'Python', 'evidence': '简历', 'assessment': 'invented'}]},
    {'strengths': 'not-a-list'},
    {'commutes': {'driving': {'duration_minutes': -1}}},
    {'commutes': {'driving': {'distance_km': float('nan')}}},
])
def test_new_evaluation_contract_still_validates_match_and_route_fields(changes):
    with pytest.raises(ValidationError):
        validate_result('evaluation_report', {'jobs': [{
            'job_id': 'job-a', 'company': '测试公司', 'title': '开发', **changes,
        }]})


def test_old_report_scores_and_snapshot_survive_edits_deletion_and_restart(client, settings):
    candidate = ready_profile(client)
    response = client.post('/api/jobs', json=complete_job())
    assert response.status_code == 201
    job = response.json()
    data = {'title': '旧版总分报告', 'summary': '保留历史事实', 'jobs': [{
        'job_id': job['id'], 'company': job['data']['company'], 'title': job['data']['title'],
        'match_score': 80, 'salary_score': 100, 'commute_score': 80, 'total_score': 85,
    }]}
    snapshot = {'profile': client.get('/api/profile').json(),
                'preferences': client.get('/api/preferences').json(), 'jobs': [job]}
    # 旧报告直接按当时 JSON 入库，不能先经过新结果契约而丢失历史字段。
    with client.app.state.database.session_factory.begin() as session:
        row = Report(kind='evaluation', data=data, snapshot=snapshot,
                     run_id='legacy-workbench-report', is_demo=True)
        session.add(row)
        session.flush()
        key = row.id
    before = client.get('/api/reports/' + key).json()
    assert before['data'] == data
    assert before['snapshot'] == snapshot
    assert client.put('/api/candidate', json=candidate_payload(
        candidate['revision'], profile=complete_profile(name='新姓名'),
        preferences=complete_preferences(city='上海'),
    )).status_code == 200
    assert client.put('/api/jobs/' + job['id'], json={
        'revision': job['revision'], 'data': complete_job(jd_text='新的招聘要求'),
    }).status_code == 200
    assert start(client, 'evaluate', job_ids=[job['id']])['report_id'] != key
    assert client.delete('/api/jobs/' + job['id']).status_code == 204
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get('/api/reports/' + key).json() == before
        reports = restarted.get('/api/reports').json()['items']
        assert next(item for item in reports if item['id'] == key) == before
        assert restarted.get(f'/api/reports/{key}/export?format=json').json() == before


def test_demo_drafts_require_explicit_confirmation_and_partial_profile_cannot_save(client):
    before = client.get('/api/candidate').json()
    normal = start(client, 'parse_resume')
    assert normal['status'] == 'completed'
    assert normal['result']['kind'] == 'profile_draft'
    data = normal['result']['data']
    assert isinstance(data['diagnosis'], list)
    assert data['notice']
    assert client.get('/api/candidate').json() == before
    saved = client.put('/api/candidate', json={
        'revision': before['revision'], 'profile': data['profile'], 'preferences': data['preferences'],
    })
    assert saved.status_code == 200
    partial = start(client, 'parse_resume', demo_scenario='partial')
    data = partial['result']['data']
    assert partial['result']['kind'] == 'profile_draft'
    assert client.put('/api/candidate', json={
        'revision': saved.json()['revision'], 'profile': data['profile'], 'preferences': data['preferences'],
    }).status_code == 422
    assert client.get('/api/candidate').json() == saved.json()


def test_profile_draft_requires_both_sections_and_diagnosis_defaults_to_empty():
    data = {'profile': complete_profile(), 'preferences': complete_preferences()}
    assert validate_result('profile_draft', data)['diagnosis'] == []
    for missing in ('profile', 'preferences'):
        with pytest.raises(ValidationError):
            validate_result('profile_draft', {key: value for key, value in data.items() if key != missing})
    assert validate_result('job_drafts', {'jobs': [complete_job()]})['diagnosis'] == []


@pytest.mark.parametrize('scenario', ['success', 'partial'])
def test_demo_job_drafts_do_not_save_before_review(client, scenario):
    run = start(client, 'extract_jobs', text='固定测试原文', demo_scenario=scenario)
    assert run['result']['kind'] == 'job_drafts'
    data = run['result']['data']
    assert isinstance(data['diagnosis'], list)
    assert data['jobs'] and data['notice']
    assert client.get('/api/jobs').json()['total'] == 0
    if scenario == 'partial':
        assert any(job.get('blind_spots', {}).get('address') for job in data['jobs'])
    for draft in data['jobs']:
        saved = client.post('/api/jobs', json=draft)
        assert saved.status_code == 201, saved.text
        assert saved.json()['ready'] is True


@pytest.mark.parametrize('action', ['parse_resume', 'extract_jobs'])
def test_clarification_resumes_original_action_without_automatic_business_save(client, action):
    before = client.get('/api/candidate').json()
    run = start(client, action, demo_scenario='needs_input')
    assert run['status'] == 'needs_input'
    assert run['pending_input']['kind'] == 'question'
    assert client.post('/api/runs', json={'action': 'chat', 'input': {'text': '另一个活动'}}).status_code == 409
    body = {'input_id': run['pending_input']['id'], 'decision': 'answer', 'text': '已补充并核对'}
    assert client.post(f"/api/runs/{run['id']}/input", json=body).status_code == 202
    resumed = wait_run(client, run['id'])
    assert resumed['action'] == action
    assert resumed['status'] == 'completed'
    assert resumed['result']['kind'] == ('profile_draft' if action == 'parse_resume' else 'job_drafts')
    assert client.post(f"/api/runs/{run['id']}/input", json=body).status_code == 409
    assert client.get('/api/candidate').json() == before
    assert client.get('/api/jobs').json()['total'] == 0


def test_running_activity_blocks_new_activity_and_releases_after_completion(settings):
    entered, release = Event(), Event()

    async def handle(command, services, emit):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return AgentResponse(kind='chat', data={'message': '已完成'})

    with TestClient(create_app(settings, handler=handle)) as client:
        response = client.post('/api/runs', json={'action': 'chat', 'input': {'text': '开始'}})
        assert response.status_code == 202
        try:
            assert entered.wait(timeout=2)
            active = client.get('/api/runs').json()['active']
            assert active['id'] == response.json()['id']
            assert active['status'] == 'running'
            assert client.post('/api/runs', json={'action': 'chat', 'input': {'text': '冲突'}}).status_code == 409
        finally:
            release.set()
        assert wait_run(client, response.json()['id'])['status'] == 'completed'
        assert client.get('/api/runs').json()['active'] is None
        assert start(client, 'chat', text='下一次')['status'] == 'completed'
