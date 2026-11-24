@echo off
title MultiStream Bot - Servidor Local
echo ===================================================
echo     INICIANDO MULTISTREAM BOT (KICK, TWITCH, YT, TIKTOK)
echo ===================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] No se detecto Python instalado en el sistema.
    echo Descarga e instala Python desde: https://www.python.org/downloads/
    echo Recuerda marcar la casilla "Add Python to PATH" durante la instalacion.
    pause
    exit /b
)

python app.py
pause
