"""报告读取和下载接口。"""
from fastapi import APIRouter, Query, Response

from .dependencies import ReportServiceDep, RunServiceDep

router = APIRouter(prefix='/reports', tags=['报告'])


@router.get('')
def reports(service: ReportServiceDep):
    """返回已保存报告的列表 items 和总数 total，每份报告包含结果 data 及当次输入快照 snapshot。"""
    return service.list()


@router.get('/{key}')
def report(key: str, service: ReportServiceDep):
    """按路径中的报告 ID 返回结果和输入快照等完整记录；报告不存在时返回 404。"""
    return service.get(key)


@router.delete('/{key}', status_code=204)
def delete_evaluation(key: str, service: ReportServiceDep):
    service.delete_evaluation(key)
    return Response(status_code=204)


@router.get('/{key}/events')
def report_events(key: str, service: ReportServiceDep, runs: RunServiceDep):
    """返回这份历史报告所属运行的执行记录。"""
    return runs.report_events(service.get(key)['run_id'])


@router.get('/{key}/export')
def export_report(key: str, service: ReportServiceDep, format: str = Query('md', pattern='^(md|json)$')):
    """将 key 对应的报告按查询参数 format（md/json，默认 md）导出，返回带附件文件名的文本响应。
    报告不存在时返回 404，format 不符合 Query 约束时返回 422。
    """
    # 服务已生成 Markdown 或 JSON 文本，直接使用 Response 可避免 JSON 字符串被再次编码。
    return Response(
        service.export(key, format),
        media_type='text/markdown' if format == 'md' else 'application/json',
        headers={'Content-Disposition': f'attachment; filename="report-{key}.{format}"'},
    )
