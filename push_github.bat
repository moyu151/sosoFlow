@echo off
setlocal

REM 用法:
REM push_github.bat "your commit message"

set REPO_DIR=%~dp0
cd /d "%REPO_DIR%"

if "%~1"=="" (
  echo [ERROR] 请提供提交信息。
  echo 用法: push_github.bat "your commit message"
  exit /b 1
)

set COMMIT_MSG=%~1

echo [INFO] 当前目录: %CD%
echo [INFO] 拉取最新 main...
git pull --rebase origin main
if errorlevel 1 (
  echo [ERROR] git pull 失败，请先解决冲突。
  exit /b 1
)

echo [INFO] 暂存变更...
git add .

git diff --cached --quiet
if not errorlevel 1 (
  echo [INFO] 提交: %COMMIT_MSG%
  git commit -m "%COMMIT_MSG%"
) else (
  echo [INFO] 没有需要提交的变更，跳过 commit。
)

echo [INFO] 推送到 GitHub origin/main...
git push origin main
if errorlevel 1 (
  echo [ERROR] git push 失败。
  exit /b 1
)

echo [DONE] GitHub 发布完成。
exit /b 0
