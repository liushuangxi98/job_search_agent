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
from workbench_fixtures import candidate_payload, complete_job, complete_profile, upload_image


CANDIDATE_MISSING = [
    ('profile', 'name', ''),
    ('profile', 'education', ''),
    ('profile', 'major', ''),
    ('profile', 'summary', ''),
    ('profile', 'commute_mode', None),
    ('profile', 'experience_years', None),
    ('profile', 'skills', []),
    ('profile', 'experience', ''),
    ('profile', 'projects', ''),
    ('profile', 'direction', ''),
    ('profile', 'city', ''),
    ('profile', 'commute_origin', ''),
    ('profile', 'salary_min', None),
    ('profile', 'salary_max', None),
]
JOB_MISSING = [
    ('company', ''), ('title', ''), ('city', ''), ('address', ''), ('job_description', ''),
    ('salary_min', None), ('salary_max', None), ('job_requirement', ''), ('experience_years', None), ('education', ''),
]
OLD_SCORES = {'salary_score', 'commute_score', 'total_score'}


def test_candidate_saves_flat_profile_and_increments_revision_once(client, settings):
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
    assert {key: saved['profile'][key] for key in payload['profile']} == payload['profile']
    legacy = client.get('/api/profile').json()
    assert legacy['revision'] == saved['revision']
    assert legacy['data'] == saved['profile']
    assert 'preferences' not in saved
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get('/api/candidate').json() == saved


@pytest.mark.parametrize('section,field,value', CANDIDATE_MISSING + [
    ('profile', 'name', '   '), ('profile', 'skills', ['   ']),
])
def test_candidate_missing_field_rejects_complete_profile_atomically(client, section, field, value):
    before = ready_profile(client)
    payload = candidate_payload(before['revision'], profile=complete_profile(name='不应写入', city='上海'))
    payload[section][field] = value
    response = client.put('/api/candidate', json=payload)
    assert response.status_code == 422, response.text
    assert client.get('/api/candidate').json() == before
    assert client.get('/api/profile').json()['data'] == before['profile']


@pytest.mark.parametrize('section,field,value', [
    ('profile', 'experience_years', -1),
    ('profile', 'experience_years', 'NaN'),
    ('profile', 'experience_years', 'Infinity'),
    pytest.param('profile', 'projects', '项' * 20001, id='projects-too-long'),
    ('profile', 'salary_min', 0),
    ('profile', 'salary_min', 40000),
])
def test_candidate_invalid_values_do_not_write(client, section, field, value):
    before = ready_profile(client)
    payload = candidate_payload(before['revision'])
    payload[section][field] = value
    assert client.put('/api/candidate', json=payload).status_code == 422
    assert client.get('/api/candidate').json() == before


def test_stale_candidate_revision_preserves_saved_profile(client):
    original = ready_profile(client)
    winner = client.put('/api/candidate', json=candidate_payload(
        original['revision'], profile=complete_profile(name='最新姓名', city='上海'),
    ))
    assert winner.status_code == 200
    saved = winner.json()
    assert saved['revision'] == original['revision'] + 1
    stale = candidate_payload(original['revision'], profile=complete_profile(name='旧表单', city='深圳'))
    assert client.put('/api/candidate', json=stale).status_code == 409
    assert client.get('/api/candidate').json() == saved
    assert client.put('/api/profile', json={
        'revision': original['revision'], 'confirmed': True, 'data': stale['profile'],
    }).status_code == 409
    assert client.get('/api/candidate').json() == saved


def test_candidate_omitted_direction_is_not_silently_confirmed(client):
    before = client.get('/api/candidate').json()
    payload = candidate_payload(before['revision'])
    del payload['profile']['direction']
    response = client.put('/api/candidate', json=payload)
    assert response.status_code == 422
    assert client.get('/api/candidate').json() == before


def test_legacy_job_reimport_reuses_id_and_adds_evidence(client):
    source = upload_image(client)
    original = client.post('/api/jobs', json=complete_job(evidence=[{'document_id': source}])).json()
    with client.app.state.database.session_factory.begin() as session:
        row = session.get(Job, original['id'])
        legacy = dict(row.data)
        legacy.pop('evidence')
        row.data = legacy
    result = client.post('/api/jobs', json=complete_job(evidence=[{'document_id': source, 'text': '新提取原文'}]))
    assert result.status_code == 201
    merged = result.json()
    assert merged['id'] == original['id']
    assert {item['document_id'] for item in merged['data']['evidence']} == {source}
    assert merged['revision'] == original['revision'] + 1
    assert client.get('/api/jobs').json()['total'] == 1


def test_candidate_database_failure_rolls_back_complete_profile_and_revision(client):
    before = ready_profile(client)
    payload = candidate_payload(before['revision'], profile=complete_profile(name='事务中的姓名', city='上海'))
    factory = client.app.state.database.session_factory
    flushed = []

    def fail_commit(session):
        # 在真实事务已 flush 后失败，验证数据库回滚，而非仅请求入参被拒绝。
        session.flush()
        row = session.get(Candidate, 1)
        flushed.append((row.profile['name'], row.profile['city'], row.revision))
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
    profile = {'summary': '旧版已确认简介', 'skills': ['Python'], 'city': '杭州'}
    with client.app.state.database.session_factory.begin() as session:
        row = session.get(Candidate, 1)
        row.profile = deepcopy(profile)
        row.revision = 3
        row.confirmed = True
    response = client.get('/api/candidate')
    assert response.status_code == 200
    candidate = response.json()
    assert candidate['revision'] == 3
    assert candidate['confirmed'] is False
    assert candidate['profile']['experience_years'] is None
    assert candidate['profile']['projects'] == ''
    assert {'profile.experience_years', 'profile.projects', 'profile.commute_origin'} <= {
        item['field'] for item in candidate['missing']
    }
    assert client.get('/api/profile').json()['data']['summary'] == profile['summary']
    assert client.get('/api/profile').json()['data']['city'] == profile['city']
    with client.app.state.database.session_factory() as session:
        row = session.get(Candidate, 1)
        assert row.profile == profile
        assert row.revision == 3


@pytest.mark.parametrize('section,field,value', CANDIDATE_MISSING)
def test_old_confirmation_marker_cannot_bypass_missing_candidate_fields(client, section, field, value):
    payload = candidate_payload(7)
    payload[section][field] = value
    # 只在兼容性用例中直接构造旧记录，模拟升级前已确认但不完整的资料。
    with client.app.state.database.session_factory.begin() as session:
        row = session.get(Candidate, 1)
        row.profile = payload['profile']
        row.revision = 7
        row.confirmed = True
    old = client.get('/api/candidate').json()
    assert old['confirmed'] is False
    assert {item['field'] for item in old['missing']} == {section + '.' + field}
    assert all(item['label'] for item in old['missing'])
    assert client.get('/api/profile').status_code == 200
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


@pytest.mark.parametrize('section,field', [('profile', 'projects'), ('profile', 'commute_origin')])
def test_personal_missing_fields_cannot_be_waived_with_blind_spots(client, section, field):
    before = client.get('/api/candidate').json()
    payload = candidate_payload(before['revision'])
    payload[section][field] = ''
    payload[section]['blind_spots'] = {field: '暂时无法提供，申请跳过'}
    assert client.put('/api/candidate', json=payload).status_code == 422
    assert client.get('/api/candidate').json() == before
    assert client.get('/api/profile').json()['confirmed'] is False


@pytest.mark.parametrize('field,value', JOB_MISSING)
def test_missing_job_fields_block_create_and_edit(client, field, value):
    payload = complete_job(**{field: value})
    rejected = client.post('/api/jobs', json=payload)
    assert rejected.status_code == 422
    assert rejected.json()['error']['code'] == 'JOB_INCOMPLETE'
    assert client.get('/api/jobs').json()['total'] == 0
    saved = client.post('/api/jobs', json=complete_job()).json()
    assert client.put('/api/jobs/' + saved['id'], json={
        'revision': saved['revision'], 'data': payload,
    }).status_code == 422
    assert client.get('/api/jobs/' + saved['id']).json() == saved


@pytest.mark.parametrize('field', ['jd_text', 'notes', 'blind_spots', 'salary_note'])
def test_retired_job_fields_are_rejected_in_new_requests(client, field):
    assert client.post('/api/jobs', json={**complete_job(), field: {}}).status_code == 422
    assert client.get('/api/jobs').json()['total'] == 0


@pytest.mark.parametrize('values', [
    {'salary_min': 0}, {'salary_min': -1}, {'salary_max': 10000001},
    {'salary_min': 30000, 'salary_max': 20000},
    {'experience_years': -1}, {'experience_years': 81},
    {'experience_years': 'NaN'}, {'experience_years': 'Infinity'},
])
def test_known_job_numbers_still_require_valid_values(client, values):
    assert client.post('/api/jobs', json=complete_job(**values)).status_code == 422
    assert client.get('/api/jobs').json()['total'] == 0


@pytest.mark.parametrize('unknown_fields', [('company',), ('title',), ('company', 'title')])
def test_unknown_company_or_title_cannot_be_imported(client, unknown_fields):
    payload = complete_job(**dict.fromkeys(unknown_fields, ''))
    assert client.post('/api/jobs', json=payload).status_code == 422
    assert client.get('/api/jobs').json()['total'] == 0


def test_invalid_job_source_never_partially_creates_merges_or_updates(client):
    source = upload_image(client)
    response = client.post('/api/jobs', json=complete_job(evidence=[{'document_id': source}]))
    assert response.status_code == 201
    saved = response.json()
    before = client.get('/api/jobs').json()
    bad_evidence = [{'document_id': 'missing-source'}]
    for payload in (complete_job(company='不应新增', evidence=bad_evidence),
                    complete_job(evidence=bad_evidence)):
        assert client.post('/api/jobs', json=payload).status_code == 404
        assert client.get('/api/jobs').json() == before
    assert client.put('/api/jobs/' + saved['id'], json={
        'revision': saved['revision'],
        'data': complete_job(title='不应覆盖', evidence=bad_evidence),
    }).status_code == 404
    assert client.get('/api/jobs/' + saved['id']).json() == saved
    assert client.get('/api/documents').json()['total'] == 1


def test_legacy_job_is_readable_and_can_be_evaluated_with_unknown_fields(client):
    ready_profile(client)
    old_data = {'company': '旧公司', 'title': '开发', 'jd_text': '原有招聘描述'}
    with client.app.state.database.session_factory.begin() as session:
        record = Job(data=old_data)
        session.add(record)
        session.flush()
        key = record.id
    result = client.get('/api/jobs/' + key).json()
    assert result['data']['job_description'] == old_data['jd_text']
    assert result['data']['job_requirement'] == ''
    assert result['data']['experience_years'] is None
    assert result['data']['education'] == ''
    assert start(client, 'evaluate', job_ids=[key])['status'] == 'partial'


def test_legacy_undisclosed_job_data_allows_evaluation_and_interview(client):
    ready_profile(client)
    with client.app.state.database.session_factory.begin() as session:
        record = Job(data=complete_job(address='', salary_min=None, salary_max=None))
        session.add(record)
        session.flush()
        job = {'id': record.id}
    run = start(client, 'evaluate', job_ids=[job['id']])
    assert run['status'] == 'partial'
    result = run['result']['data']['jobs'][0]
    assert result['match_score'] is not None
    assert result['commutes']['public_transit']['duration_minutes'] is None
    assert result['commutes']['driving']['duration_minutes'] is None
    report = client.get('/api/reports/' + run['report_id']).json()
    assert report['snapshot']['jobs'][0]['data']['salary_min'] is None
    interview = start(client, 'interview', job_id=job['id'])
    assert interview['status'] == 'needs_input'
    assert client.post(f"/api/runs/{interview['id']}/input", json={
        'input_id': interview['pending_input']['id'], 'decision': 'finish',
    }).status_code == 202
    assert wait_run(client, interview['id'])['report_id']



@pytest.mark.parametrize('match_score', [82, None])
def test_new_evaluation_preserves_match_evidence_and_routes_without_old_scores(settings, match_score):
    matches = [
        {'requirement': 'Python', 'evidence': '维护接口平台', 'assessment': '符合'},
        {'requirement': 'RAG', 'evidence': '题库检索项目', 'assessment': '部分符合'},
        {'requirement': '生产部署', 'evidence': '尚无相关经历', 'assessment': '不符合'},
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
                'supporting_experiences': '1. Python 自动化经验符合开发要求。', 'key_requirement': '缺少生产部署经历',
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
        assert result['matches'] == [dict(item, assessment=item.get('assessment', '资料不足')) for item in matches]
        assert result['supporting_experiences'] == '1. Python 自动化经验符合开发要求。'
        assert result['key_requirement'] == '缺少生产部署经历'
        assert result['commutes'] == commutes
        assert result['recommendation'] == '补充部署案例'
        assert result['evidence'] == [{'source': '简历', 'text': '维护接口平台'}]
        assert not {'company', 'title', 'strengths', 'gaps', 'missing'}.intersection(result)
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
    {'supporting_experiences': ['不是字符串']},
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
                'preferences': {'revision': candidate['revision'], 'data': {'city': '杭州'}}, 'jobs': [job]}
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
        candidate['revision'], profile=complete_profile(name='新姓名', city='上海'),
    )).status_code == 200
    assert client.put('/api/jobs/' + job['id'], json={
        'revision': job['revision'], 'data': complete_job(job_description='新的招聘要求'),
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
    assert 'profile' not in data and 'diagnosis' not in data
    assert client.get('/api/candidate').json() == before
    saved = client.put('/api/candidate', json={
        'revision': before['revision'], 'profile': data,
    })
    assert saved.status_code == 200
    partial = start(client, 'parse_resume', demo_scenario='partial')
    data = partial['result']['data']
    assert partial['result']['kind'] == 'profile_draft'
    assert client.put('/api/candidate', json={
        'revision': saved.json()['revision'], 'profile': data,
    }).status_code == 422
    assert client.get('/api/candidate').json() == saved.json()


def test_drafts_use_business_schemas_directly():
    assert validate_result('profile_draft', complete_profile())['name'] == complete_profile()['name']
    assert validate_result('job_drafts', complete_job())['title'] == complete_job()['title']
    for job_list in ([], [complete_job()], [complete_job(), complete_job()]):
        with pytest.raises(ValidationError):
            AgentResponse(kind='job_drafts', data=job_list)
    for kind, wrapped in [('profile_draft', {'profile': complete_profile()}), ('job_drafts', {'jobs': []})]:
        with pytest.raises(ValidationError):
            validate_result(kind, wrapped)


@pytest.mark.parametrize('key', ['document_id', 'document_ids'])
def test_extract_jobs_rejects_image_list_before_start(client, key):
    response = client.post('/api/runs', json={'action': 'extract_jobs', 'input': {key: ['A', 'B']}})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'SINGLE_IMAGE_REQUIRED'
    assert not client.app.state.run_service.runs


@pytest.mark.parametrize('scenario', ['success', 'partial'])
def test_demo_job_drafts_do_not_save_before_review(client, scenario):
    run = start(client, 'extract_jobs', text='固定测试原文', demo_scenario=scenario)
    assert run['result']['kind'] == 'job_drafts'
    data = run['result']['data']
    assert isinstance(data, dict) and data['title']
    assert client.get('/api/jobs').json()['total'] == 0
    if scenario == 'partial':
        assert not data['address']
        assert client.post('/api/jobs', json=data).status_code == 422
        data['address'] = '用户核实的办公地址'
    saved = client.post('/api/jobs', json=data)
    assert saved.status_code == 201, saved.text
    assert client.get('/api/jobs').json()['total'] == 1


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
