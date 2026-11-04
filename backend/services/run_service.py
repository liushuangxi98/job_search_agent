"""内存运行服务：调用 Agent、等待人工输入、确认修改和保存完成报告。"""
import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, UTC
import json
import re
import uuid

from pydantic import ValidationError

from ..config import Settings
from ..contracts import (
    AgentCommand, AgentResponse, ApplicationServices, PendingInput,
    RunInput, RunStart, validate_result,
)
from ..exceptions import AppError
from ..schemas import JobData, Preferences, ProfileData
from .material_validation import candidate_missing, job_missing


@dataclass
class Run:
    """由 dataclass 生成初始化方法的内存状态，不是数据库表模型。
    snapshot 是初始资料，state 是 Agent 返回的续接状态。
    """
    id: str
    action: str
    input: dict
    context: dict
    snapshot: dict
    is_demo: bool
    status: str = 'running'
    # 每个运行使用独立的可变容器；dataclass 的工厂在创建实例时才生成列表或字典。
    events: list = field(default_factory=list)
    result: dict | None = None
    pending_input: dict | None = None
    state: dict = field(default_factory=dict)
    report_id: str | None = None
    task: object = None


class RunService:
    """协调应用与异步 Agent 的交接，不实现模型调用、工具循环或业务评分。"""
    def __init__(self, settings: Settings, repository, files, handler):
        """接收配置、资料/文件服务和异步入口，准备内存状态与本地日志目录。

        repository 使用 AgentDataService 的普通存取接口；handler 可为 None，启动运行时再拒绝。
        """
        self.settings = settings
        self.repository = repository
        self.handler = handler
        self.services = ApplicationServices(repository, files)
        self.runs = {}
        self.lock = asyncio.Lock()
        self.log_dir = settings.data_dir / 'logs'
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def clean(self, value):
        """按已知字段名、配置密码和常见令牌模式返回脱敏值，不原地修改输入。

        只用于诊断信息，不处理业务快照；这些规则不能识别所有可能的机密格式。
        """
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
        """为指定运行追加带序号的脱敏事件，并写入本地 JSONL，成功无返回值。

        kind 标识事件类别，data 为空时用空字典；过大事件缩减为摘要。
        JSON 序列化或日志写入失败会抛异常，不能把该方法视为不会失败的日志操作。
        """
        event = {'seq': len(run.events) + 1, 'kind': kind, 'message': self.clean(str(message)),
                 'data': self.clean(data or {}), 'at': datetime.now(UTC).isoformat()}
        # 事件只发送摘要，避免接入代码将大文件或长文本塞进轮询响应。
        encoded = json.dumps(event, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 64 * 1024:
            event['data'] = {'note': '事件数据过大，界面省略；请发送摘要'}
            event['message'] = event['message'][:2000]
            encoded = json.dumps(event, ensure_ascii=False)
        run.events.append(event)
        with (self.log_dir / (run.id + '.jsonl')).open('a', encoding='utf-8') as log:
            log.write(encoded + '\n')

    def get(self, key: str, after=0):
        """返回运行状态副本，仅附带序号大于 after 的事件。

        key 是运行 ID；无新增事件返回空列表，但仍返回当前结果和待处理输入。
        运行已重启丢失或移出内存保留窗口时抛 AppError，不从报告表恢复运行。
        """
        run = self.runs.get(key)
        if run is None:
            raise AppError(404, 'RUN_EXPIRED', '运行不存在或服务已重启，请重新发起')
        events = list(run.events)
        jobs = run.snapshot.get('jobs', [])
        # 仅诊断日志脱敏；业务字段和协议 ID 必须原样返回，不能误改用户资料。
        return deepcopy({
            'id': run.id, 'action': run.action, 'status': run.status, 'is_demo': run.is_demo,
            'context': run.context,
            'subject_job_id': jobs[0]['id'] if run.action == 'interview' and jobs else None,
            'events': [event for event in events if event['seq'] > after],
            'next_seq': events[-1]['seq'] if events else 0,
            'result': run.result, 'pending_input': run.pending_input, 'report_id': run.report_id,
        })

    def current(self):
        """返回 running 或 needs_input 状态的活动；没有活动时返回 {'active': None}。"""
        active = next((
            run for run in list(self.runs.values()) if run.status in {'running', 'needs_input'}
        ), None)
        return {'active': self.get(active.id) if active else None}

    def snapshot(self, body: RunStart):
        """按 RunStart 的动作和输入检查前提，返回运行使用的资料快照。

        包含画像、条件及动作需要的岗位或资料；前提不满足时抛 AppError。
        读取的是发起时资料，后续人工修改不会替换这份快照。
        """
        if body.action == 'extract_jobs' and 'text' in body.input:
            value = body.input['text']
            if not isinstance(value, str) or not value.strip() or len(value) > 50000:
                raise AppError(422, 'JD_TEXT', '招聘原文须为不超过 50000 字的非空文本')
        snapshot = {
            'profile': self.repository.candidate('profile'),
            'preferences': self.repository.candidate('preferences'),
            'jobs': [],
        }
        if body.action in {'evaluate', 'interview'}:
            if not snapshot['profile']['confirmed']:
                raise AppError(409, 'PROFILE_UNCONFIRMED', '请先确认简历画像')
            missing = candidate_missing(snapshot['profile']['data'], snapshot['preferences']['data'])
            if missing:
                raise AppError(409, 'CANDIDATE_INCOMPLETE', '请先完善个人资料：' + '、'.join(item['label'] for item in missing))
            ids = body.input.get('job_ids', []) if body.action == 'evaluate' else [body.input.get('job_id')]
            if not isinstance(ids, list) or not 1 <= len(ids) <= 10 or any(not isinstance(x, str) for x in ids):
                raise AppError(422, 'JOB_SELECTION', '请选中 1—10 个岗位')
            snapshot['jobs'] = [self.repository.job(key) for key in dict.fromkeys(ids)]
            if any(job_missing(job['data']) for job in snapshot['jobs']):
                raise AppError(409, 'JOB_INCOMPLETE', '请先完善岗位资料，或明确确认招聘信息盲区')
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

    async def start(self, body: RunStart):
        """为已校验的 RunStart 建立后台任务，返回可供轮询的运行字典。

        入口未接入、已有活动或输入前提不满足时抛 AppError，不创建新运行。
        运行仅在内存保留，接口不等待 Agent 完成。
        """
        # 这把锁只协调发起运行与人工输入，不锁住其他资料接口或数据库。
        async with self.lock:
            if self.handler is None:
                raise AppError(503, 'AGENT_NOT_CONNECTED', '真实 Agent 尚未接入，请实现 career_agent.agent.handle')
            if any(r.status in {'running', 'needs_input'} for r in self.runs.values()):
                raise AppError(409, 'RUN_BUSY', '已有活动正在进行，请先完成或结束当前活动')
            run = Run(
                id=uuid.uuid4().hex, action=body.action, input=deepcopy(body.input),
                context=deepcopy(body.context), snapshot=self.snapshot(body),
                is_demo=self.settings.agent_mode == 'demo',
            )
            self.runs[run.id] = run
            # 完成的报告已保存到 MySQL；这里只保留最近的少量运行状态。
            if len(self.runs) > 50:
                oldest = next(iter(self.runs))
                del self.runs[oldest]
            # 将执行安排到同一事件循环；create_task 不会为同步数据库操作另开线程。
            run.task = asyncio.create_task(self.execute(run))
            return self.get(run.id)

    def apply_proposal(self, proposal):
        """将提案合并到当前画像、条件或岗位，返回更新后的资料字典。

        调用方须先确认用户选择了 confirm；这里仍检查字段和读取时的修订号。
        提案非法、记录缺失、修订冲突或数据校验失败时抛出相应异常。
        """
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

    async def respond(self, key: str, body: RunInput):
        """消费当前待处理输入并返回运行状态；重复或过期 input_id 会抛 AppError。

        确认提案后先保存再续接 Agent，拒绝不保存；提案保存失败仍保留待处理输入。
        面试的 finish 交给 Agent 生成报告，其他活动的 finish 直接结束。
        """
        async with self.lock:
            run = self.runs.get(key)
            if run is None:
                raise AppError(404, 'RUN_EXPIRED', '运行已丢失，请重新发起')
            if run.status != 'needs_input' or not run.pending_input or body.input_id != run.pending_input['id']:
                raise AppError(409, 'INPUT_ALREADY_USED', '该输入已处理或已失效，请刷新当前状态')
            pending = run.pending_input
            if pending['kind'] == 'proposal':
                # 应用先保存已确认提案，之后只通知 Agent 继续，不能再次应用同一修改。
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
        """调用异步入口并更新 run，成功无返回值；human 是本轮人工输入，首次为 None。

        校验输出后更新内存状态，并保存 completed/partial 的评估或面试报告。
        执行异常标记为 failed 并尝试记录错误；取消请求标记失败后继续向外传播。
        """
        try:
            await self.emit(run, 'status', '继续执行' if human else '开始执行', {'mode': self.settings.agent_mode})
            command = AgentCommand(run.id, run.action, deepcopy(run.input), deepcopy(run.context),
                                   deepcopy(run.snapshot), deepcopy(run.state), human)

            async def emit(kind, message, data=None):
                """把 Agent 的事件绑定到本次运行，沿用外层的脱敏和日志写入。"""
                await self.emit(run, kind, message, data)

            # await 等待入口交回结果；当前方法中的同步 SQL 和文件操作仍占用事件循环。
            value = await self.handler(command, self.services, emit)
            response = value if isinstance(value, AgentResponse) else AgentResponse.model_validate(value)
            response.data = validate_result(response.kind, response.data)
            # 在契约边界校验可序列化性，错误结果不能进入报告表。
            json.dumps(response.model_dump(), ensure_ascii=False, allow_nan=False)
            run.result = {'kind': response.kind, 'data': deepcopy(response.data)}
            run.state = deepcopy(response.state)
            # 只有 needs_input 生成续接入口；partial 表示当前运行已结束但结果不完整。
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
            run.status = 'failed'
            run.pending_input = None
            code = exc.code if isinstance(exc, AppError) else 'INVALID_AGENT_OUTPUT' if isinstance(exc, ValidationError) else 'RUN_FAILED'
            message = exc.message if isinstance(exc, AppError) else f'执行失败（{type(exc).__name__}），请检查接入代码和脱敏事件。'
            run.result = {'kind': 'error', 'data': {'code': code, 'message': self.clean(message)}}
            await self.emit(run, 'error', message, {'code': code})

    async def close(self):
        """取消并等待尚未结束的后台任务，成功无返回值；不保存未完成运行的恢复检查点。"""
        # 先取消后台任务，等它们退出后应用才能关闭数据库连接池。
        tasks = [r.task for r in self.runs.values() if r.task and not r.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
