@echo off
cd /d "C:\Users\murayan\Documents\jpn-x\kabu-stop"

set LOGFILE=scraper_log.txt
set PYTHON=C:\Users\murayan\AppData\Local\Programs\Python\Python312\python.exe
set GIT=C:\Program Files\Git\cmd\git.exe
set PATH=C:\Users\murayan\AppData\Local\Programs\Python\Python312;C:\Users\murayan\AppData\Local\Programs\Python\Python312\Scripts;C:\Program Files\Git\cmd;C:\Program Files\Git\bin;%PATH%

for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set DATESTR=%%d
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format HH:mm:ss"') do set TIMESTR=%%t
echo === %DATESTR% %TIMESTR% === >> %LOGFILE%

rem --- scraper ---
"%PYTHON%" scraper.py >> %LOGFILE% 2>&1
if %ERRORLEVEL% neq 0 (
    echo SKIP: scraper failed >> %LOGFILE%
    echo. >> %LOGFILE%
    exit /b 0
)

rem --- analyzer ---
"%PYTHON%" analyzer.py >> %LOGFILE% 2>&1
if %ERRORLEVEL% neq 0 (
    echo WARN: analyzer failed >> %LOGFILE%
)

rem --- git commit and push ---
"%GIT%" add data/stock_data.json data/gap_data.csv >> %LOGFILE% 2>&1
"%GIT%" diff --staged --quiet
if %ERRORLEVEL% neq 0 (
    "%GIT%" commit -m "auto: update %DATESTR% %TIMESTR%" >> %LOGFILE% 2>&1
    "%GIT%" pull --rebase origin main >> %LOGFILE% 2>&1
    "%GIT%" push >> %LOGFILE% 2>&1
    if %ERRORLEVEL% neq 0 (
        echo ERROR: push failed >> %LOGFILE%
    ) else (
        echo PUSHED: %DATESTR% %TIMESTR% >> %LOGFILE%
    )
) else (
    echo NO CHANGE >> %LOGFILE%
)

echo. >> %LOGFILE%