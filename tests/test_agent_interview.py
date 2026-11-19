"""通过实际运行入口验证题库选题、续答和报告，模型与知识库 HTTP 均使用替身。"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI
from openai import APIStatusError

from backend.config import Settings
from backend.exceptions import AppError
from backend.contracts import RunInput, RunStart
from backend.services.run_service import RunService
from career_agent import agent, interview
from career_agent.tools import knowledge
from workbench_fixtures import complete_job, complete_profile


# 缺少本机配置时应在发送 HTTP 前结束，避免进行无效检索。
def test_search_requires_local_key(monkeypatch):
    # 环境变量优先于本机配置文件，显式清空可稳定复现缺少密钥。
    monkeypatch.setenv('KNOWLEDGE_API_KEY', '')
    # 客户端替身用于确认一次请求都没有发出。
    client = Mock()
    monkeypatch.setattr(knowledge.httpx, 'AsyncClient', client)
    # 配置缺失必须作为明确错误返回，不能退化成没有候选题。
    with pytest.raises(AppError) as error:
        asyncio.run(knowledge.search_questions('RAG'))
    assert error.value.code == 'KNOWLEDGE_KEY_MISSING'
    client.assert_not_called()


# 计划只存考点和来源。后续动作由 DecisionNext 读取本场历史决定。
def topic(key, group='岗位与简历交集'):
    return dict(id=key, name=key, group=group)


def decision(action, message='', comment='结合回答的逐题点评', grade='了解'):
    return 'DecisionNext', dict(action=action, reason='依据本轮输入', result=dict(message=message,
                                hint='先从具体场景入手', comment=comment, grade=grade,
                                source='现场追问' if action == '继续追问' else ''))


def pick(question_id='q1', text='两个请求如何保证幂等？', number=7):
    return 'DecisionNext', dict(action='展示主问' if question_id is not None else '跳过考点',
                                reason='验证本岗位的测试能力',
                                result=dict(question_id=question_id or '', hint='先回想一个具体项目步骤。',
                                            message=text, reference='题库参考答案',
                                            source=f'题库原题 · 知识库第 {number} 题'))


def search(*queries):
    return 'DecisionNext', dict(action='需要工具', reason='查找该考点的原题',
                                result=dict(tool='search_questions', args={'queries': list(queries)}))


def main_question(app, text, number):
    # 固定每个考点的检索结果，再让模型选中这一题；题干和答案均来自本地题库替身。
    row = dict(app.bank.rows[0], question_id=f'q{number}', sort_order=number, question=text)
    app.bank.by_topic[text] = (200, [row])
    return [search(text), pick(f'q{number}', text, number)]


REPORT = 'Report', dict(reason='根据本场实际问答评价', summary='有实际设计经验', weaknesses=['并发'],
                        practice=['补充并发用例'], answers=[], topics=[])
SEARCH = search('登录测试')
FIRST_PLAN = 'TopicPlan', dict(ask_plan=[topic('幂等')], reason='岗位与支付项目交集')


@pytest.fixture
def session(tmp_path, monkeypatch):
    """走实际 LangChain 工具协议和 RunService，只替换模型生成、知识库传输和业务数据。"""
    # script 按顺序放模型接下来会给的回复；calls 记实际模型调用；searches 记实际检索请求。
    script, calls, searches = [], [], []
    monkeypatch.setenv('KNOWLEDGE_API_KEY', 'offline')
    bank = SimpleNamespace(status=200, by_topic={}, rows=[
        dict(question_id='q1', question='两个请求如何保证幂等？', answer='题库参考答案', sort_order=7),
    ])
    # 这里接收工具发出的请求，再给它一份测试题目，因此用例不会接触真实题库。
    def http(request):
        assert request.headers['X-API-Key'] == 'offline' and request.url.path == '/api/search'
        query = json.loads(request.content)
        searches.append(query)
        status, rows = bank.by_topic.get(query['topic'], (bank.status, bank.rows))
        return httpx.Response(status, json={'items': rows})
    client_type = httpx.AsyncClient
    monkeypatch.setattr(knowledge.httpx, 'AsyncClient',
                        lambda **kw: client_type(transport=httpx.MockTransport(http), **kw))

    class OfflineChat(ChatOpenAI):
        async def _agenerate(self, messages, **kwargs):
            step = script.pop(0)
            if isinstance(step, Exception):
                raise step
            # 一个元组表示单次调用；列表表示模型在同一条消息里发出多个调用。
            steps = step if isinstance(step, list) else [step]
            available = [tool['function']['name'] for tool in kwargs['tools']]
            for tool in kwargs['tools']:
                # 面试动作统一放在 action/result，规划和报告仍使用各自的结构。
                required = tool['function']['parameters']['required']
                assert set(required) == {'action', 'result', 'reason'} if tool['function']['name'] == 'DecisionNext' else 'reason' in required
            tools = [dict(name=name, args=args, id=f'call-{len(calls)}-{i}') for i, (name, args) in enumerate(steps)]
            # 模型偶尔返回未授权的工具；保留这种回复，让业务代码检查。
            # 每次模型回复只记一次。各个调用有独立 ID，用来核对工具结果。
            calls.append((messages, available, tools))
            message = AIMessage(content='', tool_calls=tools)
            return ChatResult(generations=[ChatGeneration(message=message)])
    model = OfflineChat(model='offline', api_key='test', max_retries=0)
    monkeypatch.setattr(interview, 'build_chat', lambda: model)

    repo = Mock()
    repo.profile.return_value = dict(revision=1, data=complete_profile())
    repo.job.return_value = dict(id='job', data=complete_job())
    repo.latest_evaluation.return_value = {'key_requirement': '并发控制待核实'}
    repo.save_report.return_value = {'id': 'report'}
    service = RunService(Settings(_env_file=None, agent_mode='real', data_dir=tmp_path), repo, None, agent.handle)
    async def start(**inputs):
        public = await service.start(RunStart(action='interview', input=dict(job_id='job', **inputs),
                                              context={'session_id': 'same-tab'}))
        run = service.runs[public['id']]
        await run.task
        return run
    # 用当前页面收到的输入 ID 提交，模拟用户回答或点击提前结束。
    async def reply(run, text=None):
        body = RunInput(input_id=run.pending_input['id'], decision='finish' if text is None else 'answer',
                        text=text or '')
        await service.respond(run.id, body)
        await run.task
    # session 提供题库和提交入口，每个用例可以独立模拟一场面试。
    return SimpleNamespace(start=start, reply=reply, script=script, calls=calls, searches=searches,
                           bank=bank, repo=repo, service=service)


@pytest.mark.parametrize('fixed', [True, False])
def test_plan_uses_feedback_once(session, fixed):
    # 首稿同时存在三种问题，第二次请求要把它们一次说清楚。
    topics = [topic('A'), topic('A'), topic('B'), topic('C')]
    topics[0]['name'] = topics[1]['name'] = '接口幂等'
    first = dict(ask_plan=topics, reason='优先考察项目')
    second = FIRST_PLAN if fixed else ('TopicPlan', dict(
        ask_plan=[topic('重试', '岗位扩展'), topic('限流', '岗位扩展')], reason='调整考点'))
    session.script.extend([('TopicPlan', first), second])
    if fixed:
        session.script.extend([SEARCH, pick()])

    run = asyncio.run(session.start())
    assert len(session.calls) == (4 if fixed else 2)
    original = json.loads(session.calls[0][0][-1].content)
    correction = json.loads(session.calls[1][0][-1].content)
    assert original['topic_limits'] == interview.TOPIC_LIMITS and 'info' not in correction
    assert correction['last_plan'] == first
    assert correction['problems'] == ['考点 ID「A」出现了 2 次，须唯一。',
                                      '考点名称「接口幂等」出现了 2 次，须唯一。',
                                      '岗位与简历交集有 4 项，最多允许 3 项。']
    if fixed:
        # 只有修正后通过校验的清单才成为正式计划。
        assert run.status == 'needs_input' and run.state['ask_plan'] == FIRST_PLAN[1]['ask_plan']
    else:
        assert run.status == 'failed' and run.result['data']['code'] == 'INTERVIEW_PLAN'
        assert '岗位扩展有 2 项，最多允许 1 项' in run.result['data']['message']
    assert not session.script


def test_search_batch_returns_each_result_then_one_question(session):
    topics = ['测试用例设计', '大模型接口高并发', '覆盖率']
    for i, topic_name in enumerate(topics):
        row = dict(session.bank.rows[0], question_id=f'q{i}', sort_order=10+i, question=topic_name + '原题')
        session.bank.by_topic[topic_name] = (200, [row])
    # 同一原题被两个关键词命中时，合并后的历史只保存一份。
    session.bank.by_topic[topics[-1]][1].append(session.bank.by_topic[topics[0]][1][0])
    # 一条模型回复查询三个关键词，从返回的候选中选第二道原题。
    selected = pick('q1', '大模型接口高并发原题', 11)
    session.script.extend([FIRST_PLAN, search(*topics), selected])
    run = asyncio.run(session.start())
    assert run.status == 'needs_input', run.result
    assert [q['topic'] for q in session.searches] == topics
    assert all(q['distance_limit'] == 0.5 for q in session.searches)
    assert run.result['data']['message'] == '大模型接口高并发原题'
    assert len(run.messages) == 1 and run.messages[0]['content'] == run.result['data']['message']
    assert 'turns' not in run.state
    assert run.result['data']['question_id'] == 'q1'
    # 候选题只存在历史中；状态经过 JSON 保存和恢复后仍能按当前考点找到原题。
    saved = interview.Plan.model_validate(json.loads(json.dumps(run.state)))
    searches = [item for item in saved.history if item.get('kind') == 'search']
    assert len(searches) == 1
    assert all(item['topic_id'] == saved.ask_plan[0].id for item in searches)
    assert interview.read_questions(saved)['q1']['question_id'] == 'q1'
    # 出题只存模型的完整返回，程序没有另造一条 assistant 题干消息。
    question_messages = [item for item in saved.history if item.get('kind') == 'decision'
                         and json.loads(item['content'])['action'] == '展示主问']
    assert len(question_messages) == 1
    parsed = interview.DecisionNext.model_validate(selected[1]).model_dump()
    assert json.loads(question_messages[0]['content']) == parsed
    assert not any(item.get('kind') in ('question', 'clarification') for item in saved.history)

    messages, available, _ = session.calls[-1]
    # 查库前后都返回同一模型类，多个查询词和对应结果完整进入下一次请求。
    assert available == session.calls[1][1] == ['DecisionNext']
    results = [json.loads(m.content)[interview.BANK_RESULTS] for m in messages
               if m.type == 'human' and interview.BANK_RESULTS in m.content]
    assert len(results) == 1 and list(results[0]) == ['q0', 'q1', 'q2']
    assert [row['question'] for row in results[0].values()] == [topic + '原题' for topic in topics]
    assert not any(m.type == 'tool' or getattr(m, 'tool_calls', []) for m in messages)


def test_model_call_does_not_change_history(session):
    history = [interview.message('user', '已有历史')]
    before = json.loads(json.dumps(history))
    session.script.append(FIRST_PLAN)
    reply, inputs = asyncio.run(interview.ask_model(
        interview.output_model(interview.TopicPlan), history, {}, interview.INTERVIEW_PLAN, {'任务': '创建计划'}))
    assert history == before
    assert [item['role'] for item in inputs] == ['system', 'user']


def test_use_tool_dispatches_registered_function(monkeypatch):
    # 新工具只要登记函数，分发入口就能按参数调用并返回一条消息。
    async def example_tool(text):
        return {'补充资料': text}
    monkeypatch.setitem(interview.TOOLS, 'example_tool', example_tool)
    item = asyncio.run(interview.use_tool('example_tool', {'text': '说明'}, 'Topic1'))
    assert isinstance(item, dict) and json.loads(item['content']) == {'补充资料': '说明'}
    assert item['topic_id'] == 'Topic1' and item['tool'] == 'example_tool'


@pytest.mark.parametrize('batch', [[SEARCH, ('Question', {'text':'提前出题'})], [pick('q1'), pick('q2')]])
def test_question_must_be_sent_alone(session, batch):
    # 先验证整条回复，再执行工具，避免混合回复中的第一项已经触发检索。
    session.script.extend([FIRST_PLAN, batch, batch])
    run = asyncio.run(session.start())
    assert run.status == 'failed' and run.result['data']['code'] == 'INTERVIEW_OUTPUT'
    assert len(session.calls) == 3 and '单独提交' in json.loads(session.calls[-1][0][-1].content)['修正要求']
    assert not run.pending_input and not session.searches


@pytest.mark.parametrize('source', ['现场生成', '题库改写'])
def test_main_cannot_supply_its_own_question(session, source):
    # 即使检索到了有效 ID，模型也只能选原题，不能借用该 ID 展示自己写的主问题。
    bad = 'Question', {'text':'模型编写的题干','source':source,'bank_id':'q1'}
    session.script.extend([FIRST_PLAN, SEARCH, bad, bad])
    run = asyncio.run(session.start())
    assert run.status == 'failed' and 'DecisionNext' in run.result['data']['message']
    assert run.pending_input is None


@pytest.mark.parametrize(('bad', 'field'), [
    (search(''), 'queries'),
    (pick(7), 'question_id'),
    (pick(text='模型改写的题目'), '原样填写'),
    (('DecisionNext', dict(action='随便聊', result={}, reason='讨论题目')), 'action'),
    (('DecisionNext', dict(action='需要工具', result={'tool': 'missing'}, reason='查找题目')), '未知工具'),
])
def test_worker_corrects_invalid_fields(session, bad, field):
    session.script.extend([FIRST_PLAN, SEARCH, bad, pick()])
    run = asyncio.run(session.start())
    assert run.status == 'needs_input' and run.result['data']['question_id'] == 'q1'
    assert field in json.loads(session.calls[-1][0][-1].content)['修正要求']
    assert len(session.searches) == 1 and not session.script
    assert sum(item.get('kind') == 'decision' and json.loads(item['content'])['action'] == '展示主问'
               for item in run.state['history']) == 1


@pytest.mark.parametrize(('empty', 'question_id'), [(False, 'q12'), (False, 'q1'), (True, 'q12')])
def test_skip_uses_only_next_topics_candidates(session, empty, question_id):
    first = 'TopicPlan', dict(ask_plan=[topic('幂等'), topic('重试')], reason='按顺序考察')
    query, _ = main_question(session, '重试', 12)
    if empty:
        session.bank.rows = []
    session.script.extend([first, SEARCH, pick(None), query, pick(question_id, '重试', 12)])
    if question_id == 'q1':
        session.script.append(pick('q12', '重试', 12))
    run = asyncio.run(session.start())
    # 空结果和不合适的候选都可以直接跳过；只有进入下一考点才重新查库。
    assert [q['topic'] for q in session.searches] == [SEARCH[1]['result']['args']['queries'][0], '重试']
    if question_id == 'q1':
        assert '题目 ID q1' in json.loads(session.calls[-1][0][-1].content)['修正要求']
    assert run.status == 'needs_input' and run.state['topic_index'] == 1
    assert len(run.messages) == 1 and run.result['data']['question_id'] == 'q12'


def test_search_batch_stops_at_failed_request(session):
    topics = ['第一项', '超限项', '剩余项']
    session.bank.by_topic['超限项'] = (413, [])
    session.script.extend([FIRST_PLAN, search(*topics)])
    run = asyncio.run(session.start())
    assert run.status == 'failed' and run.result['data']['code'] == 'KNOWLEDGE_REQUEST_TOO_LARGE'
    # 中途失败时停止本批，也不把半批结果交回模型继续出题。
    assert [q['topic'] for q in session.searches] == topics[:2] and len(session.calls) == 2


def test_plan_history_clarification_followup_and_report(session):
    app = session
    plan = 'TopicPlan', dict(ask_plan=[topic('测试设计'), topic('重试', '简历经历')], reason='测试项目')
    # 逐题点评和考点评价由报告模型提供，应用应保留这些结果。
    report = 'Report', dict(REPORT[1], answers=[
        dict(question='两个请求如何保证幂等？', answer='我先测试正确的密码。', feedback='报告模型对第一题的点评'),
        dict(question='你会检查哪个返回值？', answer='检查登录状态。', feedback='报告模型对追问的点评'),
        dict(question='重试前检查什么？', answer='先检查是否允许重试。', feedback='报告模型对第二考点的点评'),
    ], topics=[
        dict(name='测试设计', grade='合格', summary='能提出有效用例并检查状态', rounds=[1, 2]),
        dict(name='重试', grade='了解', summary='提到了重试条件', rounds=[3]),
    ])
    app.script.extend([
        plan, SEARCH, pick(),
        decision('沟通交流', '只考虑密码登录。', comment='', grade=None),
        decision('沟通交流', '只需说明一个检查点。', comment='', grade=None),
        SEARCH, decision('继续追问'), decision('继续追问', '你会检查哪个返回值？'),
        decision('新的题目'), *main_question(app, '重试前检查什么？', 8),
        decision('面试结束'), report,
    ])

    async def scenario():
        run = await app.start()
        assert run.status == 'needs_input', run.result
        first = run.result['data'].copy()
        # 模拟 HTTP 传输后的状态恢复，防止意外依赖模型对象或上一轮列表引用。
        # 保存首轮历史副本，后续每次恢复都应保留这个前缀。
        seen = json.loads(json.dumps(run.state['history']))
        assert run.state['topic_index'] == 0 and first['question_id'] == 'q1'
        assert list(json.loads(seen[0]['content'])) == ['job', 'resume']
        assert all('并发控制待核实' not in m['content'] for m in seen)
        assert 'reason' in json.loads(seen[3]['content'])
        assert any('"action": "需要工具"' in m['content'] for m in seen if m['role'] == 'assistant')
        for text, reply in [('请说明登录方式', '只考虑密码登录。'), ('需要列出全部检查点吗？', '只需说明一个检查点。')]:
            await app.reply(run, text)
            assert run.status == 'needs_input', run.result
            assert run.state['history'][:len(seen)] == seen
            seen = json.loads(json.dumps(run.state['history']))
            assert sum(m['role'] == 'user' and m['content'] == text for m in seen) == 1
            assert run.result['data']['message'] == reply and 'clarification' not in run.result['data']
            assert [m['content'] for m in run.messages[-2:]] == [text, reply]
            assert run.messages[0]['content'] == first['message'] and 'turns' not in run.state
            # 轮询和刷新读取同一份公开对话，不会重复追加回复或暴露内部参考答案。
            assert app.service.get(run.id)['messages'] == run.messages
            assert '题库参考答案' not in json.dumps(run.messages, ensure_ascii=False)
            # 每次请求只有本角色的系统提示在最前。岗位和简历资料在历史里只有一份。
            msgs = app.calls[-1][0]
            assert msgs[0].content.startswith(interview.INTERVIEW_WORKER)
            task = json.loads(msgs[-1].content)
            assert set(task) == {'当前考点', '最后一个考点'}
            assert any('"reference": "题库参考答案"' in m.content for m in msgs if m.type == 'ai')
            assert [m.type for m in msgs].count('system') == 1
            assert sum(m.type == 'human' and '"resume":' in m.content for m in msgs) == 1
            assert json.loads(msgs[1].content) == json.loads(seen[0]['content'])
            assert all('并发控制待核实' not in m.content for m in msgs)
        # 前两次请求澄清，直到这里用户才正式回答了第一题。
        await app.reply(run, '我先测试正确的密码。')
        # 缺少追问正文时只修正模型输出，不重做检索，也不重复登记用户输入。
        assert 'message' in json.loads(app.calls[-1][0][-1].content)['修正要求']
        assert len(app.searches) == 2
        assert run.result['data']['source'] == '现场追问'
        assert run.messages[-1]['content'] == '你会检查哪个返回值？'
        await app.reply(run, '检查登录状态。')
        assert run.result['data']['question_id'] == 'q8' and run.state['topic_index'] == 1
        await app.reply(run, '先检查是否允许重试。')
        assert run.status == 'completed', run.result
        data = run.result['data']
        assert data['messages'] == run.messages and len(run.messages) == 10
        assert [m['role'] for m in run.messages] == ['assistant', 'user'] * 5
        assert [a['answer'] for a in data['answers']] == ['我先测试正确的密码。','检查登录状态。','先检查是否允许重试。']
        report_messages = app.calls[-1][0]
        assert report_messages[0].type == 'system' and '澄清' in report_messages[0].content
        assert any(m.content == '请说明登录方式' for m in report_messages)
        assert [t['rounds'] for t in data['topics']] == [[1,2],[3]]
        assert [a['feedback'] for a in data['answers']] == [a['feedback'] for a in report[1]['answers']]
        assert data['topics'] == report[1]['topics']
        assert run.state['history'][:len(seen)] == seen
        # 最后一次报告调用也留在 state。页面只读业务结果，不返回内部历史或参考答案。
        assert json.loads(run.state['history'][-1]['content'])['summary'] == REPORT[1]['summary']
        assert 'history' not in app.service.get(run.id) and 'history' not in data
        assert not app.script
        # 逐次核对真正交给各个角色的消息顺序和公共资料。
        for messages, _, _ in app.calls:
            assert messages[0].type == 'system' and sum(m.type == 'system' for m in messages) == 1
            assert set(json.loads(messages[1].content)) == {'job', 'resume'}
            assert sum(m.type == 'human' and '"resume":' in m.content for m in messages) == 1
            assert not any('并发控制待核实' in m.content for m in messages)
        # 即使同一会话再开新面试，Plan.history 也从本场开场开始。
        app.script.extend([FIRST_PLAN, SEARCH, pick()])
        other = await app.start()
        assert other.status == 'needs_input' and len(other.messages) == 1
        assert not any(m['content'] == '请说明登录方式' for m in other.state['history'])
    asyncio.run(scenario())


@pytest.mark.parametrize('at_question', [False, True])
def test_model_413_stops_interview(session, at_question):
    # 规划和出题经过同一个模型请求入口，两处遇到超限都应立即结束这场面试。
    if at_question:
        session.script.append(FIRST_PLAN)
    # 这个响应在内存中构造，让测试经过 SDK 的异常类型和运行服务的错误处理。
    response = httpx.Response(413, request=httpx.Request('POST', 'https://offline.invalid'))
    session.script.append(APIStatusError('test', response=response, body=None))
    run = asyncio.run(session.start())
    assert run.status == 'failed' and run.result['data']['code'] == 'INTERVIEW_TOO_LARGE'
    assert not session.searches and not session.script


def test_unexpected_error_is_logged_without_plan_retry(session, capsys):
    session.script.extend([RuntimeError('offline-test'), FIRST_PLAN])
    run = asyncio.run(session.start())
    assert run.status == 'failed' and session.script == [FIRST_PLAN]
    event = session.service.report_events(run.id)[-1]
    assert event['data']['error_type'] == 'RuntimeError' and event['data']['stack']
    output = capsys.readouterr().err
    assert 'Traceback (most recent call last)' in output and 'RuntimeError: offline-test' in output


def test_search_error_prints_original_cause(session, monkeypatch, capsys):
    # 请求错误被转成 AppError 后，控制台仍能看到原始错误，页面保持业务提示。
    def fail(*args, **kwargs):
        raise httpx.ConnectError('offline connection refused')
    monkeypatch.setattr(knowledge.httpx, 'AsyncClient', fail)
    session.script.extend([FIRST_PLAN, SEARCH])
    run = asyncio.run(session.start())
    assert run.status == 'failed' and run.result['data']['code'] == 'KNOWLEDGE_UNAVAILABLE'
    output = capsys.readouterr().err
    assert 'ConnectError: offline connection refused' in output and 'AppError:' in output
    assert 'offline connection refused' not in str(run.result)


def test_latest_real_evaluation_belongs_to_selected_job():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.models import Report
    from backend.services.report_service import ReportService

    engine = create_engine('sqlite://')
    Report.__table__.create(engine)
    reports = ReportService(sessionmaker(engine))
    def save(job, text, demo=False):
        data = {'jobs': [{'job_id': job, 'key_requirement': text, 'commutes': {}}]}
        reports.save('evaluation', data, {}, text, demo)

    assert reports.latest_evaluation('job') is None
    save('job', '第一次评估')
    save('job', '目标岗位的真实评估')
    # 混入另一岗位的评估和演示数据，验证面试只使用当前岗位的最新真实评估。
    save('other', '无关岗位')
    save('job', '固定演示', demo=True)
    assert reports.latest_evaluation('job') == {'key_requirement': '目标岗位的真实评估'}
    engine.dispose()


@pytest.mark.parametrize('after_answer', [False, True])
def test_finish_while_waiting(session, after_answer):
    session.script.extend([FIRST_PLAN, SEARCH, pick()])
    if after_answer:
        session.script.append(decision('继续追问', '请说一个检查点。'))
    answers = [dict(question='两个请求如何保证幂等？', answer='我的回答', feedback='已记录本次回答')] if after_answer else []
    session.script.append(('Report', dict(REPORT[1], answers=answers,
        summary='有一条作答' if after_answer else '本场尚未作答')))

    async def scenario():
        run = await session.start()
        if after_answer:
            await session.reply(run, '我的回答')
        await session.reply(run)
        assert run.status == 'completed'
        assert [r['answer'] for r in run.result['data']['answers']] == (['我的回答'] if after_answer else [])
        assert not session.script
    asyncio.run(scenario())


def test_report_can_review_clarification_without_a_technical_answer(session):
    # 用户澄清后直接结束。即使没有技术作答，这段交流也要进入报告。
    session.script.extend([FIRST_PLAN, SEARCH, pick(), decision('沟通交流', '请只考虑网页端。'), REPORT])
    async def scenario():
        run = await session.start()
        await session.reply(run, '这里是网页端还是手机端？')
        await session.reply(run)
        assert run.status == 'completed'
        assert run.result['data']['answers'] == []
        assert [m['content'] for m in run.result['data']['messages'][-2:]] == [
            '这里是网页端还是手机端？', '请只考虑网页端。']
        assert any(m.content == '这里是网页端还是手机端？' for m in session.calls[-1][0])
        assert run.result['data']['summary'] == REPORT[1]['summary']
    asyncio.run(scenario())


@pytest.mark.parametrize('ask_more', [False, True])
def test_five_main_questions(session, ask_more):
    topics = [topic('测试设计'), topic('接口'), topic('召回'), topic('复盘', '简历经历'), topic('扩展', '岗位扩展')]
    # 模型把扩展排在最后时，实际出题仍先问扩展，交集组内的顺序保持一致。
    session.script.append(('TopicPlan', dict(ask_plan=topics.copy(), reason='按岗位选点')))
    topics = [topics[4], *topics[:4]]
    for i, item in enumerate(topics):
        session.script.extend(main_question(session, item['name']+'怎么做？', i+10))
        if i == 0 and ask_more:
            session.script.append(decision('继续追问','可以举一个例子吗？'))
        session.script.append(decision('新的题目'))
    rounds = [[1, 2], [3], [4], [5], [6]] if ask_more else [[1], [2], [3], [4], [5]]
    report = 'Report', dict(REPORT[1], topics=[
        dict(name=item['name'], grade='了解', summary='依据用户回答', rounds=numbers)
        for item, numbers in zip(topics, rounds)
    ])
    session.script.append(report)
    async def scenario():
        run = await session.start()
        for i in range(5 + int(ask_more)):
            assert run.status == 'needs_input' and len([m for m in run.messages if m['role'] == 'assistant']) == i+1
            assert run.state['ask_plan'] == topics
            await session.reply(run, '我的回答')
        assert run.status == 'completed' and not session.script
        assert run.result['data']['topics'] == report[1]['topics']
    asyncio.run(scenario())


def test_bad_plan_schema_is_returned_for_correction(session):
    # 真实 Pydantic 工具解析器拒绝整数理由；第二次输入包含第一份草稿及具体字段错误。
    session.script.extend([('TopicPlan', {'ask_plan':[], 'reason':0}), FIRST_PLAN, SEARCH, pick()])
    run = asyncio.run(session.start())
    assert run.status == 'needs_input' and run.state['topic_index'] == 0
    h=run.state['history']
    assert 'reason' in next(json.loads(m['content'])['problems'][0] for m in h if m['role'] == 'user' and '"problems"' in m['content'])
    assert any(m['role'] == 'assistant' and '"reason": 0' in m['content'] for m in h)
