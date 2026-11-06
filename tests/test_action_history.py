"""用入口替身验证 action 历史隔离，覆盖上传、人工续接及结构化结果。"""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from backend.config import Settings
from backend.contracts import AgentResponse, ChatSummary, RunInput
from backend.services.run_service import Run, RunService
from workbench_fixtures import complete_profile


def run(key, action='parse_resume', session='tab-a'):
    inputs = {
        'chat': {'text': '继续讨论'}, 'parse_resume': {'document_id': 'resume-1'},
        'extract_jobs': {'document_id': 'image-1'}, 'index_knowledge': {'document_id': 'knowledge-1'},
        'evaluate': {'job_ids': ['job-1']}, 'interview': {'job_id': 'job-1'},
    }
    return Run(key, action, inputs[action], {'session_id': session}, {}, False)


@pytest.mark.parametrize('action', [
    'chat', 'parse_resume', 'extract_jobs', 'index_knowledge', 'evaluate', 'interview',
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
    assert captured[1].history == [
        {'role': 'user', 'content': first.input.get('text') or json.dumps(first.input, ensure_ascii=False)},
        {'role': 'assistant', 'content': '请继续'},
    ]
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
        if command.human_input is None:
            return AgentResponse(
                status='needs_input', kind='chat', data={'message': '请补充月薪范围'},
                pending_input={'kind': 'question', 'prompt': '请补充月薪范围'},
                state={'profile': draft},
            )
        assert command.state == {'profile': draft}
        assert command.chat_text == '两万到三万'
        return AgentResponse(kind='profile_draft', data={'profile': complete_profile()})

    service = RunService(Settings(_env_file=None, data_dir=tmp_path), None, None, handler)
    async def scenario():
        task = run('resume')
        service.runs[task.id] = task
        await service.execute(task)
        first_history = deepcopy(service.chat_session(task).messages)
        await service.respond(task.id, RunInput(
            input_id=task.pending_input['id'], decision='answer', text='两万到三万'))
        await task.task
        assert task.status == 'completed'
        assert captured[-1].history == first_history
        messages = service.chat_session(task).messages
        assert messages[-2] == {'role': 'user', 'content': '两万到三万'}
        assert json.loads(messages[-1]['content'])['profile']['salary_min'] == 20000
    asyncio.run(scenario())


def test_interview_finish_and_report_are_recorded_in_interview_history(tmp_path):
    captured = []
    async def handler(command, services, emit):
        captured.append(command)
        if command.human_input:
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
        assert captured[-1].chat_text == '结束本次'
        assert service.chat_session(task).messages[-2]['content'] == '结束本次'
        assert json.loads(service.chat_session(task).messages[-1]['content'])['title'] == '已结束'
    asyncio.run(scenario())
