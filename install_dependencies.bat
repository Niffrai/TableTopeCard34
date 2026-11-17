@echo off
chcp 65001 > nul
title Tabletop Simulator Deck Builder - Установка зависимостей
cd /d "%~dp0"

echo ========================================================
echo   Tabletop Simulator - Custom Deck Builder
echo   Проверка и установка необходимых зависимостей
echo ========================================================
echo.

:: 1. Проверка Python
python --version > nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Python не обнаружен в вашей системе!
    echo.
    echo Пожалуйста, установите Python:
    echo 1. Перейдите на https://www.python.org/downloads/
    echo 2. Скачайте последнюю версию Python для Windows.
    echo 3. В установщике ОБЯЗАТЕЛЬНО поставьте галочку:
    echo    "Add python.exe to PATH" (Добавить Python в PATH).
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version') do echo [OK] Python найден: %%i

:: 2. Установка зависимостей через pip
echo.
echo [1/2] Проверка и установка библиотек из requirements.txt...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ОШИБКА] Не удалось установить библиотеки. Проверьте интернет-соединение.
    pause
    exit /b 1
)

:: 3. Проверка Pillow
echo.
echo [2/2] Проверка работоспособности установленных модулей...
python -c "import PIL; from PIL import Image; print('[OK] Библиотека Pillow успешно загружена!')"
if errorlevel 1 (
    echo [ВНИМАНИЕ] Ошибка при импорте Pillow.
    pause
    exit /b 1
)

echo.
echo ========================================================
echo   Все зависимости успешно установлены и готовы к работе!
echo   Теперь вы можете запустить программу через run.bat
echo ========================================================
echo.
pause
