@echo off
rem Module 7 daily pipeline runner (ASCII only, no Chinese chars)
rem Runs the full daily pipeline and appends output to daily_pipeline.log

set PYEXE=C:\Users\lenovo\AppData\Local\Programs\Python\Python314\python.exe
cd /d "%~dp0"

echo ============================================== >> daily_pipeline.log
echo [%date% %time%] pipeline start >> daily_pipeline.log
"%PYEXE%" daily_pipeline.py >> daily_pipeline.log 2>&1
set EXITCODE=%ERRORLEVEL%
echo [%date% %time%] pipeline end, exit code %EXITCODE% >> daily_pipeline.log

exit /b %EXITCODE%
