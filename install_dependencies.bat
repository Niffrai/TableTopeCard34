@echo off
chcp 65001 > nul
title Tabletop Simulator Deck Builder - Установка зависимостей
cd /d "%~dp0"

echo ========================================================
echo   Tabletop Simulator - Custom Deck Builder
echo   Проверка и установка необходимых зависимостей
echo ========================================================
echo.

python --version > nul 2>&1
if errorlevel 1 goto NO_PYTHON

echo [1/2] Проверка и установка библиотек из requirements.txt...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 goto INSTALL_ERROR

echo.
echo [2/2] Проверка работоспособности установленных модулей...
python -c "import PIL, numpy; from PIL import Image; print('[OK] Библиотеки Pillow и NumPy успешно загружены!')"
if errorlevel 1 goto IMPORT_ERROR

echo.
echo ========================================================
echo   Все зависимости успешно установлены и готовы к работе!
echo   Теперь вы можете запустить программу через run.bat
echo ========================================================
echo.
pause
goto END

:NO_PYTHON
echo [ОШИБКА] Python не обнаружен в вашей системе!
echo.
echo Пожалуйста, установите Python:
echo 1. Перейдите на https://www.python.org/downloads/
echo 2. Скачайте последнюю версию Python для Windows.
echo 3. В установщике ОБЯЗАТЕЛЬНО поставьте галочку:
echo    Add python.exe to PATH
echo.
pause
exit /b 1

:INSTALL_ERROR
echo.
echo [ОШИБКА] Не удалось установить библиотеки. Проверьте интернет-соединение.
pause
exit /b 1

:IMPORT_ERROR
echo.
echo [ВНИМАНИЕ] Ошибка при импорте Pillow.
pause
exit /b 1

:END
