@echo off
chcp 65001 > nul
title Tabletop Simulator Deck Builder
cd /d "%~dp0"

echo ========================================================
echo   Tabletop Simulator - Custom Deck Builder
echo ========================================================
echo.

python --version > nul 2>&1
if errorlevel 1 goto NO_PYTHON

python -c "import PIL" > nul 2>&1
if errorlevel 1 goto INSTALL_DEPS

:START_APP
echo Запуск локального веб-сервера...
python app.py
goto END

:INSTALL_DEPS
echo [ИНФО] Необходимые библиотеки еще не установлены.
echo Запуск автоматической установки зависимостей...
echo.
python -m pip install -r requirements.txt
if errorlevel 1 goto INSTALL_ERROR
echo.
echo [УСПЕХ] Зависимости установлены!
echo.
goto START_APP

:NO_PYTHON
echo [ОШИБКА] Python не найден на вашем компьютере!
echo Скачайте и установите Python: https://www.python.org/downloads/
echo При установке обязательно отметьте галочку: Add python.exe to PATH
echo.
pause
exit /b 1

:INSTALL_ERROR
echo.
echo [ОШИБКА] Не удалось установить библиотеки автоматически.
echo Попробуйте запустить install_dependencies.bat вручную.
echo.
pause
exit /b 1

:END
pause
