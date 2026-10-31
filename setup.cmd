@echo off
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
if errorlevel 1 goto failed
if not exist ".env" copy ".env.example" ".env" >nul
echo.
echo 依赖安装完成。请填写 .env 中的 MySQL 连接信息，然后运行 init_db.cmd。
popd
pause
exit /b 0
:failed
echo 安装失败。请确认 Python 3.13 可运行，并查看上方错误。
popd
pause
exit /b 1
