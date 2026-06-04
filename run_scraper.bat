@echo off
cd /d "C:\Users\testuser\Documents\kabu-stop"

set LOGFILE=scraper_log.txt
set PYTHON=C:\Users\testuser\AppData\Local\Python\bin\python.exe
set GIT=C:\Program Files\Git\cmd\git.exe
set PATH=C:\Program Files\Git\cmd;C:\Program Files\Git\bin;%PATH%

for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set DATESTR=%%d
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format HH:mm:ss"') do set TIMESTR=%%t
echo === %DATESTR% %TIMESTR% === >> %LOGFILE%

"%PYTHON%" scraper.py >> %LOGFILE% 2>&1
if %ERRORLEVEL% neq 0 (
    echo SKIP: scraper failed >> %LOGFILE%
    echo. >> %LOGFILE%
    exit /b 0
)

"%GIT%" add data/stock_data.json >> %LOGFILE% 2>&1
"%GIT%" diff --staged --quiet
if %ERRORLEVEL% neq 0 (
    "%GIT%" commit -m "Update stock data: %DATESTR%" >> %LOGFILE% 2>&1
    "%GIT%" pull --rebase origin main >> %LOGFILE% 2>&1
    "%GIT%" push >> %LOGFILE% 2>&1
    echo PUSHED: %DATESTR% >> %LOGFILE%
) else (
    echo NO CHANGE >> %LOGFILE%
)

echo. >> %LOGFILE%
