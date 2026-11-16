"""验证正文完整保存、普通查询不加载，以及完整导出的还原。"""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.dependencies import get_report_service
from backend.api.reports import router
from backend.config import Settings
from backend.contracts import AgentResponse
from backend.models import Base, Report
from backend.services.report_service import ReportService
from backend.services.run_service import Run, RunService


@pytest.fixture
def reports():
    # 临时库只驻留内存，HTTP 路由与服务共享同一个连接池。
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    service = ReportService(sessionmaker(engine, expire_on_commit=False))
    # 记录 SELECT 列名，验证正文在数据库查询阶段就被排除。
    statements = []
    def record_sql(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', record_sql)
    try:
        yield service, engine, statements
    finally:
        engine.dispose()


def payload():
    # 同一链接在不同岗位下可有不同正文，空正文也应完整保留。
    return {'title': '岗位评估', 'jobs': [
        {'job_id': 'job-a', 'business_analysis': '业务说明', 'business_sources': [
            {'title': '官网', 'url': 'https://example.com', 'published_time': '2026-11-01', 'content': '完整正文甲' * 30000},
            {'title': '空正文', 'url': 'https://example.com/empty', 'content': ''}]},
        {'job_id': 'job-b', 'business_sources': [
            {'title': '官网', 'url': 'https://example.com', 'content': '完整正文乙'}]},
    ]}


def test_storage_and_normal_http_queries(reports):
    service, engine, statements = reports
    data = payload()
    # 保存前保留测试样本，核对服务没有修改调用方持有的数据。
    original = deepcopy(data)
    saved = service.save('evaluation', data, {'jobs': []}, 'run-a', False)
    assert data == original
    # 独立读取正文列，核对拆分后没有丢内容或串岗位。
    with Session(engine) as session:
        contents = session.scalar(select(Report.source_contents).where(Report.id == saved['id']))
        assert contents['job-a']['https://example.com'] == original['jobs'][0]['business_sources'][0]['content']
        assert contents['job-a']['https://example.com/empty'] == ''
        assert contents['job-b']['https://example.com'] == '完整正文乙'

    # 通过现有 HTTP 列表与详情接口检查实际响应和实际执行的 SELECT。
    app = FastAPI()
    app.include_router(router, prefix='/api')
    app.dependency_overrides[get_report_service] = lambda: service
    statements.clear()
    with TestClient(app) as client:
        for path in ('/api/reports', '/api/reports/' + saved['id']):
            response = client.get(path)
            assert response.status_code == 200
            assert '完整正文' not in response.text and 'source_contents' not in response.text
            assert 'https://example.com' in response.text and '业务说明' in response.text
    # 两次普通请求应各执行一次轻量查询，没有额外触发正文懒加载。
    selects = [sql for sql in statements if sql.lstrip().startswith('SELECT')]
    assert len(selects) == 2 and all('source_contents' not in sql for sql in selects)

    # Markdown 只带展示内容，完整 JSON 导出才读取正文并恢复原有结构。
    statements.clear()
    assert '完整正文' not in service.export(saved['id'], 'md')
    assert all('source_contents' not in sql for sql in statements)
    # 完整导出还原后逐字段对比，正文中的重复字符和空字符串均不可丢失。
    exported = json.loads(service.export(saved['id'], 'json'))
    assert exported['data'] == original
    assert any('source_contents' in sql for sql in statements)

    # 相同运行重试仍返回最初保存的数据，不覆盖正文。
    service.save('evaluation', {'jobs': []}, {}, 'run-a', False)
    assert json.loads(service.export(saved['id'], 'json'))['data'] == original


def test_completed_run_uses_public_report_data(reports, tmp_path):
    service, _, _ = reports
    data = payload()
    # 入口替身返回含正文的原始分析，交给真实保存流程处理。
    async def handler(command, services, emit):
        return AgentResponse(kind='evaluation_report', data=data)
    # 使用真实报告保存服务，验证刚结束时的运行轮询也不携带正文。
    repository = SimpleNamespace(save_report=service.save)
    manager = RunService(Settings(_env_file=None, data_dir=tmp_path), repository, None, handler)
    run = Run('run-poll', 'evaluate', {}, {}, {}, False)
    manager.runs[run.id] = run
    asyncio.run(manager.execute(run))
    # 保存完成后轮询结果应改用仓储返回的展示数据。
    assert run.status == 'completed'
    # 检查浏览器实际使用的运行查询返回值，而非只检查报告服务。
    public = manager.get(run.id)
    assert '完整正文' not in json.dumps(public, ensure_ascii=False)
    # 结束时写入会话的报告消息同样使用精简数据，避免后续上下文重复携带正文。
    assert '完整正文' not in json.dumps(manager.chat_session(run).messages, ensure_ascii=False)
    # 即使公开运行数据已精简，数据库中的全文仍可完整导出。
    assert json.loads(service.export(run.report_id, 'json'))['data']['jobs'][0]['business_sources'][0]['content'] == data['jobs'][0]['business_sources'][0]['content']


def test_report_without_search_sources(reports):
    service, engine, _ = reports
    # 面试和未联网的旧式报告也能正常保存，新列使用空字典。
    for kind, data in [('interview', {'answers': []}), ('evaluation', {'jobs': [{'job_id': 'job'}]})]:
        saved = service.save(kind, data, {}, kind, False)
        assert service.get(saved['id'])['data'] == data
        assert json.loads(service.export(saved['id'], 'json'))['data'] == data
        with Session(engine) as session:
            assert session.scalar(select(Report.source_contents).where(Report.id == saved['id'])) == {}
