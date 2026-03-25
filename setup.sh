#!/bin/bash
# Автоматическая настройка DocumentAnalyzer на новом устройстве

echo "🚀 Начинаем настройку DocumentAnalyzer..."

if [ ! -d ".venv" ]; then
    echo "📦 Создаем виртуальное окружение..."
    python3 -m venv .venv
fi

echo "🔌 Активация окружения и установка зависимостей..."
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Установка системных зависимостей (если есть sudo)
if command -v apt-get &> /dev/null; then
    echo "🛠 Проверка системных зависимостей (tesseract, poppler)..."
    sudo apt-get update && sudo apt-get install -y tesseract-ocr tesseract-ocr-rus poppler-utils
fi

echo "✅ Настройка завершена!"
echo "Для запуска веб-интерфейса выполните: source .venv/bin/activate && python web_app.py"
