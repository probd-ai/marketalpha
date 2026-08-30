@echo off
rem market-hunt dev servers: backend (uvicorn :3001) + frontend (next dev :3000)
rem usage: servers.bat [start|stop|status]   (no arg = start)
setlocal
set "ROOT=%~dp0"

if /i "%~1"=="stop"   goto :stop
if /i "%~1"=="status" goto :status

:start
echo Starting market-hunt backend (:3001) and frontend (:3000)...
start "market-hunt backend"  /min cmd /c "cd /d "%ROOT%" && .venv\Scripts\python.exe -m uvicorn api_server:app --host 127.0.0.1 --port 3001"
start "market-hunt frontend" /min cmd /c "cd /d "%ROOT%frontend" && npm run dev"
echo Backend  : http://127.0.0.1:3001/health
echo Frontend : http://localhost:3000
echo (windows are minimized; use "servers.bat stop" to stop both)
goto :eof

:stop
echo Stopping listeners on :3001 and :3000...
call :killport 3001
call :killport 3000
goto :eof

:status
call :checkport 3001 backend
call :checkport 3000 frontend
goto :eof

:killport
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%~1 .*LISTENING"') do (
    echo   killing PID %%p on port %~1
    taskkill /PID %%p /F >nul 2>&1
)
goto :eof

:checkport
netstat -ano | findstr /r /c:":%~1 .*LISTENING" >nul 2>&1
if errorlevel 1 (echo   %~2 ^(:%~1^) : DOWN) else (echo   %~2 ^(:%~1^) : UP)
goto :eof
