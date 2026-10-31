@echo off
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 请先运行 setup.cmd。
  goto failed
)
if not exist ".env" (
  echo 请复制 .env.example 为 .env，填写数据库信息，再运行 init_db.cmd。
  goto failed
)
".venv\Scripts\python.exe" -m backend.init_db
if errorlevel 1 goto failed
echo.
echo 浏览器打开 http://127.0.0.1:8000 ；按 Ctrl+C 停止。
".venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000
if errorlevel 1 goto failed
popd
exit /b 0
:failed
echo 启动失败。请查看上方错误；首次使用请先运行 init_db.cmd。
popd
pause
exit /b 1
