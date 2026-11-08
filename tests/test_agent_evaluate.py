"""岗位评估通过正式入口调用结构化模型，离线核对证据、分数和岗位归属。"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI

from backend.config import Settings
from backend.contracts import AgentCommand, JobMatchAnalysis, RequirementMatch, RunInput, RunStart, validate_result
from backend.exceptions import AppError
from backend.services.report_service import markdown
from backend.services.run_service import Run, RunService
from career_agent import evaluate
from career_agent.agent import handle
from career_agent.config import AgentSettings
from career_agent.tools import amap
from workbench_fixtures import complete_job, complete_profile


@pytest.mark.parametrize('judgments, expected', [
    ([], None), (['资料不足'], None), (['不符合'], 0),
    (['符合', '部分符合', '资料不足'], 50), (['符合', '符合', '不符合'], 66.7),
])
def test_match_score(judgments, expected):
    matches = [RequirementMatch(requirement='要求', assessment=value) for value in judgments]
    assert evaluate.match_score(matches) == expected


@pytest.mark.parametrize('valid_output', [True, False])
def test_evaluate_uses_one_snapshot_job_per_call(monkeypatch, valid_output):
    job = {'id': 'a', 'data': complete_job(title='岗位甲')}
    profile = complete_profile(experience='维护接口测试平台')
    snapshot = {'profile': {'data': profile}, 'jobs': [job]}
    command = AgentCommand('run', 'evaluate', {}, {}, snapshot, {}, {'decision': 'start'})
    received = []
    experiences = '1. 维护接口测试平台，符合 Python 开发要求。\n2. 开发 RAG 题库检索项目，符合检索开发要求。'

    class OfflineChat(ChatOpenAI):
        async def _agenerate(self, messages, **kwargs):
            source = json.loads(messages[-1].content)
            received.append(source)
            schema = kwargs['tools'][0]['function']
            assert set(schema['parameters']['properties']) == {
                'matches', 'supporting_experiences', 'key_requirement', 'recommendation', 'evidence',
            }
            for name in ('supporting_experiences', 'key_requirement'):
                assert schema['parameters']['properties'][name]['type'] == 'string'
            text = source['profile']['experience']
            output = {
                'matches': [{'requirement': 'Python', 'evidence': text, 'assessment': '符合'}],
                'supporting_experiences': experiences, 'key_requirement': '',
                'recommendation': source['job']['title'], 'evidence': [{'source': '工作经历', 'text': text}],
            }
            args = json.dumps(output, ensure_ascii=False) if valid_output else 'invalid'
            message = AIMessage(content='', additional_kwargs={'tool_calls': [{
                'id': 'call', 'type': 'function', 'function': {'name': schema['name'], 'arguments': args},
            }]}, usage_metadata={'input_tokens': 2, 'output_tokens': len(received), 'total_tokens': 2 + len(received)})
            return ChatResult(generations=[ChatGeneration(message=message)])

    monkeypatch.setattr(evaluate, 'build_chat', lambda: OfflineChat(model='offline', api_key='test'))
    monkeypatch.setattr(amap, 'AgentSettings', lambda: AgentSettings(_env_file=None, amap_api_key=''))
    response = asyncio.run(handle(command, None, AsyncMock()))
    if not valid_output:
        assert response.status == 'failed' and response.kind == 'chat'
        assert response.usage['total_tokens'] == 3 and len(received) == 1
        return
    assert received == [{'profile': profile, 'job': job['data']}]
    assert response.status == 'partial' and response.usage['total_tokens'] == 3
    data = validate_result(response.kind, response.data)
    assert 'AMAP_API_KEY' in data['summary']
    row, = data['jobs']
    assert row['job_id'] == job['id'] and row['recommendation'] == job['data']['title']
    assert row['match_score'] == 100 and row['commutes'] == {}
    assert row['matches'][0]['evidence'] == row['evidence'][0]['text'] == profile['experience']
    assert row['supporting_experiences'] == experiences and row['key_requirement'] == ''
    exported = markdown({'kind': 'evaluation', 'data': data, 'snapshot': snapshot, 'is_demo': False})
    assert '## 测试科技 · 岗位甲' in exported
    assert '### 最有利的经历\n\n' + experiences in exported
    assert '### 最不利的关键要求' not in exported


@pytest.fixture
def matching_model(monkeypatch):
    analysis = JobMatchAnalysis(matches=[RequirementMatch(requirement='Python', assessment='符合', evidence='开发接口平台')])
    invoke = AsyncMock(return_value={'parsed': analysis, 'raw': SimpleNamespace(usage_metadata={'total_tokens': 42})})
    model = Mock()
    model.with_structured_output.return_value.ainvoke = invoke
    monkeypatch.setattr(evaluate, 'build_chat', lambda: model)
    monkeypatch.setattr(amap, 'AgentSettings', lambda: AgentSettings(_env_file=None, amap_api_key='offline-test'))
    return invoke


def test_confirm_endpoints_resumes_routes_only(tmp_path, monkeypatch, matching_model):
    # 走实际运行容器、Agent 和地图工具，只替换模型和地图 HTTP 返回。
    calls = []
    async def map_request(path, **params):
        calls.append((path, params))
        if path == '/v5/place/text':
            if params['keywords'] == '出发园区':
                return {'pois': [{'name': name, 'location': coords, 'citycode': '021'}
                                 for name, coords in [('东区', '1,1'), ('西区', '2,2')]]}
            assert params['keywords'] in ('办公园区', '其他路1号')
            return {'pois': [{'name': '南楼', 'location': '3,3', 'citycode': '0571'}]}
        route = {'distance': '3500', 'cost': {'duration': '900'}}
        return {'route': {'transits': [route], 'paths': [route]}}

    monkeypatch.setattr(amap, 'map_request', map_request)
    jobs = [{'id': 'a', 'data': complete_job(address='办公园区')}]
    snapshot = {'profile': {'data': complete_profile(commute_origin='出发园区', city='上海')}, 'jobs': jobs}
    reports = Mock()
    reports.save_report.return_value = {'id': 'report'}
    service = RunService(Settings(_env_file=None, data_dir=tmp_path), reports, None, handle)
    run = Run('test', 'evaluate', {}, {}, snapshot, False)
    service.runs[run.id] = run

    async def answer(start, end):
        await service.respond(run.id, RunInput(input_id=run.pending_input['id'], decision='answer', data={'start': start, 'end': end}))
        await run.task

    async def scenario():
        reports.candidate.return_value = {**snapshot['profile'], 'confirmed': True}
        second = RunStart(action='evaluate', input={'job_ids': ['b']})
        with pytest.raises(AppError) as busy:
            await service.start(second)
        assert busy.value.code == 'RUN_BUSY'
        await service.execute(run)
        assert run.status == 'needs_input' and run.result['data']['place_choices'] == {'start': ['东区', '西区'], 'end': ['南楼']}
        assert matching_model.await_count == 1 and len(calls) == 2
        with pytest.raises(AppError) as waiting:
            await service.start(second)
        assert waiting.value.code == 'RUN_BUSY'
        input_id = run.pending_input['id']
        with pytest.raises(AppError) as invalid:
            await answer('0', '1')
        assert invalid.value.status == 422 and run.pending_input['id'] == input_id
        assert run.status == 'needs_input' and run.result['data']['place_choices'] == {'start': ['东区', '西区'], 'end': ['南楼']}
        assert len(calls) == 2 and not reports.save_report.called
        await answer('2', '1')
        assert run.status == 'completed' and matching_model.await_count == 1
        assert run.usage == {'total_tokens': 42} and run.previous_usage is None
        reports.save_report.assert_called_once()
        # A 完成后才显式开始 B；B 的匹配也只执行一次。
        reports.job.return_value = {'id': 'b', 'data': complete_job(address='其他路1号')}
        started = await service.start(second)
        next_run = service.runs[started['id']]
        await next_run.task
        assert next_run.status == 'needs_input' and matching_model.await_count == 2

    asyncio.run(scenario())
    assert [path for path, _ in calls[2:4]] == ['/v5/direction/transit/integrated', '/v5/direction/driving']
    for _, params in calls[2:4]:
        assert (params['origin'], params['destination']) == ('2,2', '3,3')
    assert (calls[2][1]['city1'], calls[2][1]['city2']) == ('021', '0571')
    row, = run.result['data']['jobs']
    assert row['match_score'] == 100
    assert row['commutes']['public_transit']['duration_minutes'] == 15
    assert row['commutes']['driving']['distance_km'] == 3.5


def test_evaluate_rejects_multiple_jobs_before_agent(tmp_path):
    repo = Mock()
    repo.candidate.return_value = {'confirmed': True, 'data': complete_profile()}
    service = RunService(Settings(_env_file=None, data_dir=tmp_path), repo, None, AsyncMock())
    with pytest.raises(AppError) as error:
        asyncio.run(service.start(RunStart(action='evaluate', input={'job_ids': ['a', 'b']})))
    assert error.value.status == 422
    assert not service.runs
    repo.job.assert_not_called()


@pytest.mark.parametrize('outcome', ['no-place', 'partial', 'location-error', '413'])
def test_commute_without_complete_route(monkeypatch, matching_model, outcome):
    home = {'name': '上海起点', 'location': '1,1', 'citycode': '021'}
    office = {'name': '杭州办公楼', 'location': '2,2', 'citycode': '0571'}
    search = AsyncMock(side_effect=[[home], [] if outcome == 'no-place' else [office]])
    if outcome == 'location-error':
        search.side_effect = AppError(502, 'AMAP_UNAVAILABLE', '高德定位失败')
    monkeypatch.setattr(evaluate, 'search_places', search)
    commute = AsyncMock(return_value={
        'public_transit': {'duration_minutes': None, 'distance_km': None, 'route_summary': '无公交路线'},
        'driving': {'duration_minutes': 15, 'distance_km': 4, 'route_summary': '测试路线'},
    })
    if outcome == '413':
        commute.side_effect = AppError(413, 'AMAP_REQUEST_TOO_LARGE', '高德请求过大')
    monkeypatch.setattr(evaluate, 'calculate_commute', commute)
    snapshot = {'profile': {'data': complete_profile(commute_origin='上海起点')},
                'jobs': [{'id': 'a', 'data': complete_job(address='西湖区')}]}
    cmd = AgentCommand('run', 'evaluate', {}, {}, snapshot, {}, {'decision': 'start'})
    result = asyncio.run(handle(cmd, None, AsyncMock()))
    if outcome in ('no-place', 'location-error'):
        assert result.status == 'partial' and result.data['jobs'][0]['match_score'] == 100
        assert ('办公地点' if outcome == 'no-place' else '高德定位失败') in result.data['summary']
        commute.assert_not_awaited()
        return
    assert result.status == 'needs_input'
    cmd.state = json.loads(json.dumps(result.state))
    cmd.human_input = {'decision': 'answer', 'data': {'start': '1', 'end': '1'}}
    if outcome == '413':
        with pytest.raises(AppError) as error:
            asyncio.run(handle(cmd, None, AsyncMock()))
        assert error.value.status == 413
    else:
        result = asyncio.run(handle(cmd, None, AsyncMock()))
        assert result.status == 'partial' and 'usage' not in result.model_fields_set
        row, = validate_result(result.kind, result.data)['jobs']
        assert row['match_score'] == 100 and row['commutes']['driving']['duration_minutes'] == 15
    commute.assert_awaited_once_with(home, office)
    assert search.await_count == 2 and matching_model.await_count == 1
