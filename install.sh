#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  МособлГосЭкспертиза — DocumentAnalyzer v2.0
#  Установка проекта на новый ПК (WSL2 / Ubuntu)
#
#  Использование:
#    chmod +x install.sh
#    ./install.sh
# ═══════════════════════════════════════════════════════════════

set -e  # Остановить при первой ошибке

echo "═══════════════════════════════════════════════════════"
echo "  🏗  МособлГосЭкспертиза — Установка проекта"
echo "═══════════════════════════════════════════════════════"
echo ""

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

# ── 1. Системные зависимости ──────────────────────────────────
echo "📦 [1/6] Проверяю системные зависимости..."

NEED_APT=false
for pkg in python3 python3-venv pip docker.io tesseract-ocr poppler-utils; do
    if ! dpkg -s "$pkg" &>/dev/null; then
        echo "   ⚠ Не найден пакет: $pkg"
        NEED_APT=true
    fi
done

if [ "$NEED_APT" = true ]; then
    echo "   ⏳ Устанавливаю недостающие системные пакеты..."
    sudo apt update -qq
    sudo apt install -y python3 python3-venv python3-pip docker.io tesseract-ocr poppler-utils
    echo "   ✅ Системные пакеты установлены"
else
    echo "   ✅ Все системные пакеты на месте"
fi

# ── 2. Python виртуальное окружение ──────────────────────────
echo ""
echo "🐍 [2/6] Настраиваю виртуальное окружение Python..."

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "   ✅ Создано .venv"
else
    echo "   ✅ .venv уже существует"
fi

source .venv/bin/activate

# ── 3. Python-зависимости ────────────────────────────────────
echo ""
echo "📚 [3/6] Устанавливаю Python-зависимости..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "   ✅ Все пакеты из requirements.txt установлены"

# ── 4. Playwright (для проверки НОПРИЗ) ──────────────────────
echo ""
echo "🎭 [4/6] Устанавливаю Playwright + Chromium..."
playwright install chromium 2>/dev/null || {
    echo "   ⚠ Не удалось установить Chromium автоматически."
    echo "   Попробуйте вручную: playwright install chromium"
}
echo "   ✅ Playwright готов"

# ── 5. Файл конфигурации .env ────────────────────────────────
echo ""
echo "🔑 [5/6] Проверяю конфигурацию .env..."

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "   ⚠ Файл .env создан из шаблона .env.example"
    echo ""
    echo "   ╔══════════════════════════════════════════════════╗"
    echo "   ║  ❗ ВАЖНО: Откройте .env и заполните ключи:     ║"
    echo "   ║     • BOT_TOKEN       (от @BotFather)           ║"
    echo "   ║     • GROQ_API_KEY    (от console.groq.com)     ║"
    echo "   ║     • ADMIN_TELEGRAM_ID (ваш Telegram ID)       ║"
    echo "   ╚══════════════════════════════════════════════════╝"
    echo ""
else
    echo "   ✅ .env уже существует"
fi

# ── 6. Docker / Weaviate ─────────────────────────────────────
echo ""
echo "🐳 [6/6] Проверяю Weaviate (Docker)..."

if command -v docker &>/dev/null; then
    if docker ps --filter "name=moexp_weaviate" --filter "status=running" | grep -q weaviate; then
        echo "   ✅ Weaviate уже запущен"
    else
        echo "   ⏳ Запускаю Weaviate из docker-compose.yml..."
        docker compose up -d 2>/dev/null || docker-compose up -d 2>/dev/null || {
            echo "   ⚠ Не удалось запустить Weaviate. Запустите вручную:"
            echo "      docker compose up -d"
        }
    fi
else
    echo "   ⚠ Docker не установлен. Weaviate нужен для RAG-поиска."
    echo "     Установите Docker и выполните: docker compose up -d"
fi

# ── Готово ────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ✅  Установка завершена!"
echo ""
echo "  Следующие шаги:"
echo "    1. Заполните ключи в .env (если ещё не сделали)"
echo "    2. Запустите бота:  ./start.sh"
echo "    3. Для тестов:      ./start.sh --test"
echo ""
echo "  ℹ️  Для полного RAG-поиска запустите LM Studio"
echo "     на Windows с моделью nomic-embed-text"
echo "═══════════════════════════════════════════════════════"
