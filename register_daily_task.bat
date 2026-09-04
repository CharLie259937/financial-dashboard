@echo off
rem Register Windows daily scheduled task for the financial dashboard pipeline.
rem Task name: FinancialDashboardDailyPipeline
rem Schedule: daily 17:30 (after A-share and HK close; US quotes from the
rem           previous session are already available at that time)
rem To unregister: schtasks /Delete /TN FinancialDashboardDailyPipeline /F

set TASKNAME=FinancialDashboardDailyPipeline
set BATFILE=%~dp0run_daily_pipeline.bat

schtasks /Create /TN "%TASKNAME%" /TR "\"%BATFILE%\"" /SC DAILY /ST 17:30 /F
if %ERRORLEVEL% NEQ 0 (
    echo Failed to create task. Try running this script as Administrator.
    exit /b 1
)

echo Task created successfully.
schtasks /Query /TN "%TASKNAME%"
pause
