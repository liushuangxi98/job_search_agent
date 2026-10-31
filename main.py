"""PyCharm 中直接 Run/Debug 本文件，即可启动前端页面和后端 API。"""
import uvicorn

from backend.app import create_app

# 命令行的“main:app”按“模块名:对象名”找到这里的应用，导入时不会监听端口。
app = create_app()

__all__ = ['app']


if __name__ == '__main__':
    # 使用单进程方便 PyCharm 断点调试；修改 Python 代码后手动重启。
    uvicorn.run(app, host='127.0.0.1', port=8000)
