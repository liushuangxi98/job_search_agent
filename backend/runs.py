import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, UTC
import json
import re
import uuid

from pydantic import ValidationError

from .contracts import AgentCommand, AgentResponse, ApplicationServices, PendingInput, validate_result
from .errors import AppError
from .schemas import JobData, Preferences, ProfileData


@dataclass
class Run:
    id: str
    action: str
    input: dict
    context: dict
    snapshot: dict
    is_demo: bool
    status: str = 'running'
    events: list = field(default_factory=list)
    result: dict | None = None
    pending_input: dict | None = None
    state: dict = field(default_factory=dict)
    report_id: str | None = None
    task: object = None


class RunManager:
    def __init__(self, settings, repository, files, handler):
        self.settings, self.repository, self.handler = settings, repository, handler
        self.services = ApplicationServices(repository, files)
        self.runs = {}
        self.lock = asyncio.Lock()
        self.log_dir = settings.data_dir / 'logs'
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def clean(self, value):
        if isinstance(value, dict):
            return {k: '[REDACTED]' if k.lower() in {'api_key', 'password', 'secret', 'authorization', 'access_token', 'mysql_password'}
                    else self.clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if isinstance(value, str):
            if self.settings.mysql_password:
                value = value.replace(self.settings.mysql_password, '[REDACTED]')
            value = re.sub(r'(?i)\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{8,}', '[REDACTED]', value)
            return value
        return value

    async def emit(self, run, kind, message, data=None):
        event = {'seq': len(run.events) + 1, 'kind': kind, 'message': self.clean(str(message)),
                 'data': self.clean(data or {}), 'at': datetime.now(UTC).isoformat()}
        # A broken adapter must not flood the UI with unbounded inline payloads.
        encoded = json.dumps(event, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 64 * 1024:
            event['data'] = {'note': '事件数据过大，界面省略；请发送摘要'}
            event['message'] = event['message'][:2000]
            encoded = json.dumps(event, ensure_ascii=False)
        run.events.append(event)
        with (self.log_dir / (run.id + '.jsonl')).open('a', encoding='utf-8') as log:
            log.write(encoded + '\n')

    def get(self, key, after=0):
        run = self.runs.get(key)
        if run is None:
            raise AppError(404, 'RUN_EXPIRED', '运行不存在或服务已重启，请重新发起')
        events = list(run.events)
        jobs = run.snapshot.get('jobs', [])
        # Only diagnostics are redacted. Never rewrite protocol IDs or business records.
        return deepcopy({'id': run.id, 'action': run.action, 'status': run.status, 'is_demo': run.is_demo,
                         'context': run.context, 'subject_job_id': jobs[0]['id'] if run.action == 'interview' and jobs else None,
                         'events': [e for e in events if e['seq'] > after], 'next_seq': events[-1]['seq'] if events else 0,
                         'result': run.result, 'pending_input': run.pending_input, 'report_id': run.report_id})

    def snapshot(self, body):
        if body.action == 'extract_jobs' and 'text' in body.input:
            value = body.input['text']
            if not isinstance(value, str) or not value.strip() or len(value) > 50000:
                raise AppError(422, 'JD_TEXT', '招聘原文须为不超过 50000 字的非空文本')
        snapshot = {'profile': self.repository.candidate('profile'), 'preferences': self.repository.candidate('preferences'), 'jobs': []}
        if body.action in {'evaluate', 'interview'}:
            if not snapshot['profile']['confirmed']:
                raise AppError(409, 'PROFILE_UNCONFIRMED', '请先确认简历画像')
            ids = body.input.get('job_ids', []) if body.action == 'evaluate' else [body.input.get('job_id')]
            if not isinstance(ids, list) or not 1 <= len(ids) <= 10 or any(not isinstance(x, str) for x in ids):
                raise AppError(422, 'JOB_SELECTION', '请选中 1—10 个岗位')
            snapshot['jobs'] = [self.repository.job(key) for key in dict.fromkeys(ids)]
        if body.action in {'parse_resume', 'extract_jobs', 'index_knowledge'}:
            key = body.input.get('document_id')
            if key:
                document = self.repository.document(key)
                expected = {'parse_resume': 'resume', 'extract_jobs': 'image', 'index_knowledge': 'knowledge'}[body.action]
                if document['kind'] != expected:
                    raise AppError(422, 'DOCUMENT_KIND', '资料类型与操作不符')
                snapshot['document'] = document
            elif self.settings.agent_mode != 'demo' and not (body.action == 'extract_jobs' and body.input.get('text')):
                raise AppError(422, 'DOCUMENT_REQUIRED', '请先上传资料')
        if body.action == 'chat':
            message = body.input.get('text')
            if not isinstance(message, str) or not message.strip() or len(message) > 20000:
                raise AppError(422, 'CHAT_TEXT', '请输入不超过 20000 字的消息')
        kind, key = body.context.get('type'), body.context.get('id')
        if kind == 'job' and key:
            snapshot['context_job'] = self.repository.job(key)
        if kind == 'report' and key:
            snapshot['context_report'] = self.repository.report(key)
        return snapshot

    async def start(self, body):
        async with self.lock:
            if self.handler is None:
                raise AppError(503, 'AGENT_NOT_CONNECTED', '真实 Agent 尚未接入，请实现 career_agent.agent.handle')
            if any(r.status in {'running', 'needs_input'} for r in self.runs.values()):
                raise AppError(409, 'RUN_BUSY', '已有活动正在进行，请先完成或结束当前活动')
            run = Run(id=uuid.uuid4().hex, action=body.action, input=deepcopy(body.input),
                      context=deepcopy(body.context), snapshot=self.snapshot(body), is_demo=self.settings.agent_mode == 'demo')
            self.runs[run.id] = run
            # Completed results are in MySQL; retain a small recent runtime window only.
            if len(self.runs) > 50:
                oldest = next(iter(self.runs))
                del self.runs[oldest]
            run.task = asyncio.create_task(self.execute(run))
            return self.get(run.id)

    def apply_proposal(self, proposal):
        target, changes = proposal.get('target'), proposal.get('changes')
        if not isinstance(changes, dict) or not changes:
            raise AppError(422, 'INVALID_PROPOSAL', '修改提案为空或格式错误')
        revision = proposal.get('revision')
        if target in {'profile', 'preferences'}:
            current = self.repository.candidate(target)
            schema = ProfileData if target == 'profile' else Preferences
            if set(changes) - set(schema.model_fields):
                raise AppError(422, 'INVALID_PROPOSAL', '提案包含不可修改字段')
            data = schema.model_validate({**current['data'], **changes}).model_dump()
            return self.repository.update_candidate(target, data, revision)
        if target == 'job':
            key = proposal.get('target_id')
            current = self.repository.job(key)
            if set(changes) - (set(JobData.model_fields) - {'source_ids'}):
                raise AppError(422, 'INVALID_PROPOSAL', '提案包含不可修改字段')
            return self.repository.save_job({**current['data'], **changes}, key, revision)
        raise AppError(422, 'INVALID_PROPOSAL', '不支持此修改目标')

    async def respond(self, key, body):
        async with self.lock:
            run = self.runs.get(key)
            if run is None:
                raise AppError(404, 'RUN_EXPIRED', '运行已丢失，请重新发起')
            if run.status != 'needs_input' or not run.pending_input or body.input_id != run.pending_input['id']:
                raise AppError(409, 'INPUT_ALREADY_USED', '该输入已处理或已失效，请刷新当前状态')
            pending = run.pending_input
            if pending['kind'] == 'proposal':
                if body.decision not in {'confirm', 'reject'}:
                    raise AppError(422, 'DECISION_REQUIRED', '请选择确认或拒绝')
                if body.decision == 'confirm':
                    self.apply_proposal(pending['proposal'])
            elif body.decision not in {'answer', 'finish'} or (body.decision == 'answer' and not (body.text or body.data)):
                raise AppError(422, 'ANSWER_REQUIRED', '请输入回答，或选择结束')
            run.pending_input = None
            if body.decision == 'finish' and run.action != 'interview':
                run.status = 'completed'
                run.result = {'kind': 'chat', 'data': {'message': '已结束本次活动，没有继续执行评估或资料处理。'}}
                await self.emit(run, 'status', '用户结束本次活动')
                return self.get(key)
            run.status = 'running'
            run.task = asyncio.create_task(self.execute(run, body.model_dump()))
            return self.get(key)

    async def execute(self, run, human=None):
        try:
            await self.emit(run, 'status', '继续执行' if human else '开始执行', {'mode': self.settings.agent_mode})
            command = AgentCommand(run.id, run.action, deepcopy(run.input), deepcopy(run.context),
                                   deepcopy(run.snapshot), deepcopy(run.state), human)

            async def emit(kind, message, data=None):
                await self.emit(run, kind, message, data)

            value = await self.handler(command, self.services, emit)
            response = value if isinstance(value, AgentResponse) else AgentResponse.model_validate(value)
            response.data = validate_result(response.kind, response.data)
            # Serialize once at the contract boundary before writing logs/JSON columns.
            json.dumps(response.model_dump(), ensure_ascii=False, allow_nan=False)
            run.result = {'kind': response.kind, 'data': deepcopy(response.data)}
            run.state = deepcopy(response.state)
            if response.status == 'needs_input':
                pending = PendingInput.model_validate(response.pending_input).model_dump(exclude_none=True)
                pending['id'] = uuid.uuid4().hex
                run.pending_input = pending
            elif response.kind in {'evaluation_report', 'interview_report'} and response.status in {'completed', 'partial'}:
                report = self.repository.save_report('evaluation' if response.kind == 'evaluation_report' else 'interview',
                                                     run.result['data'], deepcopy(run.snapshot), run.id, run.is_demo)
                run.report_id = report['id']
            run.status = response.status
            await self.emit(run, 'status', {'completed': '已完成', 'partial': '已有部分结果', 'needs_input': '等待你的补充', 'failed': '执行失败'}[run.status])
        except asyncio.CancelledError:
            run.status = 'failed'
            raise
        except Exception as exc:
            run.status, run.pending_input = 'failed', None
            code = exc.code if isinstance(exc, AppError) else 'INVALID_AGENT_OUTPUT' if isinstance(exc, ValidationError) else 'RUN_FAILED'
            message = exc.message if isinstance(exc, AppError) else f'执行失败（{type(exc).__name__}），请检查接入代码和脱敏事件。'
            run.result = {'kind': 'error', 'data': {'code': code, 'message': self.clean(message)}}
            await self.emit(run, 'error', message, {'code': code})

    async def close(self):
        tasks = [r.task for r in self.runs.values() if r.task and not r.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
