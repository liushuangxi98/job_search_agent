"""报告读取和下载接口。"""
from fastapi import APIRouter, Query, Response

from .dependencies import ReportServiceDep

router = APIRouter(prefix='/reports', tags=['报告'])


@router.get('')
def reports(service: ReportServiceDep):
    return service.list()


@router.get('/{key}')
def report(key: str, service: ReportServiceDep):
    return service.get(key)


@router.get('/{key}/export')
def export_report(key: str, service: ReportServiceDep, format: str = Query('md', pattern='^(md|json)$')):
    return Response(
        service.export(key, format),
        media_type='text/markdown' if format == 'md' else 'application/json',
        headers={'Content-Disposition': f'attachment; filename="report-{key}.{format}"'},
    )
