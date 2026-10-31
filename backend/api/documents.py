"""资料上传、原文件查看和删除接口。"""
from fastapi import APIRouter, File, Form, Response, UploadFile
from fastapi.responses import FileResponse

from .dependencies import DocumentServiceDep

router = APIRouter(prefix='/documents', tags=['资料'])


@router.get('')
def documents(service: DocumentServiceDep, kind: str | None = None):
    return service.list(kind)


@router.post('', status_code=201)
async def upload_document(service: DocumentServiceDep, kind: str = Form(...), file: UploadFile = File(...)):
    return await service.save(file, kind)


@router.get('/{key}/content')
def document_content(key: str, service: DocumentServiceDep):
    item = service.get(key)
    return FileResponse(
        service.resolve(key), media_type=item['media_type'], filename=item['filename'],
        content_disposition_type='inline',
    )


@router.delete('/{key}', status_code=204)
def delete_document(key: str, service: DocumentServiceDep):
    service.delete(key)
    return Response(status_code=204)
