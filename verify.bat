@echo off
cd /d "%~dp0"
python -B -m unittest discover -s tests -p "test_*.py" -v
if errorlevel 1 goto failed
python -B -m tests.e2e local.toml
if errorlevel 1 goto failed
echo VALIDATION=PASS
exit /b 0
:failed
echo VALIDATION=BLOCKED
pause
exit /b 1
