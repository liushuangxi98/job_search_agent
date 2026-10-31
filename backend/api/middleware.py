"""在 multipart 解析前检查请求实际字节数，统一执行 48 MiB 上限。"""
from fastapi.responses import JSONResponse


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in {'POST', 'PUT', 'PATCH'}:
            return await self.app(scope, receive, send)
        maximum = 48 * 1024 * 1024
        chunks, size = [], 0
        header = dict(scope['headers']).get(b'content-length', b'0')
        try:
            too_large = int(header) > maximum
        except ValueError:
            too_large = False
        if not too_large:
            # 不只信 Content-Length；流式上传也累计实际收到的字节。
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                body = message.get('body', b'')
                size += len(body)
                if size > maximum:
                    too_large = True
                    break
                chunks.append(body)
                if not message.get('more_body', False):
                    break
        if too_large:
            response = JSONResponse({'error': {
                'code': 'REQUEST_TOO_LARGE', 'message': '请求超过 48 MiB；本次操作失败，未自动重试',
            }}, status_code=413)
            return await response(scope, receive, send)
        body, replayed = b''.join(chunks), False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()

        await self.app(scope, replay, send)
