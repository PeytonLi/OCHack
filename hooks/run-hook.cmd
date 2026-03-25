: << 'CMDEOF'
@echo off
setlocal
set "PLUGIN_ROOT=%~dp0.."
set "HOOK_NAME=%~1"
if "%HOOK_NAME%"=="" (
    echo {"error": "No hook name provided"} >&2
    exit /b 1
)
bash "%PLUGIN_ROOT%/hooks/%HOOK_NAME%" %*
exit /b %ERRORLEVEL%
CMDEOF
#!/usr/bin/env bash
# Polyglot: CMD runs the top block, bash skips to here.
PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_NAME="${1:?No hook name provided}"
shift
exec bash "$PLUGIN_ROOT/hooks/$HOOK_NAME" "$@"
