@echo off
setlocal

REM 用法:
REM 1) 临时传参（推荐，不落盘）:
REM    push_justrunmy.bat https://USER:TOKEN@justrunmy.app/git/REPO_ID
REM 2) 或预先设置环境变量:
REM    set JRM_GIT_URL=https://USER:TOKEN@justrunmy.app/git/REPO_ID
REM    push_justrunmy.bat

set REPO_DIR=%~dp0
cd /d "%REPO_DIR%"

set TARGET_URL=%~1
if "%TARGET_URL%"=="" set TARGET_URL=%JRM_GIT_URL%

if "%TARGET_URL%"=="" (
  echo [ERROR] 未提供 JustRunMy Git URL。
  echo 用法: push_justrunmy.bat https://USER:TOKEN@justrunmy.app/git/REPO_ID
  echo 或先设置环境变量 JRM_GIT_URL
  exit /b 1
)

echo [INFO] 当前目录: %CD%
echo [INFO] 推送 HEAD 到 JustRunMy deploy...
git push -u "%TARGET_URL%" HEAD:deploy
if errorlevel 1 (
  echo [ERROR] 推送失败。
  exit /b 1
)

echo [DONE] JustRunMy 更新触发成功（deploy 分支）。
exit /b 0
