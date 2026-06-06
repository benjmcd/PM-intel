@echo off
setlocal
python scripts\task.py %*
exit /b %ERRORLEVEL%
