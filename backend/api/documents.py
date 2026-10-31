"""资料上传、原文件查看和删除接口。"""
from fastapi import APIRouter, File, Form, Response, UploadFile
from fastapi.responses import FileResponse

from .dependencies import DocumentServiceDep

router = APIRouter(prefix='/documents', tags=['资料'])


@router.get('')
def documents(service: DocumentServiceDep, kind: str | None = None):
    """按查询参数 kind 筛选资料，返回元数据列表 items 和总数 total；None 表示不筛选。"""
    return service.list(kind)


# Form 和 File 让 FastAPI 从 multipart/form-data 分别取出必填文本项和上传文件。
# async 接口中的 await 可在异步等待时让出执行权；这里仍等文件保存完成才返回 201。
@router.post('', status_code=201)
async def upload_document(service: DocumentServiceDep, kind: str = Form(...), file: UploadFile = File(...)):
    """按 kind（image/resume/knowledge）保存 file 和元数据，返回 201 及资料元数据。
    元数据含 id、filename、kind、media_type、size、created_at；校验失败返回 422，超限返回 413。
    """
    return await service.save(file, kind)


@router.get('/{key}/content')
def document_content(key: str, service: DocumentServiceDep):
    """按路径中的资料 ID 返回原文件响应，使用登记的媒体类型和文件名供页面内查看。
    资料记录缺失、文件不存在或路径不在受控目录内时返回 404。
    """
    item = service.get(key)
    return FileResponse(
        service.resolve(key), media_type=item['media_type'], filename=item['filename'],
        content_disposition_type='inline',
    )


@router.delete('/{key}', status_code=204)
def delete_document(key: str, service: DocumentServiceDep):
    """按路径中的资料 ID 删除文件及元数据，成功返回无正文的 204。
    关联题目和岗位来源关系随元数据删除；资料或原文件不存在时返回 404。
    """
    service.delete(key)
    return Response(status_code=204)
