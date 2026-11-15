"""求职工作台 DOM 回归：随机端口、隔离数据库、固定 demo，无真实模型。"""
import asyncio
import base64
from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import socket
import threading
import time

from playwright.sync_api import sync_playwright, expect
import pytest
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn

from backend.app import create_app
from backend.contracts import AgentResponse
from backend.demo import JOBS, PROFILE, handle as demo_handle
from backend.repositories.candidate_repository import CandidateRepository
from backend.repositories.job_repository import JobRepository
from backend.schemas import ProfileData

pytestmark = pytest.mark.browser


def test_interview_chat_keeps_all_messages():
    """独立 DOM 检查使用真实渲染函数，无需数据库或模型，也不截图。"""
    root = Path(__file__).resolve().parents[1]
    app = (root / 'frontend/app.js').read_text(encoding='utf-8')
    api = (root / 'frontend/api.js').read_text(encoding='utf-8')
    code = next(line.replace('export ', '') for line in api.splitlines() if line.startswith('export const esc'))
    for name in ('button', 'idAttr', 'actionNames'):
        code += '\n' + next(line for line in app.splitlines() if line.startswith('const ' + name + ' ='))
    for name in ('interviewChatHTML', 'pendingHTML', 'runHTML', 'traceHTML', 'traceEntries'):
        start = app.index('function ' + name + '(')
        code += '\n' + app[start:app.index('\nfunction ', start)]

    messages = [
        {'role': 'assistant', 'content': '如何测试登录？', 'hint': '考虑输入范围', 'source': '题库原题'},
        {'role': 'user', 'content': '包括手机端吗？'},
        {'role': 'assistant', 'content': '只考虑网页端。'},
        {'role': 'user', 'content': '我先测试空密码。'},
        {'role': 'assistant', 'content': '空密码时应显示什么？'},
    ]
    run = dict(id='run', action='interview', status='needs_input', messages=messages, events=[],
               result={'data': {'message': messages[-1]['content']}},
               pending_input={'id': 'input', 'kind': 'question', 'prompt': messages[-1]['content']})
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        try:
            page = browser.new_page()
            page.add_script_tag(content=code)
            render = '(run) => { document.body.innerHTML = runHTML(run); }'
            page.evaluate(render, run)
            expect(page.locator('.interview-chat .bubble > .prose')).to_have_text([m['content'] for m in messages])
            assert not page.locator('.interview-chat details').evaluate('(node) => node.open')
            expect(page.get_by_role('textbox', name='面试消息')).to_be_visible()
            expect(page.get_by_role('button', name='发送', exact=True)).to_be_visible()
            assert '面试官补充说明' not in page.locator('body').inner_text()
            # 重画相同的运行快照不会增加消息，等待模型时也保留已经发送的内容。
            page.evaluate(render, run)
            expect(page.locator('.interview-chat .bubble')).to_have_count(5)
            run.update(status='running', pending_input=None)
            run['messages'].append({'role': 'user', 'content': '<img src=x>输入错误提示'})
            page.evaluate(render, run)
            expect(page.locator('.interview-chat .bubble')).to_have_count(6)
            expect(page.locator('.interview-chat img')).to_have_count(0)
            expect(page.locator('[data-run-answer]')).to_have_count(0)
            run.update(status='failed', result={'data': {'message': '请求失败'}})
            page.evaluate(render, run)
            expect(page.locator('.interview-chat .bubble')).to_have_count(6)
        finally:
            browser.close()


@dataclass
class Gate:
    enabled: bool = False
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)


@pytest.fixture
def browser_env(settings):
    gate, calls, contexts, requests = Gate(), [], [], []
    second_image = threading.Event()
    image_responses = {}

    async def handler(command, services, emit):
        calls.append((command.action, dict(command.input)))
        contexts.append(dict(command.context))
        if command.action in {'evaluate', 'interview'}:
            await emit('status', f'{command.action} {command.run_id}')
        images = [x for x in calls if x[0] == 'extract_jobs' and x[1].get('document_id')]
        if len(images) > 1:
            second_image.set()
        if command.action == 'extract_jobs' and command.input.get('document_id') and len(images) in image_responses:
            return image_responses[len(images)]
        if command.action == 'extract_jobs' and command.human_input['text'] == 'missing-company':
            job = {**deepcopy(JOBS[0]), 'company': '', 'title': '需要补齐的岗位'}
            return AgentResponse(kind='job_drafts', data=job)
        return await demo_handle(command, services, emit)

    app = create_app(settings, handler=handler)
    repo = app.state.agent_data_service
    candidate = app.state.candidate_service
    candidate.confirm(deepcopy(PROFILE), 0)

    class SaveGate(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            requests.append((request.method, request.url.path))
            response = await call_next(request)
            if gate.enabled and request.method == 'POST' and request.url.path == '/api/jobs':
                gate.enabled = False
                gate.started.set()
                await asyncio.to_thread(gate.release.wait, 8)
            return response

    app.add_middleware(SaveGate)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.02)
    assert server.started
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.set_default_timeout(5000)
            page.on('pageerror', lambda error: errors.append(str(error)))
            yield {'page': page, 'url': f'http://127.0.0.1:{port}', 'repo': repo,
                   'candidate': candidate, 'app': app, 'gate': gate, 'calls': calls,
                   'contexts': contexts, 'requests': requests,
                   'second_image': second_image, 'image_responses': image_responses, 'errors': errors}
            browser.close()
    finally:
        gate.release.set()
        server.should_exit = True
        thread.join(8)
        listener.close()
    assert not errors, errors


def visit(env, page='jobs'):
    env['page'].goto(env['url'] + ('/#/' + page if page else '/'))
    expect(env['page'].locator('#connection')).to_contain_text('MySQL 已连接')
    expect(env['page'].locator('#view .loading')).to_have_count(0)


def seed_jobs(env):
    return tuple(env['repo'].save_job({**deepcopy(JOBS[index]), 'company': name + '公司',
                                      'title': name + '岗位'})
                 for index, name in enumerate(['A', 'B']))


def row(page, item):
    return page.locator(f'[data-job-row="{item["id"]}"]')


def open_job_details(job_row):
    details = job_row.locator('[data-job-details]')
    if not details.evaluate('node => node.open'):
        details.locator(':scope > summary').click()
    return details


def seed_legacy_job(env, **fields):
    with env['app'].state.database.session_factory.begin() as session:
        job_id = JobRepository(session).add({**deepcopy(JOBS[0]), **fields}).id
    return env['repo'].job(job_id)


def assert_required_job_fields(form):
    fields = form.locator('[name]').evaluate_all('(nodes) => Object.fromEntries(nodes.map(n => [n.name, n.required]))')
    assert fields == dict.fromkeys(('company', 'title', 'city', 'address', 'salary_min', 'salary_max',
                                    'experience_years', 'education', 'job_description', 'job_requirement'), True)


def open_chat(page):
    page.locator('.chat-fab').click()
    expect(page.locator('#chat-drawer')).to_be_visible()


def chat(page, text):
    previous = page.locator('#chat-messages .bubble:not(.user)').count()
    page.locator('#chat-text').fill(text)
    page.locator('#chat-form button').click()
    expect(page.locator('#chat-messages .bubble:not(.user)')).to_have_count(previous + 1 if page.locator('#chat-messages .bubble.user').count() > 1 else 1, timeout=8000)
    expect(page.locator('#chat-form button')).to_be_enabled(timeout=8000)


def paste_jobs(page, text='普通招聘文本'):
    page.get_by_role('button', name='粘贴招聘原文', exact=True).click()
    page.locator('#paste-form textarea').fill(text)
    page.get_by_role('button', name='提取岗位', exact=True).click()
    expect(page.locator('#editor')).to_contain_text('确认待导入岗位')


def image_files(tmp_path):
    data = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1xQAAAAASUVORK5CYII=')
    paths = [tmp_path / 'a.png', tmp_path / 'b.png']
    for path in paths:
        path.write_bytes(data)
    return [str(path) for path in paths]


def import_images(page, files):
    with page.expect_file_chooser() as chooser:
        page.get_by_role('button', name='导入截图', exact=True).click()
    chooser.value.set_files(files)


@pytest.mark.parametrize('has_jobs', [False, True], ids=['empty', 'with-jobs'])
def test_default_home_has_only_resume_and_jobs(browser_env, has_jobs):
    env, page = browser_env, browser_env['page']
    if has_jobs:
        seed_jobs(env)
    visit(env, '')
    expect(page.locator('#page-title')).to_have_text('岗位池')
    expect(page.locator('.toolbar .actions button')).to_have_text(['导入截图', '粘贴招聘原文'])
    expect(page.locator('[data-action=new-job], [data-action=sample-jobs]')).to_have_count(0)
    expect(page.locator('[data-job-row]')).to_have_count(2 if has_jobs else 0)
    if not has_jobs:
        expect(page.locator('.job-list')).to_contain_text('还没有导入岗位')
        expect(page.locator('.job-list')).to_contain_text('通过上方的“导入截图”或“粘贴招聘原文”开始。')
    assert page.locator('nav a').evaluate_all('(nodes) => nodes.map(n => n.dataset.page)') == ['resume', 'jobs']
    expect(page.locator('#evaluate-selection')).to_have_count(0)
    assert '总分' not in page.locator('#view').inner_text()


def test_chat_preserves_unsaved_profile_form_and_session(browser_env):
    env, page = browser_env, browser_env['page']
    a, _ = seed_jobs(env)
    visit(env, 'resume')
    page.locator('#profile-form [name=projects]').fill('还没有保存的项目经历')
    open_chat(page)
    chat(page, '解释这个页面')
    expect(page.locator('#chat-messages')).to_contain_text('固定示例回复')
    page.locator('[data-action=close-chat]').click()
    page.locator('nav [data-page=jobs]').click()
    row(page, a).get_by_role('button', name='A岗位', exact=True).click()
    open_chat(page)
    chat(page, '继续聊这个岗位')
    expect(page.locator('#chat-messages')).to_contain_text('解释这个页面')
    page.locator('[data-action=close-chat]').click()
    page.locator('nav [data-page=resume]').click()
    expect(page.locator('#profile-form [name=projects]')).to_have_value('还没有保存的项目经历')
    page.once('dialog', lambda dialog: dialog.accept())
    page.reload()
    expect(page.locator('#profile-form [name=projects]')).to_have_value(PROFILE['projects'])
    open_chat(page)
    expect(page.locator('#chat-messages .bubble.user')).to_have_count(0)
    chat(page, '刷新后继续')
    sessions = [context['session_id'] for context in env['contexts']]
    assert sessions[0] == sessions[1]
    assert sessions[2] != sessions[0]
    expect(page.locator('#chat-messages .bubble.user')).to_have_count(1)
    assert page.evaluate('({local: localStorage.length, session: sessionStorage.length})') == {'local': 0, 'session': 0}


def test_profile_fields_survive_confirmation_refresh_and_draft_adoption(browser_env, monkeypatch):
    env, page = browser_env, browser_env['page']
    original = env['candidate'].get_all()
    draft_evidence = [{'text': '参与自动化项目', 'document_id': 'resume_dom', 'locator': '第 2 页',
                       'metadata': {'pages': [2, 3]}}]
    monkeypatch.setitem(PROFILE, 'evidence', draft_evidence)

    visit(env, 'resume')
    form = page.locator('#profile-form')
    controls = form.locator('input, select, textarea, [contenteditable=true]')
    names = controls.evaluate_all('(nodes) => nodes.map(n => n.name)')
    assert len(names) == 14
    assert set(names) == ProfileData.model_fields.keys() - {'evidence'}
    assert {'headline', 'commute_limit_minutes'}.isdisjoint(names)
    expect(form).to_contain_text('暂无提取依据')
    major = form.get_by_label('大学专业')
    summary = form.get_by_label('个人简介')
    mode = form.get_by_label('出行偏好')
    expect(major).to_have_attribute('required', '')
    expect(summary).to_have_attribute('required', '')
    expect(mode).to_have_attribute('required', '')
    major.fill('软件工程')
    summary.fill('负责测试平台研发')
    mode.select_option(label='驾车')
    save = form.get_by_role('button', name='保存并确认画像与条件')
    save.click()
    expect(page.locator('#toast')).to_contain_text('一起确认保存')
    page.reload()
    expect(major).to_have_value('软件工程')
    expect(summary).to_have_value('负责测试平台研发')
    expect(mode).to_have_value('驾车')

    page.get_by_role('button', name='查看示例画像').click()
    preview = page.locator('#editor')
    for label, value in [('大学专业', PROFILE['major']), ('个人简介', PROFILE['summary']),
                         ('出行偏好', '公共交通')]:
        expect(preview.locator('dt', has_text=label).locator('+ dd')).to_have_text(value)
    details = preview.locator('details').filter(has=page.locator('summary', has_text='提取依据')).first
    expect(details).not_to_have_attribute('open', '')
    details.locator(':scope > summary').click()
    expect(details).to_contain_text('参与自动化项目 resume_dom · 第 2 页')
    preview.get_by_role('button', name='填入画像表单').click()
    expect(major).to_have_value(PROFILE['major'])
    expect(summary).to_have_value(PROFILE['summary'])
    expect(mode).to_have_value('公共交通')
    expect(form).to_contain_text('参与自动化项目 resume_dom · 第 2 页')
    assert env['candidate'].get_all()['revision'] == original['revision'] + 1
    summary.fill('核对后补充的简介')
    with page.expect_request(lambda req: req.method == 'PUT' and req.url.endswith('/api/candidate')) as saved:
        save.click()
    expected = {**PROFILE, 'summary': '核对后补充的简介'}
    assert saved.value.post_data_json['profile'] == expected
    expect(page.locator('#toast')).to_contain_text('一起确认保存')
    page.reload()
    expect(summary).to_have_value(expected['summary'])
    expect(form).to_contain_text('参与自动化项目 resume_dom · 第 2 页')
    page.get_by_text('原始提取依据', exact=True).click()
    assert json.loads(page.locator('details[open] pre').inner_text()) == draft_evidence
    assert env['candidate'].get_all()['profile'] == expected


def test_candidate_zero_years_is_saved_atomically(browser_env):
    env, page = browser_env, browser_env['page']
    visit(env, 'resume')
    before = env['candidate'].get_all()['revision']
    page.locator('[name=experience_years]').fill('0')
    page.locator('[name=city]').fill('上海')
    page.get_by_role('button', name='保存并确认画像与条件').click()
    expect(page.locator('#toast')).to_contain_text('一起确认保存')
    saved = env['candidate'].get_all()
    assert saved['revision'] == before + 1
    assert saved['profile']['experience_years'] == 0
    assert saved['profile']['city'] == '上海'
    assert [x for x in env['requests'] if x[0] == 'PUT'] == [('PUT', '/api/candidate')]


@pytest.mark.parametrize('field_name', ['name', 'education', 'major', 'summary', 'commute_mode', 'experience_years', 'skills', 'experience',
                                        'projects', 'direction', 'city', 'commute_origin', 'salary_min', 'salary_max'])
def test_candidate_required_fields_block_before_request(browser_env, field_name):
    env, page = browser_env, browser_env['page']
    visit(env, 'resume')
    control = page.locator(f'#profile-form [name={field_name}]')
    if field_name == 'commute_mode':
        control.select_option('')
    else:
        control.fill('')
    page.get_by_role('button', name='保存并确认画像与条件').click()
    expect(page.locator('#candidate-errors')).to_be_visible()
    assert not any(method == 'PUT' for method, _ in env['requests'])


@pytest.mark.parametrize('field_name, missing_value, label', [
    ('summary', '', '个人简介'), ('commute_mode', None, '出行偏好'),
])
def test_missing_profile_field_blocks_jobs_until_confirmed(browser_env, field_name, missing_value, label):
    env, page = browser_env, browser_env['page']
    a, _ = seed_jobs(env)
    # 模拟旧库中仍标记为已确认、但按当前必填规则存在缺项的画像。
    with env['candidate'].session_factory.begin() as session:
        repository = CandidateRepository(session)
        candidate = repository.get()
        assert candidate.confirmed
        repository.update(candidate, {**candidate.profile, field_name: missing_value})
    current = env['candidate'].get_all()
    assert not current['confirmed']
    assert current['missing'] == [{'field': f'profile.{field_name}', 'label': label}]

    visit(env)
    for action in ('评估', '模拟面试'):
        button = row(page, a).get_by_role('button', name=action, exact=True)
        expect(button).to_have_attribute('data-blocked', 'true')
        expect(button).to_be_disabled()
    page.get_by_role('link', name='前往简历画像 →').click()
    control = page.locator(f'#profile-form [name={field_name}]')
    expect(control).to_have_value('')
    page.get_by_role('button', name='保存并确认画像与条件').click()
    expect(page.locator('#candidate-errors')).to_contain_text(label)
    assert not any(method == 'PUT' for method, _ in env['requests'])

    if field_name == 'commute_mode':
        control.select_option('公共交通')
    else:
        control.fill(PROFILE[field_name])
    page.get_by_role('button', name='保存并确认画像与条件').click()
    expect(page.locator('#toast')).to_contain_text('一起确认保存')
    assert env['candidate'].get_all()['confirmed']
    page.locator('nav [data-page=jobs]').click()
    for action in ('评估', '模拟面试'):
        button = row(page, a).get_by_role('button', name=action, exact=True)
        expect(button).to_have_attribute('data-blocked', 'false')
        expect(button).to_be_enabled()
    assert not env['calls']


def test_commute_selects_both_places_before_query(browser_env):
    env, page = browser_env, browser_env['page']
    answers = []

    async def handle(command, services, emit):
        if command.human_input['decision'] == 'answer':
            answers.append(command.human_input['data'])
            return await demo_handle(command, services, emit)
        prompt = '请选择通勤的出发点和目的地。'
        return AgentResponse(
            status='needs_input', kind='chat', pending_input={'kind': 'question', 'prompt': prompt},
            data={'message': prompt, 'place_choices': {
                'start': ['杭州东门'], 'end': ['杭州西门', '杭州南门'],
            }},
        )

    env['app'].state.run_service.handler = handle
    job, _ = seed_jobs(env)
    visit(env)
    job_row = row(page, job)
    job_row.get_by_role('button', name='评估', exact=True).click()
    form = job_row.locator('[data-run-answer]')
    start = form.locator('select[name=start]')
    end = form.locator('select[name=end]')
    expect(start).to_be_visible()
    expect(end).to_be_visible()
    expect(start).to_contain_text('杭州东门')
    expect(end).to_contain_text('杭州南门')
    expect(form.locator('option[value=new], input[type=text], input:not([type]), textarea')).to_have_count(0)
    expect(start).to_have_value('')
    expect(end).to_have_value('')
    submit = form.locator('button:not([type=button])')
    submit.click()
    assert answers == []
    start.select_option('1')
    end.select_option('2')
    submit.click()
    expect(job_row.locator('.report')).to_be_visible()
    assert answers == [{'start': '1', 'end': '2'}]
    assert sum(method == 'POST' and path.endswith('/input') for method, path in env['requests']) == 1


def test_old_run_view_does_not_replace_active_run(browser_env):
    env, page = browser_env, browser_env['page']
    a, _ = seed_jobs(env)
    visit(env)
    row(page, a).get_by_role('button', name='评估', exact=True).click()
    evaluation = row(page, a).locator('[data-job-action=evaluate]')
    expect(evaluation.locator('.report')).to_be_visible()
    earlier = env['repo'].reports()['items'][0]
    old_url = page.url
    page.locator('#scenario').select_option('needs_input')
    row(page, a).get_by_role('button', name='评估', exact=True).click()
    expect(row(page, a).locator('.pending')).to_contain_text('演示地址歧义')
    page.goto(old_url)
    expect(row(page, a).locator('.pending')).to_contain_text('演示地址歧义')
    expect(page.locator('#chat-drawer')).not_to_be_visible()
    row(page, a).locator('[data-run-answer] textarea').fill('杭州软件园')
    row(page, a).get_by_role('button', name='提交并继续').click()
    expect(evaluation.locator('select option')).to_have_count(2)
    assert env['repo'].reports()['total'] == 2
    latest = next(report for report in env['repo'].reports()['items'] if report['id'] != earlier['id'])
    evaluation.locator('select').select_option(earlier['id'])
    evaluation.locator('.report .trace > summary').click()
    expect(evaluation.locator('.trace')).to_contain_text('evaluate ' + earlier['run_id'])
    expect(evaluation.locator('.trace')).not_to_contain_text(latest['run_id'])


def test_cancel_import_stops_unsent_jobs(browser_env):
    env, page = browser_env, browser_env['page']
    visit(env)
    paste_jobs(page)
    env['gate'].enabled = True
    page.get_by_role('button', name='确认入库', exact=True).click()
    assert env['gate'].started.wait(3)
    page.get_by_role('button', name='取消', exact=True).click()
    env['gate'].release.set()
    expect(page.locator('#toast')).to_contain_text('已停止后续导入')
    assert env['repo'].jobs()['total'] == 1


def test_incomplete_job_draft_can_be_corrected_before_saving(browser_env):
    env, page = browser_env, browser_env['page']
    visit(env)
    paste_jobs(page, 'missing-company')
    page.locator('#editor input[name=company]').fill('补齐后的公司')
    page.get_by_role('button', name='确认入库', exact=True).click()
    expect(page.locator('#editor')).not_to_be_visible()
    expect(page.locator('#view')).to_contain_text('补齐后的公司')
    assert env['repo'].jobs()['total'] == 1


def test_partial_job_requires_complete_fields_before_import(browser_env):
    env, page = browser_env, browser_env['page']
    visit(env)
    page.locator('#scenario').select_option('partial')
    paste_jobs(page)
    form = page.locator('#job-draft-form')
    assert_required_job_fields(form)
    address = form.locator('[name=address]')
    save = page.get_by_role('button', name='确认入库', exact=True)
    save.click()
    assert address.evaluate('n => n.validity.valueMissing')
    for value in ('   ', ' 未披露 '):
        address.fill(value)
        save.click()
        expect(page.locator('#toast')).to_contain_text('请补齐岗位信息')
        expect(form).to_be_visible()
    assert ('POST', '/api/jobs') not in env['requests']
    assert env['repo'].jobs()['total'] == 0
    address.fill('杭州滨江区')
    form.locator('[name=experience_years]').fill('0')
    save.click()
    expect(page.locator('#editor')).not_to_be_visible()
    items = env['repo'].jobs()['items']
    assert len(items) == 1
    item = items[0]
    assert item['data']['address'] == '杭州滨江区'
    assert item['data']['experience_years'] == 0
    assert item['data']['evidence'] == []
    assert env['requests'].count(('POST', '/api/jobs')) == 1
    row(page, item).locator('[data-job-details] > summary').click()
    expect(row(page, item)).to_contain_text('杭州滨江区')
    expect(row(page, item).get_by_role('button', name='评估', exact=True)).to_be_enabled()


def test_legacy_job_requires_complete_fields_on_edit_and_survives_reload(browser_env):
    env, page = browser_env, browser_env['page']
    item = seed_legacy_job(env, experience_years=None, education='')
    visit(env)
    row(page, item).locator('[data-job-details] > summary').click()
    expect(row(page, item)).to_contain_text('未披露')
    row(page, item).get_by_role('button', name='编辑岗位', exact=True).click()
    form = page.locator('#job-form')
    assert_required_job_fields(form)
    form.locator('[name=job_description]').fill('负责服务端开发')
    form.locator('[name=job_requirement]').fill('熟悉 Python')
    form.locator('[name=experience_years]').fill('0')
    education = form.locator('[name=education]')
    education.fill('   ')
    save = page.get_by_role('button', name='保存岗位', exact=True)
    save.click()
    expect(page.locator('#toast')).to_contain_text('请补齐岗位信息')
    education.fill('本科及以上')
    upper = form.locator('[name=salary_max]')
    upper.fill('')
    save.click()
    assert upper.evaluate('n => n.validity.valueMissing')
    update = ('PUT', '/api/jobs/' + item['id'])
    assert update not in env['requests']
    assert env['repo'].job(item['id']) == item
    upper.fill('32000')
    save.click()
    expect(page.locator('#editor')).not_to_be_visible()
    saved = env['repo'].job(item['id'])['data']
    assert saved['experience_years'] == 0 and saved['education'] == '本科及以上'
    assert saved['job_description'] == '负责服务端开发' and saved['job_requirement'] == '熟悉 Python'
    assert saved['salary_min'] == JOBS[0]['salary_min'] and saved['salary_max'] == 32000
    assert env['requests'].count(update) == 1
    page.reload()
    expect(row(page, item)).to_contain_text('负责服务端开发')
    expect(row(page, item)).to_contain_text('熟悉 Python')


def test_interview_reload_and_report_stay_with_the_correct_job(browser_env):
    env, page = browser_env, browser_env['page']
    a, b = seed_jobs(env)
    visit(env)
    job = row(page, a)
    evaluation = job.locator('[data-job-action=evaluate]')
    interview = job.locator('[data-job-action=interview]')
    job.get_by_role('button', name='评估', exact=True).click()
    expect(evaluation.locator('.report')).to_be_visible()
    job.get_by_role('button', name='模拟面试', exact=True).click()
    expect(interview.locator('.interview-chat .bubble')).to_have_count(1)
    interview.get_by_role('textbox', name='面试消息').fill('已经提交的第一轮答案')
    interview.get_by_role('button', name='发送', exact=True).click()
    expect(interview.locator('.interview-chat .bubble')).to_have_count(3)
    interview.get_by_role('textbox', name='面试消息').fill('刷新时应丢弃的草稿')
    page.once('dialog', lambda dialog: dialog.accept())
    page.reload()
    expect(interview.locator('.interview-chat .bubble')).to_have_count(3)
    expect(interview.get_by_role('textbox', name='面试消息')).to_have_value('')
    interview.get_by_role('button', name='结束面试并生成报告').click()
    expect(interview.locator('.report')).to_contain_text('已经提交的第一轮答案')
    expect(interview.locator('.interview-dimensions')).to_contain_text('Agent 基础（固定示例）')
    expect(interview.locator('.interview-dimensions')).to_contain_text('75')
    runs = {r['kind']: r['run_id'] for r in env['repo'].reports()['items']}
    interview.locator('.report .trace > summary').click()
    expect(interview.locator('.trace')).to_contain_text('interview ' + runs['interview'])
    expect(interview.locator('.trace')).not_to_contain_text(runs['evaluation'])
    if not evaluation.evaluate('node => node.open'):
        evaluation.locator(':scope > summary').click()
    evaluation.locator('.report .trace > summary').click()
    expect(evaluation.locator('.trace')).to_contain_text('evaluate ' + runs['evaluation'])
    expect(evaluation.locator('.trace')).not_to_contain_text(runs['interview'])
    expect(row(page, b).locator('.report')).to_have_count(0)
    assert '#/jobs' in page.url


def test_one_poll_network_failure_recovers_without_reposting(browser_env):
    env, page = browser_env, browser_env['page']
    visit(env, 'resume')
    attempts = []

    def intercept(route):
        if route.request.method == 'GET' and re.search(r'/api/runs/[^/?]+', route.request.url):
            attempts.append(route.request.url)
            if len(attempts) == 1:
                route.abort('connectionfailed')
                return
        route.continue_()

    page.route('**/api/runs/**', intercept)
    open_chat(page)
    page.locator('#chat-text').fill('测试一次网络中断')
    page.locator('#chat-form button').click()
    expect(page.locator('#chat-messages')).to_contain_text('固定示例回复', timeout=8000)
    assert len(attempts) >= 2
    assert len([x for x in env['calls'] if x[0] == 'chat']) == 1


def test_finishing_image_clarification_clears_remaining_import_queue(browser_env, tmp_path):
    env, page = browser_env, browser_env['page']
    visit(env)
    page.locator('#scenario').select_option('needs_input')
    with page.expect_file_chooser() as chooser:
        page.get_by_role('button', name='导入截图', exact=True).click()
    chooser.value.set_files(image_files(tmp_path))
    expect(page.locator('[data-page-run=jobs]')).to_contain_text('演示地址歧义')
    page.get_by_role('button', name='结束本次', exact=True).click()
    expect(page.locator('.pending')).to_have_count(0)
    assert len([x for x in env['requests'] if x == ('POST', '/api/documents')]) == 1
    page.locator('#scenario').select_option('success')
    paste_jobs(page)
    page.get_by_role('button', name='确认入库', exact=True).click()
    expect(page.locator('#toast')).to_contain_text('岗位已入库')
    assert not env['second_image'].wait(.3)


@pytest.mark.parametrize('skip_first', [False, True])
def test_images_are_uploaded_one_at_a_time_after_confirmation(browser_env, tmp_path, skip_first):
    env, page = browser_env, browser_env['page']
    visit(env)
    with page.expect_file_chooser() as chooser:
        page.get_by_role('button', name='导入截图', exact=True).click()
    chooser.value.set_files(image_files(tmp_path))
    expect(page.locator('#editor')).to_contain_text('确认待导入岗位')
    assert len([x for x in env['requests'] if x == ('POST', '/api/documents')]) == 1
    if skip_first:
        page.get_by_label('跳过此条').check()
    page.get_by_role('button', name='确认入库', exact=True).click()
    expect(page.locator('#editor')).to_contain_text('确认待导入岗位')
    assert env['second_image'].wait(5)
    assert len([x for x in env['requests'] if x == ('POST', '/api/documents')]) == 2
    ids = [args['document_id'] for action, args in env['calls'] if action == 'extract_jobs']
    assert len(ids) == len(set(ids)) == 2
    evidence = page.locator('#editor details', has=page.locator(f'a[href="/api/documents/{ids[1]}/content"]'))
    evidence.locator('summary').click()
    expect(evidence.get_by_role('link')).to_be_visible()
    expect(page.get_by_text('已上传的招聘截图', exact=False)).to_have_count(0)
    page.get_by_role('button', name='确认入库', exact=True).click()
    expect(page.locator('#editor')).not_to_be_visible()
    jobs = env['repo'].jobs()['items']
    expected = ids[1:] if skip_first else ids
    assert len(jobs) == len(expected)
    assert {job['data']['evidence'][0]['document_id'] for job in jobs} == set(expected)


@pytest.mark.parametrize('continue_batch', [True, False], ids=['continue', 'cancel'])
def test_folded_image_waits_for_user_before_next_upload(browser_env, tmp_path, continue_batch):
    env, page = browser_env, browser_env['page']
    env['image_responses'][1] = AgentResponse(
        status='needs_input', kind='chat', data={'message': '请补充地址'},
        pending_input={'kind': 'question', 'prompt': '请补充地址'}, state={'complete': None},
    )
    visit(env)
    with page.expect_response(lambda r: r.url.endswith('/api/runs') and r.request.method == 'POST') as first:
        import_images(page, image_files(tmp_path))
    dialog = page.locator('#editor')
    expect(dialog).to_contain_text('当前截图的岗位正文被折叠')
    expect(dialog).to_contain_text('a.png')
    proceed = dialog.get_by_role('button', name='跳过并继续下一张', exact=True)
    expect(proceed).to_be_visible()
    run = page.request.get(env['url'] + '/api/runs/' + first.value.json()['id']).json()
    assert run['status'] == 'failed' and run['result']['data']['code'] == 'JOB_TEXT_FOLDED'
    assert run['pending_input'] is None
    assert not env['second_image'].wait(.3)
    assert env['requests'].count(('POST', '/api/documents')) == 1
    assert env['repo'].jobs()['total'] == 0

    if continue_batch:
        proceed.click()
        expect(dialog).to_contain_text('确认待导入岗位')
        ids = [args['document_id'] for action, args in env['calls'] if action == 'extract_jobs']
        assert len(set(ids)) == len(ids) == 2
        dialog.locator('summary', has_text='提取依据').click()
        source = dialog.locator(f'a[href="/api/documents/{ids[1]}/content"]')
        expect(source).to_be_visible()
        original = page.request.get(source.evaluate('link => link.href'))
        assert original.status == 200 and 'b.png' in original.headers['content-disposition']
        expect(page.get_by_text('已上传的招聘截图', exact=False)).to_have_count(0)
        assert env['repo'].jobs()['total'] == 0
        dialog.get_by_role('button', name='确认入库', exact=True).click()
        expect(page.locator('#toast')).to_contain_text('岗位已入库')
        job, = env['repo'].jobs()['items']
        assert [e['document_id'] for e in job['data']['evidence']] == [ids[1]]
        assert env['requests'].count(('POST', '/api/jobs')) == 1
        assert env['requests'].count(('POST', '/api/documents')) == 2
        assert env['requests'].count(('POST', '/api/runs')) == 2
    else:
        dialog.get_by_role('button', name='取消', exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.get_by_role('button', name='导入截图', exact=True)).to_be_enabled()
        assert not env['second_image'].wait(.3)
        assert env['requests'].count(('POST', '/api/documents')) == 1
        assert env['requests'].count(('POST', '/api/runs')) == 1
        assert ('POST', '/api/jobs') not in env['requests']
        assert env['repo'].jobs()['total'] == 0


@pytest.mark.parametrize('last_folded', [True, False], ids=['last-folded', 'ordinary-failure'])
def test_failed_image_stops_without_retry_or_continue(browser_env, tmp_path, last_folded):
    env, page = browser_env, browser_env['page']
    visit(env)
    files = image_files(tmp_path)
    if last_folded:
        env['image_responses'][1] = AgentResponse(kind='job_drafts', data={**JOBS[0], 'complete': None})
        files = files[:1]
        message = '岗位正文被折叠'
    else:
        page.locator('#scenario').select_option('fail')
        message = '固定的失败场景'
    import_images(page, files)
    expect(page.locator('#toast')).to_contain_text(message)
    expect(page.locator('#editor')).not_to_be_visible()
    expect(page.get_by_role('button', name='导入截图', exact=True)).to_be_enabled()
    assert not env['second_image'].wait(.3)
    assert env['requests'].count(('POST', '/api/documents')) == 1
    assert env['requests'].count(('POST', '/api/runs')) == 1
    assert ('POST', '/api/jobs') not in env['requests']
    assert env['repo'].jobs()['total'] == 0


def test_upload_413_stops_batch_without_retry(browser_env, tmp_path):
    env, page = browser_env, browser_env['page']
    attempts = []

    def intercept(route):
        if route.request.method == 'POST':
            attempts.append(route.request.url)
            route.fulfill(status=413, content_type='application/json', body='{}')
        else:
            route.continue_()

    visit(env)
    page.route('**/api/documents', intercept)
    import_images(page, image_files(tmp_path))
    expect(page.locator('#global-error')).to_contain_text('413')
    expect(page.locator('#toast')).to_contain_text('不会重试')
    expect(page.locator('#editor')).not_to_be_visible()
    expect(page.get_by_role('button', name='导入截图', exact=True)).to_be_enabled()
    page.wait_for_timeout(300)
    assert len(attempts) == 1
    assert not env['calls']
    assert env['repo'].jobs()['total'] == 0


def test_old_knowledge_bookmark_returns_to_jobs(browser_env):
    env, page = browser_env, browser_env['page']
    job, _ = seed_jobs(env)
    external_requests = []
    def catch_external(route):
        external_requests.append(route.request.url)
        route.fulfill(body='unexpected redirect')
    page.context.route('http://127.0.0.1:8200/**', catch_external)
    page.goto(env['url'] + '/#/knowledge')
    expect(page).to_have_url(env['url'] + '/#/jobs')
    expect(page.locator('#page-title')).to_have_text('岗位池')
    expect(page.get_by_role('link', name='面试题库')).to_have_count(0)
    assert not external_requests
    row(page, job).get_by_role('button', name='模拟面试', exact=True).click()
    expect(row(page, job).locator('.interview-chat .bubble')).to_have_count(1)


def test_answer_is_sent_once_and_waiting_run_stops_polling(browser_env):
    env, page = browser_env, browser_env['page']
    a, b = seed_jobs(env)
    visit(env)
    row(page, a).get_by_role('button', name='模拟面试', exact=True).click()
    expect(row(page, a).locator('.interview-chat .bubble')).to_have_count(1)
    expect(row(page, b).get_by_role('button', name='评估', exact=True)).to_be_disabled()
    polls = len([x for x in env['requests'] if x[0] == 'GET' and x[1].startswith('/api/runs/')])
    page.wait_for_timeout(850)
    assert len([x for x in env['requests'] if x[0] == 'GET' and x[1].startswith('/api/runs/')]) == polls
    row(page, a).get_by_role('textbox', name='面试消息').fill('同一答案只提交一次')
    row(page, a).locator('[data-run-answer]').evaluate('(form) => { form.requestSubmit(); form.requestSubmit(); }')
    expect(row(page, a).locator('.interview-chat .bubble')).to_have_count(3)
    assert len([x for x in env['requests'] if x[0] == 'POST' and x[1].endswith('/input')]) == 1


def test_page_back_preserves_answer_and_job_editor(browser_env):
    env, page = browser_env, browser_env['page']
    a, _ = seed_jobs(env)
    visit(env)
    row(page, a).get_by_role('button', name='模拟面试', exact=True).click()
    row(page, a).get_by_role('textbox', name='面试消息').fill('离开页面前的思路')
    page.locator('nav [data-page=resume]').click()
    expect(page.locator('#page-title')).to_have_text('简历画像')
    page.go_back()
    expect(row(page, a).get_by_role('textbox', name='面试消息')).to_have_value('离开页面前的思路')
    open_job_details(row(page, a))
    row(page, a).get_by_role('button', name='编辑岗位').click()
    page.locator('#job-form [name=job_requirement]').fill('未保存的任职要求')
    page.get_by_role('button', name='取消', exact=True).click()
    expect(page.locator('#editor')).to_be_visible()
    expect(page.locator('#job-form [name=job_requirement]')).to_have_value('未保存的任职要求')
    page.once('dialog', lambda dialog: dialog.accept())
    page.get_by_role('button', name='取消', exact=True).click()
    expect(page.locator('#editor')).not_to_be_visible()


def test_evaluation_is_inline_routes_and_historical_snapshot_are_preserved(browser_env):
    env, page = browser_env, browser_env['page']
    a, _ = seed_jobs(env)
    visit(env)
    row(page, a).get_by_role('button', name='评估', exact=True).click()
    report = row(page, a).locator('.report')
    expect(report).to_contain_text('匹配分 / 100')
    expect(report).to_contain_text('公交 · 单程')
    expect(report).to_contain_text('公里')
    expect(report).to_contain_text('逐项匹配证据')
    text = report.inner_text()
    assert not any(word in text for word in ['总分', '薪资分', '通勤分', '排名'])
    assert '#/jobs' in page.url
    open_job_details(row(page, a))
    row(page, a).get_by_role('button', name='编辑岗位').click()
    page.locator('#job-form [name=company]').fill('后来修改的公司')
    page.get_by_role('button', name='保存岗位', exact=True).click()
    expect(row(page, a).locator('.job-overview')).to_contain_text('后来修改的公司')
    expect(row(page, a).locator('.report')).to_contain_text('A公司')
    expect(row(page, a).locator('.report')).not_to_contain_text('后来修改的公司')


def test_job_sections_are_independent_and_shared_commute_is_shown_once(browser_env):
    env, page = browser_env, browser_env['page']
    a = env['repo'].save_job({**deepcopy(JOBS[0]), 'company': '北辰公司', 'title': '检索工程师',
                              'evidence': [{'text': '原文里的要求', 'locator': '岗位职责', 'document_id': None}]})
    endpoints = '通勤测试起点 → 通勤测试终点'
    report_id = env['repo'].save_report('evaluation', {
        'title': '测试评估', 'summary': '固定摘要', 'jobs': [{
            'job_id': a['id'], 'match_score': 80, 'recommendation': '准备项目证据',
            'supporting_experiences': '1. 检索评测经历', 'key_requirement': '需要三年经验，已确认一年',
            'matches': [{'requirement': '检索开发', 'evidence': '题库检索项目', 'assessment': '符合'}],
            'commutes': {
                'public_transit': {'duration_minutes': 30, 'distance_km': 8, 'route_summary': endpoints + '\n步行 → 地铁'},
                'driving': {'duration_minutes': 16, 'distance_km': 7.5, 'route_summary': endpoints + '\n环城路'},
            },
        }]}, {'jobs': [a]}, 'saved-evaluation', False)['id']
    visit(env, 'reports?id=' + report_id)
    job = row(page, a)
    sections = job.locator(':scope > details')
    expect(sections).to_have_count(3)
    summaries = sections.locator(':scope > summary')
    texts = summaries.all_text_contents()
    assert '岗位详情' in texts[0] and '评估' in texts[1] and '面试' in texts[2]
    sizes = summaries.evaluate_all('(nodes) => nodes.map(n => getComputedStyle(n).fontSize)')
    assert len(set(sizes)) == 1, sizes

    evaluation = job.locator('[data-job-action=evaluate]')
    interview = job.locator('[data-job-action=interview]')
    report = evaluation.locator('.report')
    expect(report).to_be_visible()
    expect(interview).not_to_have_attribute('open', '')
    headings = report.get_by_role('heading').all_text_contents()
    required = ['求职建议', '最有利的经历', '最不利的关键要求', '逐项匹配证据', '单程通勤']
    assert [head for head in headings if head in required] == required, headings
    assert report.inner_text().count(endpoints) == 1
    cards = report.locator('.commute')
    expect(cards).to_have_count(2)
    expect(cards.nth(0)).to_contain_text('步行 → 地铁')
    expect(cards.nth(1)).to_contain_text('环城路')
    assert all(endpoints not in card.inner_text() for card in cards.all())

    details = open_job_details(job)
    expect(details.locator('dt', has_text='公司名称').locator('+ dd')).to_have_text('北辰公司')
    expect(details.locator('dt', has_text='岗位名称').locator('+ dd')).to_have_text('检索工程师')
    evidence = details.locator('details', has=page.locator('summary', has_text='提取依据'))
    expect(evidence).not_to_have_attribute('open', '')
    evidence.locator('summary').click()
    expect(evidence.get_by_text('原文里的要求', exact=True)).to_be_visible()
    evaluation.locator(':scope > summary').click()
    expect(report).not_to_be_visible()
    expect(details).to_have_attribute('open', '')
    interview.locator(':scope > summary').click()
    expect(interview).to_have_attribute('open', '')
    expect(evaluation).not_to_have_attribute('open', '')


def test_delete_selected_evaluation_and_job(browser_env):
    env, page = browser_env, browser_env['page']
    a, b = seed_jobs(env)
    repo = env['repo']
    for points in (64, 82, 91):
        repo.save_report('evaluation', {'title': f'评估 {points}', 'jobs': [{'job_id': a['id'], 'match_score': points}]},
                         {'jobs': [a]}, f'eval-{points}', True)
    latest, middle, oldest = repo.reports()['items']
    interview = repo.save_report('interview', {'title': '保留的面试'}, {'jobs': [a]}, 'interview', True)
    visit(env, 'reports?id=' + middle['id'])
    job = row(page, a)
    section = job.locator('[data-job-action=evaluate]')
    delete = section.get_by_role('button', name='删除本次评估', exact=True)
    expect(section.locator('.report h2')).to_have_text(middle['data']['title'])
    expect(delete).to_be_visible()
    page.once('dialog', lambda dialog: dialog.dismiss())
    delete.click()
    assert not [entry for entry in env['requests'] if entry[0] == 'DELETE']

    for selected, remaining in ((middle, latest), (latest, oldest), (oldest, None)):
        expect(section.locator('.report h2')).to_have_text(selected['data']['title'])
        page.once('dialog', lambda dialog: dialog.accept())
        delete.click()
        if remaining:
            expect(section.locator('.report h2')).to_have_text(remaining['data']['title'])
            expect(job.locator('.job-score')).to_contain_text(str(remaining['data']['jobs'][0]['match_score']))
        else:
            expect(job.locator('.job-overview')).to_contain_text('待评估')
            expect(delete).to_have_count(0)
    assert [r['id'] for r in repo.reports()['items']] == [interview['id']]
    assert repo.jobs()['total'] == 2
    remove_job = job.locator('.job-overview').get_by_role('button', name='删除岗位', exact=True)
    expect(remove_job).to_be_visible()
    page.once('dialog', lambda dialog: dialog.accept())
    remove_job.click()
    expect(job).to_have_count(0)
    expect(row(page, b)).to_be_visible()
    assert repo.report(interview['id'])['snapshot']['jobs'][0]['id'] == a['id']


def test_old_report_link_shows_missing_job_and_saved_report(browser_env):
    env, page = browser_env, browser_env['page']
    a, _ = seed_jobs(env)
    visit(env)
    row(page, a).get_by_role('button', name='评估', exact=True).click()
    expect(row(page, a).locator('.report')).to_be_visible()
    report_id = env['repo'].reports()['items'][0]['id']
    env['repo'].delete_job(a['id'])
    visit(env, 'reports?id=' + report_id)
    expect(page.locator('#page-title')).to_have_text('岗位池')
    expect(page.locator('#view')).to_contain_text('关联岗位已移除或不存在')
    expect(page.locator('#view > .report')).to_contain_text('A公司')


def test_candidate_gate_and_failed_extraction_are_explicit(browser_env):
    env, page = browser_env, browser_env['page']
    a, _ = seed_jobs(env)
    current = env['candidate'].get_all()
    env['repo'].update_candidate(current['profile'], current['revision'], False)
    visit(env)
    expect(row(page, a).get_by_role('button', name='评估', exact=True)).to_be_disabled()
    expect(row(page, a).get_by_role('button', name='模拟面试', exact=True)).to_be_disabled()
    expect(page.get_by_role('link', name='前往简历画像 →')).to_be_visible()
    page.locator('#scenario').select_option('fail')
    page.get_by_role('button', name='粘贴招聘原文', exact=True).click()
    page.locator('#paste-form textarea').fill('服务失败测试')
    page.get_by_role('button', name='提取岗位', exact=True).click()
    expect(page.locator('[data-page-run=jobs]')).to_contain_text('失败')
    expect(page.locator('#editor')).not_to_be_visible()
    assert env['repo'].jobs()['total'] == 2


def test_empty_names_and_untrusted_text_are_displayed_safely(browser_env):
    env, page = browser_env, browser_env['page']
    item = seed_legacy_job(env, company='', title='', job_description='<img src=x onerror="window.untrusted=true">')
    visit(env)
    row(page, item).get_by_role('button', name='未披露职位', exact=True).click()
    expect(row(page, item)).to_contain_text('未披露公司')
    expect(row(page, item)).to_contain_text('<img src=x onerror=')
    assert page.evaluate('window.untrusted') is None
    assert row(page, item).locator('img').count() == 0


def test_desktop_and_narrow_layout_have_no_horizontal_overflow(browser_env):
    env, page = browser_env, browser_env['page']
    seed_jobs(env)
    for width in (1440, 1024, 390):
        page.set_viewport_size({'width': width, 'height': 1000})
        for destination in ('jobs', 'resume'):
            visit(env, destination)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, destination)


def test_empty_interview_dimensions_are_not_zero_scores(browser_env):
    env, page = browser_env, browser_env['page']
    a, _ = seed_jobs(env)
    visit(env)
    row(page, a).get_by_role('button', name='模拟面试', exact=True).click()
    row(page, a).get_by_role('button', name='结束面试并生成报告').click()
    expect(row(page, a).locator('.interview-dimensions')).to_contain_text('未考察')


def test_profile_partial_draft_requires_manual_completion(browser_env):
    env, page = browser_env, browser_env['page']
    visit(env, 'resume')
    page.locator('#scenario').select_option('partial')
    page.get_by_role('button', name='查看示例画像').click()
    expect(page.locator('#editor')).to_contain_text('固定模拟数据')
    page.get_by_role('button', name='填入画像表单').click()
    expect(page.locator('#profile-form [name=projects]')).to_have_value('')
    page.get_by_role('button', name='保存并确认画像与条件').click()
    expect(page.locator('#candidate-errors')).to_contain_text('项目经历')
    assert not any(method == 'PUT' for method, _ in env['requests'])
    page.locator('#profile-form [name=projects]').fill('人工补充项目经历')
    page.get_by_role('button', name='保存并确认画像与条件').click()
    expect(page.locator('#toast')).to_contain_text('一起确认保存')


def test_chat_proposal_does_not_overwrite_dirty_form_or_revision(browser_env):
    env, page = browser_env, browser_env['page']
    visit(env, 'resume')
    page.locator('#profile-form [name=projects]').fill('应保留的未保存项目')
    page.locator('#scenario').select_option('needs_input')
    open_chat(page)
    page.locator('#chat-text').fill('修改城市的固定演示')
    page.locator('#chat-form button').click()
    expect(page.locator('#chat-drawer .pending')).to_contain_text('演示修改')
    page.locator('#chat-drawer').get_by_role('button', name='确认并保存', exact=True).click()
    expect(page.locator('#chat-drawer .pending')).to_have_count(0)
    page.locator('[data-action=close-chat]').click()
    expect(page.locator('#profile-form [name=projects]')).to_have_value('应保留的未保存项目')
    page.get_by_role('button', name='保存并确认画像与条件').click()
    expect(page.locator('#toast')).to_contain_text('已更新')
    expect(page.locator('#profile-form [name=projects]')).to_have_value('应保留的未保存项目')
    assert env['candidate'].get_all()['profile']['projects'] == PROFILE['projects']


def test_undisclosed_job_is_actionable_and_aliases_stay_in_workbench(browser_env):
    env, page = browser_env, browser_env['page']
    a = seed_legacy_job(env, address='')
    visit(env)
    row(page, a).locator('[data-job-details] > summary').click()
    expect(row(page, a)).to_contain_text('未披露')
    expect(row(page, a).get_by_role('button', name='评估', exact=True)).to_be_enabled()
    expect(row(page, a).get_by_role('button', name='模拟面试', exact=True)).to_be_enabled()
    visit(env, 'preferences')
    expect(page.locator('#page-title')).to_have_text('简历画像')
    for alias in ('evaluate', 'interview'):
        visit(env, alias)
        expect(page.locator('#page-title')).to_have_text('岗位池')
