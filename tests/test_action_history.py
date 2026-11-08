"""用入口替身验证 action 历史隔离，覆盖上传、人工续接及结构化结果。"""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from backend.config import Settings
from backend.schemas import ProfileData
from backend.contracts import AgentResponse, ChatSummary, RunInput
from backend.services.run_service import Run, RunService
from workbench_fixtures import complete_profile


def run(key, action='parse_resume', session='tab-a'):
    inputs = {
        'chat': {'text': '继续讨论'}, 'parse_resume': {'document_id': 'resume-1'},
        'extract_jobs': {'document_id': 'image-1'},
        'evaluate': {'job_ids': ['job-1']}, 'interview': {'job_id': 'job-1'},
    }
    return Run(key, action, inputs[action], {'session_id': session}, {}, False)


@pytest.mark.parametrize('action', [
    'chat', 'parse_resume', 'extract_jobs', 'evaluate', 'interview',
])
def test_each_action_gets_its_history_and_keeps_it_between_runs(tmp_path, action):
    captured = []
    async def handler(command, services, emit):
        captured.append(command)
        return AgentResponse(kind='chat', data={'message': '请继续'})

    service = RunService(Settings(_env_file=None, data_dir=tmp_path), None, None, handler)
    first = run('first', action)
    second = run('second', action)
    second.context.update(type='job', id='another-job')
    asyncio.run(service.execute(first))
    asyncio.run(service.execute(second))
    assert first.status == second.status == 'completed'
    assert captured[0].history == []
    assert captured[0].human_input == {
        'decision': 'start', 'text': first.input.get('text', ''), 'data': {}, 'input_id': None,
    }
    assert captured[0].input == {key: value for key, value in first.input.items() if key != 'text'}
    expected = [{'role': 'assistant', 'content': '请继续'}]
    if action == 'chat':
        expected.insert(0, {'role': 'user', 'content': '继续讨论'})
    assert captured[1].history == expected
    asyncio.run(service.execute(run('other-tab', action, 'tab-b')))
    assert captured[-1].history == []


def test_different_actions_keep_history_summary_and_usage_separate(tmp_path):
    captured = []
    async def handler(command, services, emit):
        captured.append(command)
        return AgentResponse(kind='chat', data={'message': '回答'})

    service = RunService(Settings(_env_file=None, data_dir=tmp_path), None, None, handler)
    resume = service.chat_session(run('resume'))
    resume.append_turn('之前的补充', '收到')
    resume.summary = ChatSummary(text='简历补充摘要', covered_messages=2)
    resume.last_usage = {'total_tokens': 12}
    asyncio.run(service.execute(run('chat', 'chat')))
    assert captured[-1].history == [] and captured[-1].summary is None
    assert captured[-1].previous_usage is None
    asyncio.run(service.execute(run('resume-again')))
    assert captured[-1].summary.text == '简历补充摘要'
    assert captured[-1].previous_usage == {'total_tokens': 12}
    assert captured[-1].history[0]['content'] == '之前的补充'


def test_resume_answer_gets_history_and_state_without_repeating_initial_input(tmp_path):
    captured = []
    draft = complete_profile(salary_min=None, salary_max=None)
    async def handler(command, services, emit):
        captured.append(command)
        if command.human_input['decision'] == 'start':
            return AgentResponse(
                status='needs_input', kind='chat', data={'message': '请补充月薪范围'},
                pending_input={'kind': 'question', 'prompt': '请补充月薪范围'},
                state={'profile': draft},
            )
        assert command.state == {'profile': draft}
        assert command.human_input['text'] == '两万到三万'
        return AgentResponse(kind='profile_draft', data=complete_profile())

    service = RunService(Settings(_env_file=None, data_dir=tmp_path), None, None, handler)
    async def scenario():
        task = run('resume')
        service.runs[task.id] = task
        await service.execute(task)
        first_history = deepcopy(service.chat_session(task).messages)
        assert first_history == [{'role': 'assistant', 'content': '请补充月薪范围'}]
        await service.respond(task.id, RunInput(
            input_id=task.pending_input['id'], decision='answer', text='两万到三万'))
        await task.task
        assert task.status == 'completed'
        assert captured[-1].history == first_history
        assert captured[-1].human_input['decision'] == 'answer'
        messages = service.chat_session(task).messages
        assert messages[-2] == {'role': 'user', 'content': '两万到三万'}
        assert json.loads(messages[-1]['content'])['salary_min'] == 20000
    asyncio.run(scenario())


def test_pasted_job_text_has_one_agent_input_location(tmp_path):
    captured = []
    async def handler(command, services, emit):
        captured.append(command)
        return AgentResponse(kind='chat', data={'message': '已收到'})
    service = RunService(Settings(_env_file=None, data_dir=tmp_path), None, None, handler)
    task = run('pasted', 'extract_jobs')
    task.input = {'text': '招聘 Python 工程师'}
    asyncio.run(service.execute(task))
    assert captured[0].input == {}
    assert captured[0].human_input['text'] == '招聘 Python 工程师'
    assert task.input == {'text': '招聘 Python 工程师'}


def test_resume_agent_reads_first_and_followup_human_input(tmp_path, monkeypatch):
    from career_agent.agent import handle
    from career_agent import parse_resume

    calls = []
    async def invoke(messages):
        calls.append(messages)
        if len(calls) == 1:
            assert all(message['content'] for message in messages)
            profile = complete_profile(salary_min=None, salary_max=None)
        else:
            assert messages[-1] == {'role': 'user', 'content': '两万到三万'}
            profile = complete_profile()
        return {
            'parsed': ProfileData.model_validate(profile),
            'raw': SimpleNamespace(usage_metadata=None),
        }
    model = SimpleNamespace(with_structured_output=lambda *args, **kwargs: SimpleNamespace(ainvoke=invoke))
    monkeypatch.setattr(parse_resume, 'build_chat', lambda: model)
    monkeypatch.setattr(parse_resume, 'extract_resume_text', lambda path: '虚构简历正文')
    path = SimpleNamespace(name='resume.docx', read_bytes=lambda: b'synthetic')
    files = SimpleNamespace(resolve=lambda key: path)
    service = RunService(Settings(_env_file=None, data_dir=tmp_path), None, files, handle)

    async def scenario():
        task = run('resume-agent')
        task.snapshot = {'profile': {'revision': 0, 'data': {}}}
        service.runs[task.id] = task
        await service.execute(task)
        assert task.status == 'needs_input'
        await service.respond(task.id, RunInput(
            input_id=task.pending_input['id'], decision='answer', text='两万到三万'))
        await task.task
        assert task.status == 'completed'
        assert len(calls) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize('start,output,status', [
    (True, {'city': '杭州'}, 'completed'),
    (False, {'salary_min': 40000}, 'failed'),
])
def test_resume_merges_saved_profile_and_validates_result(tmp_path, monkeypatch, start, output, status):
    from career_agent.agent import handle
    from career_agent import parse_resume

    async def invoke(messages):
        return {'parsed': ProfileData.model_validate(output), 'raw': SimpleNamespace(usage_metadata=None)}
    model = SimpleNamespace(with_structured_output=lambda *args, **kwargs: SimpleNamespace(ainvoke=invoke))
    monkeypatch.setattr(parse_resume, 'build_chat', lambda: model)
    monkeypatch.setattr(parse_resume, 'extract_resume_text', lambda path: '虚构简历正文')
    files = SimpleNamespace(resolve=lambda key: None)
    service = RunService(Settings(_env_file=None, data_dir=tmp_path), None, files, handle)
    task = run('merge-review')
    task.snapshot = {'profile': {'data': complete_profile()}}
    if not start:
        task.state = complete_profile()
    human = None if start else {'decision': 'answer', 'text': '下限四万', 'data': {}, 'input_id': 'test'}
    asyncio.run(service.execute(task, human))
    assert task.status == status
    if start:
        assert task.result['data']['experience'] == complete_profile()['experience']
    else:
        assert '字段约束冲突' in task.result['data']['message']


def test_interview_finish_and_report_are_recorded_in_interview_history(tmp_path):
    captured = []
    async def handler(command, services, emit):
        captured.append(command)
        if command.human_input['decision'] == 'finish':
            return AgentResponse(kind='interview_report', data={'title': '已结束', 'answers': []})
        return AgentResponse(
            status='needs_input', kind='interview_question',
            data={'round': 1, 'question': '如何检索？', 'question_id': 'q-1', 'source': '测试题库'},
            pending_input={'kind': 'question', 'prompt': '如何检索？'},
        )
    repository = SimpleNamespace(save_report=lambda *args: {'id': 'report-1'})
    service = RunService(Settings(_env_file=None, data_dir=tmp_path), repository, None, handler)
    async def scenario():
        task = run('interview', 'interview')
        task.input = {'job_id': 'job-1'}
        service.runs[task.id] = task
        await service.execute(task)
        await service.respond(task.id, RunInput(input_id=task.pending_input['id'], decision='finish'))
        await task.task
        assert task.status == 'completed'
        assert captured[-1].history[-1]['content'] == '如何检索？'
        assert captured[-1].human_input['text'] == '结束本次'
        assert service.chat_session(task).messages[-2]['content'] == '结束本次'
        assert json.loads(service.chat_session(task).messages[-1]['content'])['title'] == '已结束'
    asyncio.run(scenario())
