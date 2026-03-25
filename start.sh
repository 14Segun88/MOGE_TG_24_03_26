#!/bin/bash
# ─────────────────────────────────────────────
#  DocumentAnalyzer — Быстрый старт
#  Запуск: ./start.sh
#  Тест 4 шагов: ./start.sh --test   (включает детальный мониторинг)
# ─────────────────────────────────────────────
cd "$(dirname "$0")"

# Читаем порт из .env (или дефолт 8001)
WEB_PORT=$(grep -oP '^WEB_PORT=\K.*' .env 2>/dev/null || echo "8001")

TEST_MODE=false
BOT_LOG="logs/bot_$(date +%Y%m%d_%H%M%S).log"
mkdir -p logs

[[ "$1" == "--test" ]] && TEST_MODE=true

echo "🔍 Проверяем Weaviate (Docker)..."
if docker ps --filter "name=moexp_weaviate" --filter "status=running" | grep -q weaviate; then
    echo "✅ Weaviate уже запущен"
else
    echo "⏳ Запускаем Weaviate..."
    docker start moexp_weaviate 2>/dev/null || echo "⚠️  Weaviate не найден — проверь: docker ps -a"
fi

echo ""
echo "🤖 Проверяем LM Studio..."
if curl -s --max-time 2 http://172.31.128.1:1234/v1/models > /dev/null 2>&1; then
    echo "✅ LM Studio доступен (hybrid RAG)"
else
    echo "⚠️  LM Studio не запущен — RAG будет работать в режиме BM25 (без векторного поиска)"
    echo "   Для полного RAG: запусти LM Studio на Windows и загрузи модель nomic-embed-text"
fi

echo ""

# ── Test Monitor (запускается если --test или всегда для отслеживания) ──
MONITOR_PID=""
COMPARE_PID=""
chmod +x test_monitor.sh 2>/dev/null
if [ -f "test_monitor.sh" ]; then
    if $TEST_MODE; then
        echo "🔎 Режим тестирования: мониторинг 4 сценариев активен"
        echo "   Лог ошибок: logs/test_issues_*.log"
        echo "   Итог — при остановке (Ctrl+C)"

        # Проверяем наличие эталонного заключения эксперта
        if [ -f "reference/expert_conclusion.json" ]; then
            echo "📋 Эталон найден → запускаю автосравнение бот vs эксперт"
            .venv/bin/python tools/compare_with_expert.py --watch 2>&1 | \
                tee -a "$BOT_LOG" &
            COMPARE_PID=$!
        elif ls reference/*.pdf 2>/dev/null | grep -q pdf; then
            echo "📋 Найден PDF заключения → подготавливаю эталон..."
            .venv/bin/python tools/parse_conclusion.py 2>&1
            if [ -f "reference/expert_conclusion.json" ]; then
                echo "✅ Эталон готов → запускаю автосравнение"
                .venv/bin/python tools/compare_with_expert.py --watch 2>&1 | \
                    tee -a "$BOT_LOG" &
                COMPARE_PID=$!
            fi
        else
            echo "💡 Для сравнения с экспертом: положи PDF заключения в reference/"
            echo "   Затем: python tools/parse_conclusion.py reference/<файл>.pdf"
        fi
    else
        echo "💡 Совет: запусти с --test для мониторинга: ./start.sh --test"
    fi
    BOT_LOG_FILE="$BOT_LOG" bash test_monitor.sh &
    MONITOR_PID=$!
fi

echo ""
echo "🚀 Запускаем веб-интерфейс..."
echo "   Откройте: http://localhost:$WEB_PORT"
echo "   Debug-режим: /debug в чате"
echo "   Остановить: Ctrl+C"
echo "   Лог: $BOT_LOG"
echo "─────────────────────────────────────────────"

# Корректное завершение: останавливаем монитор при Ctrl+C
cleanup() {
    echo ""
    echo "🛑 Останавливаем сервер..."
    [ -n "$MONITOR_PID" ]  && kill "$MONITOR_PID"  2>/dev/null && wait "$MONITOR_PID"  2>/dev/null
    [ -n "$COMPARE_PID" ] && kill "$COMPARE_PID" 2>/dev/null && wait "$COMPARE_PID" 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

# Web-сервер пишет в terminal И в лог-файл через tee
.venv/bin/python web_app.py 2>&1 | tee "$BOT_LOG"

# Если сервер упал сам — тоже чистимся
cleanup
