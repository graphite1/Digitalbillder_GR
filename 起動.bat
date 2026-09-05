@echo off
setlocal
pushd "%~dp0"
if not defined DIGITALBUILDER_DATA_DIR set "DIGITALBUILDER_DATA_DIR=%~dp0data"
set "DIGITALBUILDER_INSTALL_ROOT=%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0launcher.py" %*
) else (
    py -X utf8 "%~dp0launcher.py" %*
)
set "billder_launch_status=%errorlevel%"
if not "%billder_launch_status%"=="0" (
    echo.
    echo Application failed to start. Please check the error above.
    pause
)
popd
exit /b %billder_launch_status%
