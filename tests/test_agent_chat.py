"""不调用真实模型，验证聊天上下文、会话隔离和人工确认契约。"""
import asyncio
import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI

from backend.contracts import AgentCommand, AgentResponse, ChatSummary, validate_result
from backend.config import Settings
from backend.services.run_service import Run, RunService
from career_agent import agent, llm
from workbench_fixtures import complete_job, complete_profile


def command(text='这个岗位适合我吗？', session='session-a', job='job-a', human=None, state=None):
    return AgentCommand(
        run_id='run-' + text, action='chat', input={'text': text},
        context={'type': 'job', 'id': job, 'session_id': session},
        snapshot={
            'profile': {'revision': 2, 'confirmed': True, 'data': complete_profile(skills=['Python'])},
            'context_job': {'id': job, 'revision': 3, 'data': complete_job(title='Agent开发', jd_text='要求 RAG')},
        },
        state=state or {}, human_input=human,
    )


async def emit(*args, **kwargs):
    pass


@pytest.fixture
def service(tmp_path):
    return RunService(Settings(_env_file=None, data_dir=tmp_path), None, None, agent.handle)


def run_chat(service, value):
    run = Run(value.run_id, value.action, value.input, value.context, value.snapshot, False)
    asyncio.run(service.execute(run, value.human_input))
    return run


def install_model(monkeypatch, answer, usage=None):
    """只替换网络生成，保留真正的 with_structured_output 和 Pydantic 解析链。"""
    class OfflineChat(ChatOpenAI):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            roles = {'system': 'system', 'human': 'user', 'ai': 'assistant'}
            values = [{'role': roles[m.type], 'content': m.content} for m in messages]
            content = await answer(values)
            assert kwargs['tools'][0]['function']['name'] == 'ChatDecision'
            assert kwargs['tool_choice']['function']['name'] == 'ChatDecision'
            assert kwargs['extra_body']['thinking'] == {'type': 'disabled'}
            calls = [{'id': 'offline-call', 'type': 'function',
                      'function': {'name': 'ChatDecision', 'arguments': content}}] if isinstance(content, str) else []
            message = AIMessage(content='', additional_kwargs={'tool_calls': calls}, usage_metadata=usage)
            return ChatResult(generations=[ChatGeneration(message=message)])
    model = OfflineChat(model='deepseek-flash', api_key='offline-test-key',
                        base_url='http://127.0.0.1:1', max_retries=0)
    monkeypatch.setattr(llm, 'build_chat', lambda: model)


@pytest.fixture
def model_stub(monkeypatch):
    def install(answer, usage=None):
        async def generate(messages):
            return json.dumps(await answer(messages), ensure_ascii=False)
        install_model(monkeypatch, generate, usage)
    return install


def test_history_follows_session_across_jobs_pages_and_reports(service, model_stub):
    captured = []
    async def answer(messages):
        captured.append(messages)
        return {'msg_kind': 'reply', 'message': '建议补充 RAG 项目'}
    model_stub(answer)
    first = command('请记住我有四年测试经验')
    second = command('换到这个岗位后该怎么准备？', job='job-b')
    page = command('回到画像页，我刚才说了几年经验？')
    page.context = {'type': 'profile', 'session_id': 'session-a'}
    page.snapshot.pop('context_job')
    report = command('再看看这份报告')
    report.context = {'type': 'report', 'id': 'report-a', 'session_id': 'session-a'}
    report.snapshot.pop('context_job')
    report.snapshot['context_report'] = {'id': 'report-a', 'data': {'summary': '报告结论'}}
    expected_history = []
    for value in (first, second, page, report):
        assert run_chat(service, value).status == 'completed'
        assert captured[-1][2:-1] == expected_history
        expected_history.extend([
            {'role': 'user', 'content': value.input['text']},
            {'role': 'assistant', 'content': '建议补充 RAG 项目'},
        ])
    assert captured[1][2]['content'] == first.input['text']


def test_model_receives_complete_snapshot_without_remapping(model_stub):
    captured = []
    async def answer(messages):
        captured.append(messages)
        return {'msg_kind': 'reply', 'message': '根据当前资料回答'}
    model_stub(answer)
    value = command()
    value.snapshot['document'] = {'id': 'document-a', 'filename': '简历.pdf'}
    before = json.dumps(value.snapshot, ensure_ascii=False)
    asyncio.run(agent.handle(value, None, emit))
    assert json.loads(captured[0][1]['content']) == value.snapshot
    assert json.dumps(value.snapshot, ensure_ascii=False) == before
    assert [m['role'] for m in captured[0]] == ['system', 'user', 'user']


def test_sessions_and_missing_session_run_fallback_are_isolated(service, model_stub):
    captured = []
    async def answer(messages):
        captured.append(messages)
        return {'msg_kind': 'reply', 'message': '回答'}
    model_stub(answer)
    run_chat(service, command('浏览器A'))
    run_chat(service, command('浏览器B', session='session-b'))
    assert len(captured[-1]) == 3
    for session in (None, ''):
        first = command('没有会话标识1' + str(session), session=session)
        second = command('没有会话标识2' + str(session), session=session)
        if session is None:
            first.context.pop('session_id')
            second.context.pop('session_id')
        run_chat(service, first)
        run_chat(service, second)
        assert len(captured[-1]) == 3
        second.run_id = first.run_id
        run_chat(service, second)
        assert captured[-1][2]['content'] == first.input['text']


def test_failed_calls_do_not_append_history(service, model_stub):
    captured = []
    async def answer(messages):
        captured.append(messages)
        return {'msg_kind': 'reply', 'message': '有效回答'}
    model_stub(answer)
    run_chat(service, command('成功的问题'))

    async def fail(messages):
        raise RuntimeError('offline failure')
    model_stub(fail)
    assert run_chat(service, command('失败的问题')).status == 'failed'
    model_stub(answer)
    run_chat(service, command('重试下一条'))
    assert all(m['content'] != '失败的问题' for m in captured[-1])
    assert captured[-1][2]['content'] == '成功的问题'


def test_clarification_resumes_with_answer_not_original_text(model_stub):
    replies = iter([{'msg_kind': 'question', 'message': '你做过 RAG 吗？'}, {'msg_kind': 'reply', 'message': '建议先做小项目'}])
    captured = []
    async def answer(messages):
        captured.append(messages)
        return next(replies)
    model_stub(answer)
    pending = asyncio.run(agent.handle(command(), None, emit))
    result = asyncio.run(agent.handle(command(human={'decision': 'answer', 'text': '还没有'}, state=pending.state), None, emit))
    assert result.status == 'completed'
    assert captured[-1][-1]['content'] == '还没有'


def test_proposal_revision_is_forwarded_without_rewriting(model_stub):
    async def answer(messages):
        return {'msg_kind': 'proposal', 'message': '是否修改城市？', 'proposal': {
            'target': 'profile', 'revision': 999, 'changes': {'city': '上海'}}}
    model_stub(answer)
    result = asyncio.run(agent.handle(command(), None, emit))
    assert result.status == 'needs_input'
    assert result.pending_input['proposal']['revision'] == 999


@pytest.mark.parametrize('kind,status,pending_kind', [
    ('reply', 'completed', None),
    ('question', 'needs_input', 'question'),
    ('proposal', 'needs_input', 'proposal'),
])
def test_message_kind_controls_flow_and_reuses_one_message(service, model_stub, kind, status, pending_kind):
    message = '请查看这一条展示消息'
    async def answer(messages):
        value = {'msg_kind': kind, 'message': message, 'proposal': None}
        if kind == 'proposal':
            value['proposal'] = {'target': 'profile', 'revision': 2, 'changes': {'city': '上海'}}
        return value
    model_stub(answer)
    run = run_chat(service, command())
    assert run.status == status
    assert run.result['data']['message'] == message
    assert service.chat_sessions[('session-a', 'chat')].messages[-1] == {'role': 'assistant', 'content': message}
    if pending_kind:
        assert run.pending_input['kind'] == pending_kind
        assert run.pending_input['prompt'] == message
    else:
        assert run.pending_input is None


def test_old_sessions_are_not_evicted(service):
    first = service.chat_session(command(session='0'))
    first.append_turn('原话', '回答')
    for number in range(1, 121):
        service.chat_session(command(session=str(number))).append_turn('问题', '答案')
    assert service.chat_session(command(session='0')).messages == [
        {'role': 'user', 'content': '原话'}, {'role': 'assistant', 'content': '回答'},
    ]


@pytest.mark.parametrize('value', [
    {},
    {'msg_kind': 'proposal', 'message': '缺少具体修改数据'},
    {'msg_kind': 'reply', 'message': '普通回答夹带修改', 'proposal': {
        'target': 'profile', 'revision': 2, 'changes': {'city': '上海'}}},
    {'msg_kind': 'question', 'message': '追问夹带修改', 'proposal': {
        'target': 'profile', 'revision': 2, 'changes': {'city': '上海'}}},
    {'msg_kind': 'proposal', 'message': '缺岗位 ID', 'proposal': {
        'target': 'job', 'revision': 3, 'changes': {'city': '上海'}}},
    {'msg_kind': 'invalid', 'message': '未知消息类型'},
    {'msg_kind': 'proposal', 'message': '不能保存非 JSON 数值', 'proposal': {
        'target': 'profile', 'revision': 2, 'changes': {'evidence': [{'value': float('nan')}]}}},
])
def test_invalid_decision_does_not_pollute_existing_history(service, model_stub, value):
    session = service.chat_session(command())
    session.append_turn('之前的问题', '之前的回答')
    before = list(session.messages)
    async def answer(messages):
        return value
    model_stub(answer)
    assert run_chat(service, command('这次会失败')).status == 'failed'
    assert session.messages == before


def test_unimplemented_action_returns_valid_failure():
    value = command()
    value.action = 'evaluate'
    result = asyncio.run(agent.handle(value, None, emit))
    assert result.status == 'failed'
    validate_result(result.kind, result.data)


def test_model_call_yields_to_other_tasks(monkeypatch):
    progressed = []
    async def generate(messages):
        await asyncio.sleep(0.02)
        assert progressed == ['tick']
        return '{"msg_kind": "reply", "message": "回答"}'
    install_model(monkeypatch, generate)
    async def run():
        async def tick():
            await asyncio.sleep(0)
            progressed.append('tick')
        answer, _ = await asyncio.gather(agent.handle(command(), None, emit), tick())
        assert answer.data == {'message': '回答'}
    asyncio.run(run())


@pytest.mark.parametrize('content', ['', '   ', 'not json', '[]', '{}', '{"msg_kind": "reply", "message": ""}', None, []])
def test_invalid_model_output_fails_explicitly(service, monkeypatch, content):
    async def generate(messages):
        return content
    install_model(monkeypatch, generate)
    assert run_chat(service, command('非法输出的问题')).status == 'failed'
    assert service.chat_sessions[('session-a', 'chat')].messages == []


def test_complete_history_is_passed_to_model(service, model_stub):
    expected = []
    for number in range(20):
        user = str(number) + '问' * 5000
        assistant = str(number) + '答' * 5000
        service.chat_session(command()).append_turn(user, assistant)
        expected.extend([
            {'role': 'user', 'content': user}, {'role': 'assistant', 'content': assistant},
        ])
    captured = []
    async def answer(messages):
        captured.append(messages)
        return {'msg_kind': 'reply', 'message': '完整历史已收到'}
    model_stub(answer)
    assert run_chat(service, command('继续')).status == 'completed'
    assert captured[0][2:-1] == expected


def test_summary_writeback_preserves_original_transcript(service, model_stub):
    session = service.chat_session(command())
    session.append_turn('很早的问题', '很早的回答')
    session.append_turn('近期的问题', '近期的回答')
    original = [message.copy() for message in session.messages]

    async def update_summary(cmd, services, emit):
        # Agent 收到独立副本，修改副本也不能改写应用保存的原话。
        cmd.history[0]['content'] = '试改副本'
        return AgentResponse(kind='chat', data={'message': '本轮回复'},
                             summary=ChatSummary(text='较早一轮的要点', covered_messages=2))

    service.handler = update_summary
    run_chat(service, command('继续'))
    assert session.messages[:4] == original
    assert session.summary.covered_messages == 2
    assert service.chat_session(command(session='other')).summary is None

    captured = []
    async def answer(messages):
        captured.append(messages)
        return {'msg_kind': 'reply', 'message': '好的'}

    model_stub(answer)
    service.handler = agent.handle
    run = run_chat(service, command('下一轮'))
    assert run.status == 'completed'
    assert run.usage is None
    assert captured[0][2]['content'] == '较早对话摘要：较早一轮的要点'
    assert captured[0][3:-1] == session.messages[2:-2]
    assert session.messages[:4] == original
    assert session.summary.text == '较早一轮的要点'


def test_failed_response_cannot_replace_summary_or_original_messages(service):
    session = service.chat_session(command())
    session.append_turn('之前的问题', '之前的回答')
    session.summary = ChatSummary(text='原摘要', covered_messages=2)

    async def invalid(cmd, services, emit):
        return AgentResponse(kind='chat', data={'message': '本轮回复'},
                             summary=ChatSummary(text='越界摘要', covered_messages=20))

    service.handler = invalid
    assert run_chat(service, command()).status == 'failed'
    assert len(session.messages) == 2
    assert session.summary.text == '原摘要'


def test_invalid_model_json_reports_actual_usage(service, monkeypatch):
    async def invalid(messages):
        return 'not json'
    usage = {'input_tokens': 32, 'output_tokens': 6, 'total_tokens': 38}
    install_model(monkeypatch, invalid, usage)
    run = run_chat(service, command())
    assert run.status == 'failed'
    assert run.usage == usage
    assert service.chat_session(command()).messages == []


def test_previous_model_usage_follows_session_across_runs(service, model_stub):
    received = []
    async def handler(cmd, services, emit):
        received.append(cmd.previous_usage)
        return await agent.handle(cmd, services, emit)

    async def answer(messages):
        return {'msg_kind': 'reply', 'message': '回答'}

    service.handler = handler
    first = {'input_tokens': 12, 'output_tokens': 5, 'total_tokens': 17,
             'input_token_details': {'cache_read': 3}}
    second = {'input_tokens': 24, 'output_tokens': 7, 'total_tokens': 31}
    for index, (session_id, counts, previous) in enumerate([
        ('A', first, None), ('B', second, None), ('A', second, first),
        ('A', None, second), ('A', first, None),
    ]):
        model_stub(answer, counts)
        run = run_chat(service, command(str(index), session=session_id))
        assert run.status == 'completed'
        assert (received[-1], run.previous_usage, run.usage) == (previous, previous, counts)


def test_backend_confirms_once_and_rejects_without_saving(settings, model_stub):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from test_runs_api import wait_run

    calls = []
    async def answer(messages):
        calls.append(messages)
        return {'msg_kind': 'proposal', 'message': '期望城市改为上海？', 'proposal': {
            'target': 'profile', 'revision': json.loads(messages[1]['content'])['profile']['revision'],
            'changes': {'city': '上海'}}}
    usage = {'input_tokens': 80, 'output_tokens': 20, 'total_tokens': 100}
    model_stub(answer, usage)
    with TestClient(create_app(settings.model_copy(update={'agent_mode': 'real'}), handler=agent.handle)) as client:
        before = client.get('/api/profile').json()
        def begin():
            result = client.post('/api/runs', json={
                'action': 'chat', 'input': {'text': '期望城市改成上海'},
                'context': {'type': 'resume', 'session_id': 'integration'},
            })
            assert result.status_code == 202
            return wait_run(client, result.json()['id'])
        run = begin()
        assert run['status'] == 'needs_input'
        assert run['usage'] == usage
        body = {'input_id': run['pending_input']['id'], 'decision': 'confirm'}
        assert client.post('/api/runs/' + run['id'] + '/input', json=body).status_code == 202
        assert wait_run(client, run['id'])['status'] == 'completed'
        assert client.get('/api/runs/' + run['id']).json()['usage'] == usage
        after = client.get('/api/profile').json()
        assert after['data']['city'] == '上海'
        assert after['revision'] == before['revision'] + 1
        assert len(calls) == 1
        assert client.post('/api/runs/' + run['id'] + '/input', json=body).status_code == 409
        run = begin()
        assert calls[-1][-3] == {'role': 'user', 'content': '确认修改'}
        assert '已由应用确认并保存' in calls[-1][-2]['content']
        assert client.post('/api/runs/' + run['id'] + '/input', json={
            'input_id': run['pending_input']['id'], 'decision': 'reject',
        }).status_code == 202
        assert wait_run(client, run['id'])['status'] == 'completed'
        assert client.get('/api/profile').json() == after
        assert len(calls) == 2
        session = client.app.state.run_service.chat_sessions[('integration', 'chat')]
        assert session.messages[-2:] == [
            {'role': 'user', 'content': '拒绝修改'},
            {'role': 'assistant', 'content': '已拒绝这次修改，资料保持原样。'},
        ]


@pytest.mark.parametrize('changes,stale,expected_status', [
    ({'city': '上海'}, True, 409),
    ({'unknown_field': '不应保存'}, False, 422),
    ({'evidence': [{'text': '人工改写提取依据'}]}, False, 422),
    ({'salary_min': -1}, False, 422),
])
def test_backend_rejects_invalid_proposals_before_writing(settings, model_stub, changes, stale, expected_status):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from test_runs_api import wait_run

    calls = []
    async def answer(messages):
        calls.append(messages)
        revision = json.loads(messages[1]['content'])['profile']['revision']
        return {'msg_kind': 'proposal', 'message': '请核对修改', 'proposal': {
            'target': 'profile', 'revision': revision + 1 if stale else revision,
            'changes': changes,
        }}
    model_stub(answer)
    with TestClient(create_app(settings.model_copy(update={'agent_mode': 'real'}), handler=agent.handle)) as client:
        before = client.get('/api/profile').json()
        started = client.post('/api/runs', json={'action': 'chat', 'input': {'text': '提出修改'}})
        assert started.status_code == 202
        run = wait_run(client, started.json()['id'])
        assert run['status'] == 'needs_input'
        url = '/api/runs/' + run['id'] + '/input'
        body = {'input_id': run['pending_input']['id'], 'decision': 'confirm'}
        assert client.post(url, json=body).status_code == expected_status
        assert client.get('/api/profile').json() == before
        assert client.get('/api/runs/' + run['id']).json()['status'] == 'needs_input'
        assert len(calls) == 1
        body['decision'] = 'reject'
        assert client.post(url, json=body).status_code == 202
        assert wait_run(client, run['id'])['status'] == 'completed'
        assert client.get('/api/profile').json() == before


def test_backend_rejects_invalid_chat_input_before_agent(settings, model_stub):
    from fastapi.testclient import TestClient
    from backend.app import create_app

    calls = []
    async def answer(messages):
        calls.append(messages)
        return {'msg_kind': 'reply', 'message': '不应调用'}
    model_stub(answer)
    with TestClient(create_app(settings, handler=agent.handle)) as client:
        inputs = [{}, *({'text': value} for value in [None, 123, [], '', '   ', 'x' * 20001])]
        for value in inputs:
            response = client.post('/api/runs', json={'action': 'chat', 'input': value})
            assert response.status_code == 422
        assert calls == []
        assert client.app.state.run_service.runs == {}


def test_backend_checks_clarification_decisions_and_handles_finish(settings, model_stub):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from test_runs_api import start, wait_run

    calls = []
    async def answer(messages):
        calls.append(messages)
        return {'msg_kind': 'question', 'message': '你的工作城市是哪里？'}
    model_stub(answer)
    with TestClient(create_app(settings, handler=agent.handle)) as client:
        run = start(client, 'chat', text='帮我分析')
        url = '/api/runs/' + run['id'] + '/input'
        for fields in [
            {'decision': 'confirm'}, {'decision': 'reject'}, {'decision': 'invalid'},
            {'decision': 'answer'}, {'decision': 'answer', 'text': '   '},
        ]:
            result = client.post(url, json={'input_id': run['pending_input']['id'], **fields})
            assert result.status_code == 422
            assert client.get('/api/runs/' + run['id']).json()['status'] == 'needs_input'
        finished = client.post(url, json={'input_id': run['pending_input']['id'], 'decision': 'finish'})
        assert finished.status_code == 202
        assert wait_run(client, run['id'])['status'] == 'completed'
        assert len(calls) == 1


def test_backend_accepts_structured_answer_without_text(settings, model_stub):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from test_runs_api import start, wait_run

    calls = []
    async def answer(messages):
        calls.append(messages)
        if len(calls) == 1:
            return {'msg_kind': 'question', 'message': '你的工作城市是哪里？'}
        return {'msg_kind': 'reply', 'message': '已收到补充信息'}
    model_stub(answer, {'input_tokens': 30, 'output_tokens': 7, 'total_tokens': 37})
    with TestClient(create_app(settings, handler=agent.handle)) as client:
        run = start(client, 'chat', text='帮我分析')
        result = client.post('/api/runs/' + run['id'] + '/input', json={
            'input_id': run['pending_input']['id'], 'decision': 'answer', 'data': {'city': '杭州'},
        })
        assert result.status_code == 202
        final = wait_run(client, run['id'])
        assert final['status'] == 'completed'
        assert final['usage'] == {'input_tokens': 30, 'output_tokens': 7, 'total_tokens': 37}
        assert final['previous_usage'] == final['usage']
        assert len(calls) == 2
        assert json.loads(calls[-1][-1]['content'].split('补充信息：', 1)[1]) == {'city': '杭州'}
