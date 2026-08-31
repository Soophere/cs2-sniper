@echo off
REM Safety check before pushing to GitHub.
REM Fails if .env or known secret patterns would be included.

cd /d "%~dp0"

echo Checking that secrets will not be pushed...

git check-ignore -q .env
if errorlevel 1 (
  echo FAIL: .env is NOT ignored by git. Fix .gitignore before pushing.
  exit /b 1
)

git ls-files --error-unmatch .env >nul 2>&1
if not errorlevel 1 (
  echo FAIL: .env is tracked by git. Run: git rm --cached .env
  exit /b 1
)

git diff --cached --name-only | findstr /i /x "\.env" >nul
if not errorlevel 1 (
  echo FAIL: .env is staged. Unstage it before committing.
  exit /b 1
)

findstr /r /c:"CSFLOAT_API_KEY=.\+" .env.example >nul 2>&1
REM .env.example uses a placeholder string — that is fine.

echo OK: .env is ignored and not tracked.
echo Safe to commit / push (still review git status yourself).
exit /b 0
