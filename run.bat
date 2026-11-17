@echo off
chcp 65001 > nul
title Tabletop Simulator Deck Builder
cd /d "%~dp0"

echo ========================================================
echo   Tabletop Simulator - Custom Deck Builder
echo   Запуск локального сервера...
echo ========================================================
echo.

python app.py

pause
