@echo off
title Financial Dashboard
cd /d "%~dp0"

rem Kill any previous dashboard instance still listening on port 8501
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8501" ^| findstr "LISTENING"') do taskkill /PID %%p /F >nul 2>&1

echo Starting dashboard at http://localhost:8501 ...
"C:\Users\lenovo\AppData\Local\Programs\Python\Python314\python.exe" -m streamlit run dashboard.py --server.port 8501

echo.
echo Dashboard stopped.
pause
