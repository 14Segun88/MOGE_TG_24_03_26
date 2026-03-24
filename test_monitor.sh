#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  TEST MONITOR v2 — Мониторинг 4 тестовых сценариев
#
#  Сценарии (соответствуют реальным ZIP-архивам):
#  [С1] Сырая подача        — "Документация предоставленная"
#  [С2] Сравнение с экспертом — "Заключение.pdf" vs ответ бота
#  [С3] Тест исправления    — "Документация откорректированная"
#  [С4] Тест идеала         — "Документация окончательная"
#
#  Запуск: BOT_LOG_FILE=logs/bot.log bash test_monitor.sh
#  Или автоматически через: ./start.sh --test
# ═══════════════════════════════════════════════════════════════════

BOT_LOG="${BOT_LOG_FILE:-logs/bot.log}"
MONITOR_LOG="logs/test_issues_$(date +%Y%m%d_%H%M%S).log"
SUMMARY_LOG="logs/test_summary_latest.log"

mkdir -p logs

# ── Цвета ──────────────────────────────────────────────────────────
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Состояние текущего сценария ────────────────────────────────────
CURRENT_SCENARIO=""    # С1/С2/С3/С4
CURRENT_DOC=""         # имя текущего файла/архива

declare -A SCENARIO_ERRORS     # количество ошибок per сценарий
declare -A SCENARIO_WARNINGS   # предупреждений per сценарий
declare -A SCENARIO_VERDICT    # финальный вердикт бота per сценарий
declare -A SCENARIO_STATUS     # NOT_STARTED / IN_PROGRESS / DONE / FAILED

SCENARIO_STATUS["С1"]="NOT_STARTED"
SCENARIO_STATUS["С2"]="NOT_STARTED"
SCENARIO_STATUS["С3"]="NOT_STARTED"
SCENARIO_STATUS["С4"]="NOT_STARTED"
SCENARIO_ERRORS["С1"]=0; SCENARIO_ERRORS["С2"]=0
SCENARIO_ERRORS["С3"]=0; SCENARIO_ERRORS["С4"]=0
SCENARIO_WARNINGS["С1"]=0; SCENARIO_WARNINGS["С2"]=0
SCENARIO_WARNINGS["С3"]=0; SCENARIO_WARNINGS["С4"]=0

# ── Лог-функции ────────────────────────────────────────────────────
log_issue() {
    local sc="$1"   # С1/С2/С3
    local level="$2"
    local msg="$3"
    local ts; ts=$(date '+%H:%M:%S')

    echo "[$ts] [$sc] [$level] $msg" >> "$MONITOR_LOG"

    case "$level" in
        ERROR)
            echo -e "${RED}[$ts] ⛔ $sc: $msg${RESET}"
            SCENARIO_ERRORS["$sc"]=$(( ${SCENARIO_ERRORS["$sc"]:-0} + 1 ))
            ;;
        WARNING)
            echo -e "${YELLOW}[$ts] ⚠️  $sc: $msg${RESET}"
            SCENARIO_WARNINGS["$sc"]=$(( ${SCENARIO_WARNINGS["$sc"]:-0} + 1 ))
            ;;
        INFO)
            echo -e "${GREEN}[$ts] ✅ $sc: $msg${RESET}"
            ;;
        EXPERT)
            echo -e "${BLUE}[$ts] 👨‍⚖️ $sc: $msg${RESET}"
            ;;
    esac
}

# ── Определяем сценарий по имени файла ────────────────────────────
# Бот пишет в лог имя файла/архива при начале обработки
detect_scenario() {
    local line="$1"
    local fname=""

    # Пытаемся извлечь имя файла из строки лога
    fname=$(echo "$line" | grep -oiP "(?:файл|file|архив|doc).*?['\"]?([^ '\"]+\.zip|[^ '\"]+\.pdf)['\"]?" | head -1)

    if echo "$fname$line" | grep -qiP "предоставл|исходн|сырой|initial|provided"; then
        CURRENT_SCENARIO="С1"
    elif echo "$fname$line" | grep -qiP "заключени.*pdf|conclusion|эксперт.*pdf|pdf.*exprt"; then
        CURRENT_SCENARIO="С2"
    elif echo "$fname$line" | grep -qiP "откоррект|correct|исправл|corrected"; then
        CURRENT_SCENARIO="С3"
    elif echo "$fname$line" | grep -qiP "окончател|final|идеал|готов"; then
        CURRENT_SCENARIO="С4"
    fi

    # Если сценарий определён — обновляем статус
    if [ -n "$CURRENT_SCENARIO" ] && [ "${SCENARIO_STATUS[$CURRENT_SCENARIO]}" = "NOT_STARTED" ]; then
        SCENARIO_STATUS["$CURRENT_SCENARIO"]="IN_PROGRESS"
        local -A names=( ["С1"]="Сырая подача" ["С2"]="Сравнение с экспертом" ["С3"]="Тест исправления" ["С4"]="Тест идеала" )
        echo -e "\n${BOLD}${CYAN}═══ Начало сценария $CURRENT_SCENARIO: ${names[$CURRENT_SCENARIO]} ═══${RESET}"
        echo "[$( date '+%H:%M:%S')] Начало $CURRENT_SCENARIO" >> "$MONITOR_LOG"
    fi
}

# ── Ключевые паттерны для отслеживания kostyakov ──────────────────

check_line() {
    local line="$1"

    # 1. Определяем сценарий по новому файлу
    if echo "$line" | grep -qiP "Начинаем обработку|Получен файл|pipeline.*start|analyze_package|processing.*zip|processing.*pdf"; then
        detect_scenario "$line"
        CURRENT_DOC=$(echo "$line" | grep -oiP "['\"][^'\"]+\.(zip|pdf)['\"]" | head -1 | tr -d "'\"")
        [ -n "$CURRENT_DOC" ] && log_issue "${CURRENT_SCENARIO:-??}" "INFO" "Документ: $CURRENT_DOC"
        return
    fi

    # 2. Фиксируем финальный вердикт бота
    if echo "$line" | grep -qiP "verdict.*APPROVED|СООТВЕТСТВУЕТ|вердикт.*принято"; then
        SCENARIO_VERDICT["${CURRENT_SCENARIO:-??}"]="APPROVED ✅"
        log_issue "${CURRENT_SCENARIO:-??}" "INFO" "Вердикт бота: ПРИНЯТО (APPROVED)"
        SCENARIO_STATUS["${CURRENT_SCENARIO:-??}"]="DONE"

    elif echo "$line" | grep -qiP "verdict.*RETURNED|ВОЗВРАТ|ДОРАБОТК"; then
        SCENARIO_VERDICT["${CURRENT_SCENARIO:-??}"]="RETURNED ⚠️"
        log_issue "${CURRENT_SCENARIO:-??}" "INFO" "Вердикт бота: ВОЗВРАТ НА ДОРАБОТКУ"
        SCENARIO_STATUS["${CURRENT_SCENARIO:-??}"]="DONE"

    elif echo "$line" | grep -qiP "verdict.*PENDING|ТРЕБУЕТСЯ ЭКСПЕРТ"; then
        SCENARIO_VERDICT["${CURRENT_SCENARIO:-??}"]="PENDING 🔍"
        log_issue "${CURRENT_SCENARIO:-??}" "INFO" "Вердикт бота: ТРЕБУЕТСЯ ЭКСПЕРТИЗА"
        SCENARIO_STATUS["${CURRENT_SCENARIO:-??}"]="DONE"
    fi

    # 3. Completeness Score — важная метрика
    if echo "$line" | grep -qiP "Completeness Score.*[0-9]"; then
        local score
        score=$(echo "$line" | grep -oP "[0-9]+(?:%| процент)")
        log_issue "${CURRENT_SCENARIO:-??}" "INFO" "Completeness Score = $score"
    fi

    # ── С2: Сравнение с экспертом — особые паттерны ───────────────
    if [ "$CURRENT_SCENARIO" = "С2" ]; then
        if echo "$line" | grep -qiP "расхождени.*с.*эксперт|expert.*differ|expert.*disagr"; then
            log_issue "С2" "WARNING" "Расхождение с заключением эксперта: $line"
        fi
        if echo "$line" | grep -qiP "совпад.*эксперт|match.*expert|согласу"; then
            log_issue "С2" "INFO" "Совпадение с заключением эксперта"
        fi
    fi

    # ── Технические ошибки (любой сценарий) ──────────────────────
    local sc="${CURRENT_SCENARIO:-??}"

    # Критические
    if echo "$line" | grep -qiP "Traceback|Exception|Error.*pipeline|CRITICAL|критическ.*ошибк"; then
        log_issue "$sc" "ERROR" "$(echo "$line" | head -c 200)"
        SCENARIO_STATUS["$sc"]="FAILED" 2>/dev/null || true
        return
    fi

    # RAG/База знаний
    if echo "$line" | grep -qiP "weaviate.*error|NormSearch.*fail|RAG.*упал|rag.*exception"; then
        log_issue "$sc" "ERROR" "RAG/Weaviate сбой: $(echo "$line" | head -c 150)"
        return
    fi

    # LLM/Groq
    if echo "$line" | grep -qiP "groq.*RateLimit|groq.*429|groq.*APIError|LLM.*timeout"; then
        log_issue "$sc" "ERROR" "LLM/Groq сбой: $(echo "$line" | head -c 150)"
        return
    fi

    # FC ошибки
    if echo "$line" | grep -qiP "FC-[A-Z0-9]+.*critical"; then
        local fc_code
        fc_code=$(echo "$line" | grep -oP "FC-[A-Z0-9]+")
        log_issue "$sc" "WARNING" "Формальная ошибка $fc_code: $(echo "$line" | head -c 120)"
        return
    fi

    # ТУ/ГПЗУ расхождения
    if echo "$line" | grep -qiP "ТУ.*ИОС.*расхождени|\[ТУ↔ИОС\]|ГПЗУ.*ПЗ.*расхождени|\[ГПЗУ↔ПЗ\]"; then
        log_issue "$sc" "WARNING" "Расхождение ИРД: $line"
        return
    fi

    # НОПРИЗ
    if echo "$line" | grep -qiP "НОПРИЗ.*not_found|NOPRIZ.*not_found|ГИП.*не найден"; then
        log_issue "$sc" "WARNING" "ГИП не найден в реестре НОПРИЗ"
        return
    fi

    # Бот упал или завис
    if echo "$line" | grep -qiP "bot.*crash|restart|Telegram.*disconnect|unhealthy"; then
        log_issue "$sc" "ERROR" "Бот завис или упал: $line"
        return
    fi
}

# ── Финальная сводка ──────────────────────────────────────────────
print_summary() {
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════${RESET}"
    echo -e "${BOLD}   📊 ИТОГИ ТЕСТИРОВАНИЯ — 4 СЦЕНАРИЯ${RESET}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════${RESET}"

    declare -A SC_NAMES=(
        ["С1"]="Сырая подача          "
        ["С2"]="Сравнение с экспертом "
        ["С3"]="Тест исправления      "
        ["С4"]="Тест идеала           "
    )

    for sc in С1 С2 С3 С4; do
        local status="${SCENARIO_STATUS[$sc]}"
        local errs="${SCENARIO_ERRORS[$sc]:-0}"
        local warns="${SCENARIO_WARNINGS[$sc]:-0}"
        local verdict="${SCENARIO_VERDICT[$sc]:-—}"
        local name="${SC_NAMES[$sc]}"

        local status_icon
        case "$status" in
            NOT_STARTED) status_icon="⬜ Не запускался" ;;
            IN_PROGRESS) status_icon="${YELLOW}🔄 В процессе${RESET}" ;;
            DONE)        status_icon="${GREEN}✅ Завершён${RESET}" ;;
            FAILED)      status_icon="${RED}❌ Упал${RESET}" ;;
        esac

        local err_color=$GREEN; [ "$errs" -gt 0 ] && err_color=$RED
        local warn_color=$GREEN; [ "$warns" -gt 0 ] && warn_color=$YELLOW

        echo ""
        echo -e "  ${BOLD}[$sc] $name${RESET}"
        echo -e "       Статус:  $status_icon"
        echo -e "       Ошибок:  ${err_color}${errs}${RESET}   Предупреждений: ${warn_color}${warns}${RESET}"
        echo -e "       Вердикт бота: $verdict"
    done

    echo ""
    echo -e "  📋 Детальный лог: ${CYAN}${MONITOR_LOG}${RESET}"

    # Что фиксить
    local total_err=0
    for sc in С1 С2 С3 С4; do
        total_err=$(( total_err + ${SCENARIO_ERRORS[$sc]:-0} ))
    done

    if [ "$total_err" -gt 0 ]; then
        echo ""
        echo -e "${YELLOW}💡 Есть ошибки — смотри детали:${RESET}"
        echo -e "   ${CYAN}grep '\\[ERROR\\]' $MONITOR_LOG${RESET}"
        echo -e "   ${CYAN}grep '\\[С1\\]\\|\\[С2\\]\\|\\[С3\\]\\|\\[С4\\]' $MONITOR_LOG${RESET}"
    else
        echo -e "${GREEN}🎉 Критических ошибок не обнаружено во всех сценариях!${RESET}"
    fi

    # Сохраняем сводку
    {
        echo "=== ТЕСТ 4 СЦЕНАРИЕВ $(date '+%Y-%m-%d %H:%M') ==="
        for sc in С1 С2 С3 С4; do
            echo "$sc | статус=${SCENARIO_STATUS[$sc]} | err=${SCENARIO_ERRORS[$sc]:-0} | warn=${SCENARIO_WARNINGS[$sc]:-0} | вердикт=${SCENARIO_VERDICT[$sc]:-—}"
        done
        echo "лог=$MONITOR_LOG"
    } > "$SUMMARY_LOG"

    echo -e "${BOLD}═══════════════════════════════════════════════════════${RESET}"
}

# ── Старт ────────────────────────────────────────────────────────
echo -e "${CYAN}${BOLD}"
echo "  ┌─────────────────────────────────────────────┐"
echo "  │  TEST MONITOR v2 — 4 тестовых сценария     │"
echo "  │  С1: Сырая подача                           │"
echo "  │  С2: Сравнение с заключением эксперта       │"
echo "  │  С3: Тест исправления                       │"
echo "  │  С4: Тест идеала (окончательный архив)      │"
echo "  └─────────────────────────────────────────────┘"
echo -e "${RESET}"
echo -e "  Лог событий: ${CYAN}$MONITOR_LOG${RESET}"
echo -e "  Слежу за:    ${CYAN}$BOT_LOG${RESET}"
echo ""
echo "$(date '+%Y-%m-%d %H:%M:%S') TEST MONITOR STARTED" >> "$MONITOR_LOG"

trap 'print_summary; exit 0' SIGTERM SIGINT

# Ждём появления лог-файла если бот ещё не запустился
while [ ! -f "$BOT_LOG" ]; do
    sleep 1
done

tail -n 0 -f "$BOT_LOG" 2>/dev/null | while IFS= read -r line; do
    check_line "$line"
done
