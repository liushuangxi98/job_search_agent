@echo off
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 请先运行 setup.cmd。
  goto failed
)
".venv\Scripts\python.exe" -m backend.init_db --create-database
if errorlevel 1 goto failed
echo 初始化完成。已有数据未清空，现在可以运行 start.cmd。
popd
pause
exit /b 0
:failed
echo 初始化未完成。请检查 .env、MySQL 服务与账号权限。
popd
pause
exit /b 1
