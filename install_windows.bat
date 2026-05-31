@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set "PROJECT_ROOT=%CD%"
set "VENV_DIR=%PROJECT_ROOT%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "CONFIG_FILE=%PROJECT_ROOT%\mcp-client-config.windows.json"
set "RESULT_FILE=%PROJECT_ROOT%\install_result.txt"
set "LOG_FILE=%PROJECT_ROOT%\install_windows.log"
set "PYTHON_CMD="

if exist "%RESULT_FILE%" del /q "%RESULT_FILE%" >nul 2>nul
if exist "%LOG_FILE%" del /q "%LOG_FILE%" >nul 2>nul

call :log "auto-index-mcp Windows installer"
call :log "Project root: %PROJECT_ROOT%"

if not exist "%PROJECT_ROOT%\pyproject.toml" (
    set "FAIL_REASON=pyproject.toml was not found. Run this script from the auto-index-mcp directory."
    goto fail
)

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >> "%LOG_FILE%" 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >> "%LOG_FILE%" 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    set "FAIL_REASON=Python 3.11 or newer was not found on PATH."
    goto fail
)

call :log "Using Python command: %PYTHON_CMD%"

if not exist "%VENV_PY%" (
    call :log "Creating virtual environment: %VENV_DIR%"
    %PYTHON_CMD% -m venv "%VENV_DIR%" >> "%LOG_FILE%" 2>&1
    if errorlevel 1 (
        set "FAIL_REASON=Failed to create virtual environment."
        goto fail
    )
) else (
    call :log "Using existing virtual environment: %VENV_DIR%"
)

call :log "Upgrading pip, setuptools, and wheel."
"%VENV_PY%" -m pip install --upgrade pip setuptools wheel >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    set "FAIL_REASON=Failed to upgrade installer packages."
    goto fail
)

call :log "Installing auto-index-mcp into the virtual environment."
"%VENV_PY%" -m pip install -e "%PROJECT_ROOT%" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    set "FAIL_REASON=Failed to install auto-index-mcp."
    goto fail
)

call :log "Verifying MCP server entrypoint."
"%VENV_PY%" -m auto_index_mcp.server --help >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    set "FAIL_REASON=MCP server entrypoint verification failed."
    goto fail
)

call :write_config

(
    echo status=success
    echo project_root=%PROJECT_ROOT%
    echo python=%VENV_PY%
    echo config_example=%CONFIG_FILE%
    echo log=%LOG_FILE%
    echo.
    echo MCP client command:
    echo "%VENV_PY%" -m auto_index_mcp.server
) > "%RESULT_FILE%"

call :log "Install completed."
echo.
echo auto-index-mcp install completed.
echo Config example: %CONFIG_FILE%
echo Result file: %RESULT_FILE%
echo Log file: %LOG_FILE%
echo.
echo This script does not modify MCP client settings and does not start a backend service.
exit /b 0

:write_config
set "CONFIG_PY=%VENV_PY:\=\\%"
(
    echo {
    echo   "mcpServers": {
    echo     "auto-index": {
    echo       "command": "%CONFIG_PY%",
    echo       "args": [
    echo         "-m",
    echo         "auto_index_mcp.server"
    echo       ]
    echo     }
    echo   }
    echo }
) > "%CONFIG_FILE%"
exit /b 0

:log
echo %~1
>> "%LOG_FILE%" echo %DATE% %TIME% %~1
exit /b 0

:fail
echo.
echo ERROR: %FAIL_REASON%
>> "%LOG_FILE%" echo %DATE% %TIME% ERROR: %FAIL_REASON%
(
    echo status=failed
    echo reason=%FAIL_REASON%
    echo project_root=%PROJECT_ROOT%
    echo log=%LOG_FILE%
) > "%RESULT_FILE%"
exit /b 1
