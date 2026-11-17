@echo off
chcp 65001 > nul
title Tabletop Simulator Deck Builder
cd /d "%~dp0"

echo ========================================================
echo   Tabletop Simulator - Custom Deck Builder
echo ========================================================
echo.

:: 1. Проверка наличия Python
python --version > nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Python не найден на вашем компьютере!
    echo Скачайте и установите Python: https://www.python.org/downloads/
    echo При установке обязательно отметьте галочку: "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

:: 2. Автоматическая проверка и доустановка Pillow при первом запуске
python -c "import PIL" > nul 2>&1
if errorlevel 1 (
    echo [ИНФО] Необходимые библиотеки еще не установлены.
    echo Запуск автоматической установки зависимостей...
    echo.
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ОШИБКА] Не удалось установить библиотеки автоматически.
        echo Попробуйте запустить install_dependencies.bat вручную.
        pause
        exit /b 1
    )
    echo.
    echo [УСПЕХ] Зависимости установлены!
    echo.
)

:: 3. Запуск веб-сервера
echo Запуск локального веб-сервера...
python app.py

pause
