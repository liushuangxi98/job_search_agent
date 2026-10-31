"""可向调用方说明的应用错误；HTTP 层统一转换成 JSON。"""


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
