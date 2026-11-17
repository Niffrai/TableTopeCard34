#!/usr/bin/env bash
set -e

# Переход в директорию скрипта
cd "$(dirname "$0")"

echo "========================================================"
echo "  Tabletop Simulator - Custom Deck Builder (Linux)"
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

# Если есть .venv, используем его
if [ -f ".venv/bin/python" ]; then
    PYTHON_CMD=".venv/bin/python"
fi

# Проверка наличия Pillow
if ! $PYTHON_CMD -c "import PIL" &> /dev/null; then
    echo "[ИНФО] Необходимые библиотеки еще не установлены."
    echo "Запуск скрипта установки зависимостей..."
    echo ""
    if [ -f "./install_dependencies.sh" ]; then
        chmod +x ./install_dependencies.sh
        ./install_dependencies.sh
        if [ -f ".venv/bin/python" ]; then
            PYTHON_CMD=".venv/bin/python"
        fi
    else
        echo "[ОШИБКА] install_dependencies.sh не найден!"
        exit 1
    fi
fi

echo "Запуск локального веб-сервера..."
$PYTHON_CMD app.py
