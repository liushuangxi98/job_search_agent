"""三入口工作台 DOM 回归：随机端口、隔离数据库、固定 demo，无真实模型。"""
import asyncio
import base64
from copy import deepcopy
from dataclasses import dataclass, field
import json
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
from backend.schemas import ProfileData

pytestmark = pytest.mark.browser


@dataclass
class Gate:
    enabled: bool = False
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)


@pytest.fixture
def browser_env(settings):
    gate, calls, contexts, requests = Gate(), [], [], []
    second_image = threading.Event()

    async def handler(command, services, emit):
        calls.append((command.action, dict(command.input)))
        contexts.append(dict(command.context))
        images = [x for x in calls if x[0] == 'extract_jobs' and x[1].get('document_id')]
        if len(images) > 1:
            second_image.set()
        if command.action == 'extract_jobs' and command.input.get('text') == 'missing-company':
            job = {**deepcopy(JOBS[0]), 'company': '', 'title': '需要补齐的岗位'}
            return AgentResponse(kind='job_drafts', data={'jobs': [job], 'notice': '缺失字段测试样例'})
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
                   'second_image': second_image, 'errors': errors}
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


def test_default_home_has_only_three_entries(browser_env):
    env, page = browser_env, browser_env['page']
    seed_jobs(env)
    visit(env, '')
    expect(page.locator('#page-title')).to_have_text('岗位池')
    assert page.locator('nav [data-page]').evaluate_all('(nodes) => nodes.map(n => n.dataset.page)') == ['resume', 'knowledge', 'jobs']
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
    major.fill('软件工程')
    summary.fill('负责测试平台研发')
    mode.select_option(label='驾车')
    save = form.get_by_role('button', name='保存并确认画像与条件')
    save.click()
    expect(page.locator('#toast')).to_contain_text('一起确认保存')
    page.reload()
    expect(major).to_have_value('软件工程')
    expect(summary).to_have_value('负责测试平台研发')
    expect(mode).to_have_value('driving')

    page.get_by_role('button', name='查看示例画像').click()
    preview = page.locator('#editor')
    for label, value in [('大学专业', PROFILE['major']), ('个人简介', PROFILE['summary']),
                         ('出行偏好', '公共交通')]:
        expect(preview.locator('dt', has_text=label).locator('+ dd')).to_have_text(value)
    expect(preview).to_contain_text('参与自动化项目 resume_dom · 第 2 页')
    preview.get_by_role('button', name='填入画像表单').click()
    expect(major).to_have_value(PROFILE['major'])
    expect(summary).to_have_value(PROFILE['summary'])
    expect(mode).to_have_value('public_transit')
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


@pytest.mark.parametrize('field_name', ['name', 'education', 'major', 'experience_years', 'skills', 'experience',
                                        'projects', 'direction', 'city', 'commute_origin', 'salary_min', 'salary_max'])
def test_candidate_required_fields_block_before_request(browser_env, field_name):
    env, page = browser_env, browser_env['page']
    visit(env, 'resume')
    page.locator(f'#profile-form [name={field_name}]').fill('')
    page.get_by_role('button', name='保存并确认画像与条件').click()
    expect(page.locator('#candidate-errors')).to_be_visible()
    assert not any(method == 'PUT' for method, _ in env['requests'])


def test_old_run_view_does_not_replace_active_run(browser_env):
    env, page = browser_env, browser_env['page']
    a, _ = seed_jobs(env)
    visit(env)
    row(page, a).get_by_role('button', name='评估', exact=True).click()
    expect(row(page, a).locator('.history')).to_be_visible()
    old_url = page.url
    page.locator('#scenario').select_option('needs_input')
    row(page, a).get_by_role('button', name='评估', exact=True).click()
    expect(row(page, a).locator('.pending')).to_contain_text('演示地址歧义')
    page.goto(old_url)
    expect(row(page, a).locator('.pending')).to_contain_text('演示地址歧义')
    expect(page.locator('#chat-drawer')).not_to_be_visible()
    row(page, a).locator('[data-run-answer] textarea').fill('杭州软件园')
    row(page, a).get_by_role('button', name='提交并继续').click()
    try:
        expect(row(page, a).locator('.history summary').first).to_contain_text('2 次')
    except AssertionError:
        print('Current run:', row(page, a).locator('.run-panel').inner_text())
        print('Toast:', page.locator('#toast').inner_text())
        print('Last requests:', env['requests'][-12:])
        raise
    assert env['repo'].reports()['total'] == 2


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
    page.get_by_role('button', name='确认入库', exact=True).click()
    expect(page.locator('#toast')).to_contain_text('请补充公司')
    assert env['repo'].jobs()['total'] == 0
    page.locator('#editor input[name=company]').fill('补齐后的公司')
    page.get_by_role('button', name='确认入库', exact=True).click()
    expect(page.locator('#editor')).not_to_be_visible()
    expect(page.locator('#view')).to_contain_text('补齐后的公司')
    assert env['repo'].jobs()['total'] == 1


def test_partial_job_requires_explicit_blind_spot_confirmation(browser_env):
    env, page = browser_env, browser_env['page']
    visit(env)
    page.locator('#scenario').select_option('partial')
    paste_jobs(page)
    first = page.locator('[data-draft-index="0"]')
    expect(first.locator('[data-blind=address]')).not_to_be_checked()
    page.get_by_role('button', name='确认入库', exact=True).click()
    expect(page.locator('#toast')).to_contain_text('请补充办公地址')
    assert env['repo'].jobs()['total'] == 0
    first.locator('[data-blind=address]').check()
    first.locator('[name=blind_address]').fill('')
    page.get_by_role('button', name='确认入库', exact=True).click()
    expect(page.locator('#toast')).to_contain_text('盲区说明')
    first.locator('[name=blind_address]').fill('已查看原图，招聘方没有披露具体地点')
    page.get_by_role('button', name='确认入库', exact=True).click()
    expect(page.locator('#editor')).not_to_be_visible()
    items = env['repo'].jobs()['items']
    assert len(items) == 2
    assert any(x['data']['blind_spots'].get('address') == '已查看原图，招聘方没有披露具体地点' for x in items)


def test_interview_reload_and_report_stay_with_the_correct_job(browser_env):
    env, page = browser_env, browser_env['page']
    a, b = seed_jobs(env)
    visit(env)
    row(page, a).get_by_role('button', name='模拟面试', exact=True).click()
    expect(row(page, a)).to_contain_text('第 1 / 最多 8 轮')
    row(page, a).get_by_role('textbox', name='面试回答').fill('已经提交的第一轮答案')
    row(page, a).get_by_role('button', name='提交回答', exact=True).click()
    expect(row(page, a)).to_contain_text('第 2 / 最多 8 轮')
    row(page, a).get_by_role('textbox', name='面试回答').fill('刷新时应丢弃的草稿')
    page.once('dialog', lambda dialog: dialog.accept())
    page.reload()
    expect(row(page, a)).to_contain_text('第 2 / 最多 8 轮')
    expect(row(page, a).get_by_role('textbox', name='面试回答')).to_have_value('')
    row(page, a).get_by_role('button', name='提前结束并生成报告').click()
    expect(row(page, a).locator('.report')).to_contain_text('已经提交的第一轮答案')
    expect(row(page, a).locator('.interview-dimensions')).to_contain_text('Agent 基础（固定示例）')
    expect(row(page, a).locator('.interview-dimensions')).to_contain_text('75')
    expect(row(page, b).locator('.history')).to_have_count(0)
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


def test_images_are_uploaded_one_at_a_time_after_confirmation(browser_env, tmp_path):
    env, page = browser_env, browser_env['page']
    visit(env)
    with page.expect_file_chooser() as chooser:
        page.get_by_role('button', name='导入截图', exact=True).click()
    chooser.value.set_files(image_files(tmp_path))
    expect(page.locator('#editor')).to_contain_text('确认待导入岗位')
    assert len([x for x in env['requests'] if x == ('POST', '/api/documents')]) == 1
    page.get_by_role('button', name='确认入库', exact=True).click()
    expect(page.locator('#editor')).to_contain_text('确认待导入岗位')
    page.wait_for_function('document.querySelectorAll(".file-row").length >= 2')
    assert len([x for x in env['requests'] if x == ('POST', '/api/documents')]) == 2
    page.get_by_role('button', name='取消', exact=True).click()


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
    with page.expect_file_chooser() as chooser:
        page.get_by_role('button', name='导入截图', exact=True).click()
    chooser.value.set_files(image_files(tmp_path))
    expect(page.locator('#global-error')).to_contain_text('413')
    expect(page.locator('#toast')).to_contain_text('不会重试')
    page.wait_for_timeout(300)
    assert len(attempts) == 1
    assert not env['calls']


def test_knowledge_upload_and_manual_edit(browser_env, tmp_path):
    env, page = browser_env, browser_env['page']
    source = tmp_path / '题库验收.md'
    source.write_text('# 测试题\n什么是工具调用？\n', encoding='utf-8')
    visit(env, 'knowledge')
    with page.expect_file_chooser() as chooser:
        page.get_by_role('button', name='选择文件', exact=True).click()
    chooser.value.set_files(str(source))
    expect(page.locator('#editor')).to_contain_text('核对题库处理结果')
    assert any(x[0] == 'index_knowledge' for x in env['calls'])
    page.get_by_role('button', name='确认保存题目', exact=True).click()
    expect(page.locator('#editor')).not_to_be_visible()
    expect(page.locator('#view')).to_contain_text('题库验收.md')
    page.get_by_role('button', name='＋ 手动添加题目').click()
    page.locator('#question-form [name=question]').fill('手动测试题')
    page.locator('#question-form [name=answer]').fill('手动测试答案')
    page.get_by_role('button', name='保存题目', exact=True).click()
    expect(page.locator('#view')).to_contain_text('手动测试题')
    page.locator('.question-item', has_text='手动测试题').locator('[data-action=edit-question]').click()
    page.locator('#question-form [name=question]').fill('更新后的测试题')
    page.get_by_role('button', name='保存题目', exact=True).click()
    expect(page.locator('#view')).to_contain_text('更新后的测试题')


def test_answer_is_sent_once_and_waiting_run_stops_polling(browser_env):
    env, page = browser_env, browser_env['page']
    a, b = seed_jobs(env)
    visit(env)
    row(page, a).get_by_role('button', name='模拟面试', exact=True).click()
    expect(row(page, a)).to_contain_text('第 1 / 最多 8 轮')
    expect(row(page, b).get_by_role('button', name='评估', exact=True)).to_be_disabled()
    polls = len([x for x in env['requests'] if x[0] == 'GET' and x[1].startswith('/api/runs/')])
    page.wait_for_timeout(850)
    assert len([x for x in env['requests'] if x[0] == 'GET' and x[1].startswith('/api/runs/')]) == polls
    row(page, a).get_by_role('textbox', name='面试回答').fill('同一答案只提交一次')
    row(page, a).locator('[data-run-answer]').evaluate('(form) => { form.requestSubmit(); form.requestSubmit(); }')
    expect(row(page, a)).to_contain_text('第 2 / 最多 8 轮')
    assert len([x for x in env['requests'] if x[0] == 'POST' and x[1].endswith('/input')]) == 1


def test_page_back_preserves_answer_and_job_editor(browser_env):
    env, page = browser_env, browser_env['page']
    a, _ = seed_jobs(env)
    visit(env)
    row(page, a).get_by_role('button', name='模拟面试', exact=True).click()
    row(page, a).get_by_role('textbox', name='面试回答').fill('离开页面前的思路')
    page.locator('nav [data-page=knowledge]').click()
    expect(page.locator('#page-title')).to_have_text('面试题库')
    page.go_back()
    expect(row(page, a).get_by_role('textbox', name='面试回答')).to_have_value('离开页面前的思路')
    row(page, a).get_by_role('button', name='编辑岗位').click()
    page.locator('#job-form [name=notes]').fill('未保存的岗位备注')
    page.get_by_role('button', name='取消', exact=True).click()
    expect(page.locator('#editor')).to_be_visible()
    expect(page.locator('#job-form [name=notes]')).to_have_value('未保存的岗位备注')
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
    expect(report).to_contain_text('逐项匹配依据')
    text = report.inner_text()
    assert not any(word in text for word in ['总分', '薪资分', '通勤分', '排名'])
    assert '#/jobs' in page.url
    row(page, a).get_by_role('button', name='编辑岗位').click()
    page.locator('#job-form [name=company]').fill('后来修改的公司')
    page.get_by_role('button', name='保存岗位', exact=True).click()
    expect(row(page, a).locator('.job-overview')).to_contain_text('后来修改的公司')
    expect(row(page, a).locator('.report')).to_contain_text('A公司')
    expect(row(page, a).locator('.report')).not_to_contain_text('后来修改的公司')


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
    item = env['repo'].save_job({**deepcopy(JOBS[0]), 'company': '', 'title': '',
                               'jd_text': '<img src=x onerror="window.untrusted=true">',
                               'blind_spots': {'company': '未披露名称', 'title': '未披露职位名'}})
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
        for destination in ('jobs', 'resume', 'knowledge'):
            visit(env, destination)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, destination)


def test_empty_interview_dimensions_are_not_zero_scores(browser_env):
    env, page = browser_env, browser_env['page']
    a, _ = seed_jobs(env)
    visit(env)
    row(page, a).get_by_role('button', name='模拟面试', exact=True).click()
    row(page, a).get_by_role('button', name='提前结束并生成报告').click()
    expect(row(page, a).locator('.interview-dimensions')).to_contain_text('未考察')


def test_profile_partial_draft_requires_manual_completion(browser_env):
    env, page = browser_env, browser_env['page']
    visit(env, 'resume')
    page.locator('#scenario').select_option('partial')
    page.get_by_role('button', name='查看示例画像').click()
    expect(page.locator('#editor')).to_contain_text('固定缺项示例')
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


def test_legacy_incomplete_job_is_gated_and_aliases_stay_in_workbench(browser_env):
    env, page = browser_env, browser_env['page']
    a, _ = seed_jobs(env)

    def incomplete(route):
        response = route.fetch()
        data = response.json()
        for item in data['items']:
            if item['id'] == a['id']:
                item.update(ready=False, missing=[{'field': 'address', 'label': '办公地址'}])
                item['data']['address'] = ''
        route.fulfill(response=response, json=data)

    page.route('**/api/jobs?*', incomplete)
    visit(env)
    expect(row(page, a)).to_contain_text('待完善')
    expect(row(page, a).get_by_role('button', name='评估', exact=True)).to_be_disabled()
    expect(row(page, a).get_by_role('button', name='模拟面试', exact=True)).to_be_disabled()
    visit(env, 'preferences')
    expect(page.locator('#page-title')).to_have_text('简历画像')
    for alias in ('evaluate', 'interview'):
        visit(env, alias)
        expect(page.locator('#page-title')).to_have_text('岗位池')
