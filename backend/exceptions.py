"""可向调用方说明的应用错误；HTTP 层统一转换成 JSON。"""


class AppError(Exception):
    """携带可公开的错误信息，由 HTTP 层或运行服务决定怎样向调用方展示。"""
    def __init__(self, status: int, code: str, message: str):
        """保存 HTTP 状态码、稳定错误标识和用户提示；message 不应包含机密信息。"""
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
