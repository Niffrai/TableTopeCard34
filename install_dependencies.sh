#!/usr/bin/env bash
set -e

# Переход в директорию скрипта
cd "$(dirname "$0")"

echo "========================================================"
echo "  Tabletop Simulator - Custom Deck Builder (Linux)"
echo "  Проверка и установка необходимых зависимостей"
echo "========================================================"
echo ""

# Проверка наличия python3
if ! command -v python3 &> /dev/null; then
    echo "[ОШИБКА] python3 не найден в вашей системе!"
    echo "Установите Python 3 через ваш пакетный менеджер, например:"
    echo "  Ubuntu/Debian: sudo apt update && sudo apt install python3 python3-pip python3-venv"
    echo "  Arch Linux:    sudo pacman -S python python-pip"
    echo "  Fedora:        sudo dnf install python3 python3-pip"
    exit 1
fi

PYTHON_CMD="python3"

# Проверка/создание виртуального окружения (.venv) для совместимости с PEP 668 (Ubuntu 24.04+, Debian 12+)
if [ ! -d ".venv" ]; then
    echo "[1/3] Создание виртуального окружения .venv..."
    if python3 -m venv .venv 2>/dev/null; then
        echo "  [OK] Окружение .venv создано."
    else
        echo "  [ВНИМАНИЕ] python3 -m venv завершился с ошибкой (возможно, не установлен пакет python3-venv)."
        echo "  Попробуем использовать pip напрямую..."
    fi
fi

if [ -f ".venv/bin/python" ]; then
    PYTHON_CMD=".venv/bin/python"
    PIP_CMD=".venv/bin/pip"
else
    PIP_CMD="python3 -m pip"
fi

echo "[2/3] Обновление pip и установка зависимостей из requirements.txt..."
$PIP_CMD install --upgrade pip 2>/dev/null || true

if ! $PIP_CMD install -r requirements.txt; then
    echo "  Повторная попытка с флагом --break-system-packages..."
    if ! $PIP_CMD install -r requirements.txt --break-system-packages; then
        echo "[ОШИБКА] Не удалось установить зависимости."
        echo "Рекомендация: установите пакет python3-venv (sudo apt install python3-venv) и повторите запуск."
        exit 1
    fi
fi

echo ""
echo "[3/3] Проверка работоспособности установленных модулей..."
$PYTHON_CMD -c "import PIL; from PIL import Image; print('[OK] Библиотека Pillow успешно загружена!')"

echo ""
echo "========================================================"
echo "  Все зависимости успешно установлены и готовы к работе!"
echo "  Теперь вы можете запустить программу командой:"
echo "    ./run.sh"
echo "========================================================"
