"""Real DOM regressions. AI execution must be delegated; no screenshots are taken."""
import asyncio
from dataclasses import dataclass, field
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
from backend.demo import handle as demo_handle

pytestmark = pytest.mark.browser


@dataclass
class Gate:
    enabled: bool = False
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)


@pytest.fixture
def browser_env(settings):
    gate, calls = Gate(), []
    second_image = threading.Event()

    async def handler(command, services, emit):
        calls.append((command.action, dict(command.input)))
        images = [x for x in calls if x[0] == 'extract_jobs' and x[1].get('document_id')]
        if len(images) > 1:
            second_image.set()
        if command.action == 'extract_jobs' and command.input.get('text') == 'missing-company':
            return AgentResponse(kind='job_drafts', data={'jobs': [
                {'company': '', 'title': '需要补齐的岗位', 'city': '杭州', 'jd_text': '测试资料'}
            ], 'notice': '缺失字段测试样例'})
        return await demo_handle(command, services, emit)

    app = create_app(settings, handler=handler)
    repo = app.state.agent_data_service
    repo.update_candidate('profile', {'summary': '已确认的测试经历'}, 0, True)

    class SaveGate(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
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
                   'gate': gate, 'calls': calls, 'second_image': second_image, 'errors': errors}
            browser.close()
    finally:
        gate.release.set()
        server.should_exit = True
        thread.join(8)
        listener.close()
    assert not errors, errors


def visit(env, page='resume'):
    env['page'].goto(env['url'] + '/#/' + page)
    expect(env['page'].locator('#connection')).to_contain_text('MySQL 已连接')


def seed_jobs(env):
    a = env['repo'].save_job({'company': 'A公司', 'title': 'A岗位', 'city': '杭州', 'jd_text': 'A资料'})
    b = env['repo'].save_job({'company': 'B公司', 'title': 'B岗位', 'city': '杭州', 'jd_text': 'B资料'})
    return a, b


def open_chat(page):
    page.locator('.chat-fab').click()
    expect(page.locator('#chat-drawer')).to_be_visible()


def paste_jobs(page, text='普通招聘文本'):
    page.get_by_role('button', name='粘贴招聘原文').click()
    page.locator('#paste-form textarea').fill(text)
    page.get_by_role('button', name='提取岗位', exact=True).click()
    expect(page.locator('#editor')).to_contain_text('确认待导入岗位')


def test_chat_preserves_unsaved_profile_form(browser_env):
    env, page = browser_env, browser_env['page']
    visit(env)
    page.locator('#profile-form [name=summary]').fill('还没有保存的个人简介')
    open_chat(page)
    page.locator('#chat-text').fill('解释一下这个页面')
    page.locator('#chat-form button').click()
    expect(page.locator('#chat-messages')).to_contain_text('固定示例回复')
    page.wait_for_load_state('networkidle')
    expect(page.locator('#profile-form [name=summary]')).to_have_value('还没有保存的个人简介')


def test_old_run_view_does_not_replace_active_run(browser_env):
    env, page = browser_env, browser_env['page']
    seed_jobs(env)
    visit(env, 'jobs')
    page.locator('[data-select-job]').first.check()
    page.locator('#evaluate-selection').click()
    expect(page.locator('#view')).to_contain_text('已完成')
    old_url = page.url
    page.locator('nav [data-page=jobs]').click()
    page.locator('#scenario').select_option('needs_input')
    page.locator('#evaluate-selection').click()
    expect(page.locator('#chat-drawer')).to_contain_text('演示地址歧义')
    page.goto(old_url)
    expect(page.locator('#chat-drawer')).to_contain_text('演示地址歧义')
    page.locator('#chat-drawer [data-run-answer] textarea').fill('杭州软件园')
    page.locator('#chat-drawer [data-run-answer] button', has_text='提交并继续').click()
    expect(page.locator('#chat-drawer .pending')).to_have_count(0)
    assert env['repo'].reports()['total'] == 2


def test_cancel_import_stops_unsent_jobs(browser_env):
    env, page = browser_env, browser_env['page']
    visit(env, 'jobs')
    paste_jobs(page)
    env['gate'].enabled = True
    page.get_by_role('button', name='确认入库', exact=True).click()
    assert env['gate'].started.wait(3)
    page.get_by_role('button', name='取消', exact=True).click()
    env['gate'].release.set()
    expect(page.locator('#toast')).to_contain_text(re.compile('岗位已入库|已停止后续导入'))
    assert env['repo'].jobs()['total'] == 1


def test_incomplete_job_draft_can_be_corrected_before_saving(browser_env):
    env, page = browser_env, browser_env['page']
    visit(env, 'jobs')
    paste_jobs(page, 'missing-company')
    expect(page.locator('#editor input[name=company]')).to_have_count(1)
    page.locator('#editor input[name=company]').fill('补齐后的公司')
    page.get_by_role('button', name='确认入库', exact=True).click()
    expect(page.locator('#editor')).not_to_be_visible()
    expect(page.locator('#view')).to_contain_text('补齐后的公司')
    assert env['repo'].jobs()['total'] == 1


def test_interview_reload_and_report_stay_with_the_correct_job(browser_env):
    env, page = browser_env, browser_env['page']
    seed_jobs(env)
    visit(env, 'jobs')
    page.get_by_role('link', name='A岗位', exact=True).click()
    page.get_by_role('button', name='模拟面试', exact=True).click()
    expect(page.locator('#view')).to_contain_text('第 1 / 最多 8 轮')
    page.reload()
    expect(page.locator('#view')).to_contain_text('第 1 / 最多 8 轮')
    page.get_by_role('button', name='提前结束并生成报告').click()
    expect(page.locator('#job-drawer')).to_be_visible()
    expect(page.locator('#job-drawer')).to_contain_text('A岗位')
    expect(page.locator('#job-drawer [data-action=view-report]')).to_have_count(1)
    page.locator('#job-drawer [data-action=close-job]').click()
    page.get_by_role('link', name='B岗位', exact=True).click()
    expect(page.locator('#job-drawer [data-action=view-report]')).to_have_count(0)


def test_one_poll_network_failure_recovers_without_reposting(browser_env):
    env, page = browser_env, browser_env['page']
    visit(env)
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
    assert len([item for item in env['calls'] if item[0] == 'chat']) == 1


def test_finishing_image_clarification_clears_remaining_import_queue(browser_env, tmp_path):
    import base64
    env, page = browser_env, browser_env['page']
    data = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1xQAAAAASUVORK5CYII=')
    paths = [tmp_path / 'a.png', tmp_path / 'b.png']
    for path in paths:
        path.write_bytes(data)
    visit(env, 'jobs')
    page.locator('#scenario').select_option('needs_input')
    with page.expect_file_chooser() as chooser:
        page.get_by_role('button', name='导入截图', exact=True).click()
    chooser.value.set_files([str(x) for x in paths])
    expect(page.locator('#chat-drawer')).to_contain_text('演示地址歧义')
    page.get_by_role('button', name='结束本次', exact=True).click()
    expect(page.locator('#chat-drawer .pending')).to_have_count(0)
    page.locator('#chat-drawer [data-action=close-chat]').click()
    page.locator('#scenario').select_option('success')
    paste_jobs(page)
    page.get_by_role('button', name='确认入库', exact=True).click()
    expect(page.locator('#toast')).to_contain_text('岗位已入库')
    assert not env['second_image'].wait(1), 'A cancelled image batch unexpectedly resumed'


def test_knowledge_upload_and_manual_edit(browser_env, tmp_path):
    env, page = browser_env, browser_env['page']
    source = tmp_path / '题库验收.md'
    source.write_text('# 测试题\n什么是工具调用？\n', encoding='utf-8')
    visit(env, 'knowledge')
    with page.expect_file_chooser() as chooser:
        page.get_by_role('button', name='选择文件', exact=True).click()
    chooser.value.set_files(str(source))
    expect(page.locator('#view')).to_contain_text('题库验收.md')
    page.get_by_role('button', name='＋ 手动添加题目').click()
    page.locator('#question-form [name=question]').fill('手动测试题')
    page.locator('#question-form [name=answer]').fill('手动测试答案')
    page.get_by_role('button', name='保存题目', exact=True).click()
    expect(page.locator('#view')).to_contain_text('手动测试题')
    page.locator('[data-action=edit-question]').click()
    page.locator('#question-form [name=question]').fill('更新后的测试题')
    page.get_by_role('button', name='保存题目', exact=True).click()
    expect(page.locator('#view')).to_contain_text('更新后的测试题')
