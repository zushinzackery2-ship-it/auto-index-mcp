@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set "PROJECT_ROOT=%CD%"
set "VENV_DIR=%PROJECT_ROOT%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "CONFIG_FILE=%PROJECT_ROOT%\mcp-client-config.windows.json"
set "MODEL_DIR=%PROJECT_ROOT%\models\minilm-onnx"
set "RESULT_FILE=%PROJECT_ROOT%\install_result.txt"
set "LOG_FILE=%PROJECT_ROOT%\install_windows.log"
set "PYTHON_CMD="

if exist "%RESULT_FILE%" del /q "%RESULT_FILE%" >nul 2>nul
if exist "%LOG_FILE%" del /q "%LOG_FILE%" >nul 2>nul

echo auto-index-mcp Windows installer
>> "%LOG_FILE%" echo %DATE% %TIME% auto-index-mcp Windows installer
echo Project root: %PROJECT_ROOT%
>> "%LOG_FILE%" echo %DATE% %TIME% Project root: %PROJECT_ROOT%

if not exist "%PROJECT_ROOT%\pyproject.toml" (
    set "FAIL_REASON=pyproject.toml was not found. Run this script from the auto-index-mcp directory."
    goto fail
)

if not exist "%MODEL_DIR%\model.onnx" (
    set "FAIL_REASON=Bundled embedding model was not found: %MODEL_DIR%\model.onnx"
    goto fail
)

if not exist "%MODEL_DIR%\tokenizer.json" (
    set "FAIL_REASON=Bundled embedding tokenizer was not found: %MODEL_DIR%\tokenizer.json"
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

echo Using Python command: %PYTHON_CMD%
>> "%LOG_FILE%" echo %DATE% %TIME% Using Python command: %PYTHON_CMD%

if not exist "%VENV_PY%" (
    echo Creating virtual environment: %VENV_DIR%
    >> "%LOG_FILE%" echo %DATE% %TIME% Creating virtual environment: %VENV_DIR%
    %PYTHON_CMD% -m venv "%VENV_DIR%" >> "%LOG_FILE%" 2>&1
    if errorlevel 1 (
        set "FAIL_REASON=Failed to create virtual environment."
        goto fail
    )
) else (
    echo Using existing virtual environment: %VENV_DIR%
    >> "%LOG_FILE%" echo %DATE% %TIME% Using existing virtual environment: %VENV_DIR%
)

echo Upgrading pip, setuptools, and wheel.
>> "%LOG_FILE%" echo %DATE% %TIME% Upgrading pip, setuptools, and wheel.
"%VENV_PY%" -m pip install --upgrade pip setuptools wheel >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    set "FAIL_REASON=Failed to upgrade installer packages."
    goto fail
)

echo Installing auto-index-mcp with semantic dependencies into the virtual environment.
>> "%LOG_FILE%" echo %DATE% %TIME% Installing auto-index-mcp with semantic dependencies into the virtual environment.
"%VENV_PY%" -m pip install -e "%PROJECT_ROOT%[semantic]" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    set "FAIL_REASON=Failed to install auto-index-mcp."
    goto fail
)

echo Configuring AUTO_INDEX_EMBEDDING_MODEL user environment variable.
>> "%LOG_FILE%" echo %DATE% %TIME% Configuring AUTO_INDEX_EMBEDDING_MODEL=%MODEL_DIR%
set "AUTO_INDEX_EMBEDDING_MODEL=%MODEL_DIR%"
setx AUTO_INDEX_EMBEDDING_MODEL "%MODEL_DIR%" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    set "FAIL_REASON=Failed to set AUTO_INDEX_EMBEDDING_MODEL user environment variable."
    goto fail
)

echo Verifying embedding model environment.
>> "%LOG_FILE%" echo %DATE% %TIME% Verifying embedding model environment.
"%VENV_PY%" -c "import os, sys; from auto_index_mcp.embedding.backend import resolve_embedding_model_path; path = resolve_embedding_model_path(); raise SystemExit(0 if path and str(path) == os.environ.get('AUTO_INDEX_EMBEDDING_MODEL') else 1)" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    set "FAIL_REASON=Embedding model environment verification failed."
    goto fail
)

echo Verifying MCP server entrypoint.
>> "%LOG_FILE%" echo %DATE% %TIME% Verifying MCP server entrypoint.
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
    echo embedding_model=%MODEL_DIR%
    echo config_example=%CONFIG_FILE%
    echo log=%LOG_FILE%
    echo.
    echo MCP client command:
    echo "%VENV_PY%" -m auto_index_mcp.server
) > "%RESULT_FILE%"

echo Install completed.
>> "%LOG_FILE%" echo %DATE% %TIME% Install completed.
echo.
echo auto-index-mcp install completed.
echo Config example: %CONFIG_FILE%
echo Result file: %RESULT_FILE%
echo Log file: %LOG_FILE%
echo.
echo This script updates the user AUTO_INDEX_EMBEDDING_MODEL environment variable.
echo Restart an already-running MCP client so it can inherit the new environment.
echo This script does not modify MCP client settings and does not start a backend service.
goto done

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

:done
exit /b 0
