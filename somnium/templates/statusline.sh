#!/bin/bash
# Somnium status line for Claude Code.
# Installed by `somnium status --install-line`.
# Receives JSON session data from Claude Code on stdin.
# Reads Somnium injection state from ~/.claude/somnium/state/prompt_context.json.
#
# Output format (single line):
#   Opus 4.6 (1M context) | $0.15 | Session: 23% (1h04m) | Weekly: 10% (3d04h) | Ctx: [ ▓▓▓▓░░░░░░░░ ] - 3 skills & 5 mem - 14.5k

set -euo pipefail

# Colors (ANSI)
RESET='\033[0m'
BOLD='\033[1m'
DIM='\033[2m'
CYAN='\033[36m'
GREEN='\033[32m'
YELLOW='\033[33m'
RED='\033[31m'
MAGENTA='\033[35m'
WHITE='\033[37m'
BLUE='\033[34m'

# Read Claude Code JSON from stdin
INPUT=$(cat)

# Check jq
if ! command -v jq &>/dev/null; then
    echo -e "${RED}jq not found${RESET} — install it for the Somnium status line"
    exit 0
fi

# --- Extract data (individual calls to handle special chars in values) ---
jq_get() { echo "$INPUT" | jq -r "$1" 2>/dev/null; }

MODEL_NAME=$(jq_get '.model.display_name // "?"')
CTX_PCT=$(jq_get '.context_window.used_percentage // 0 | floor')
CTX_MAX=$(jq_get '.context_window.context_window_size // 200000')
CTX_INPUT=$(jq_get '.context_window.current_usage.input_tokens // 0')
CTX_OUTPUT=$(jq_get '.context_window.current_usage.output_tokens // 0')
CTX_CACHE_READ=$(jq_get '.context_window.current_usage.cache_read_input_tokens // 0')
CTX_CACHE_CREATE=$(jq_get '.context_window.current_usage.cache_creation_input_tokens // 0')
COST=$(jq_get '.cost.total_cost_usd // 0')
RATE_5H_PCT=$(jq_get '.rate_limits.five_hour.used_percentage // -1 | floor')
RATE_5H_RESET=$(jq_get '.rate_limits.five_hour.resets_at // 0')
RATE_7D_PCT=$(jq_get '.rate_limits.seven_day.used_percentage // -1 | floor')
RATE_7D_RESET=$(jq_get '.rate_limits.seven_day.resets_at // 0')

# --- Model display (include context size for large models) ---
CTX_MAX_K=$(( CTX_MAX / 1000 ))
if [ "$CTX_MAX_K" -ge 1000 ]; then
    CTX_SIZE_LABEL="$(( CTX_MAX_K / 1000 ))M context"
else
    CTX_SIZE_LABEL="${CTX_MAX_K}k context"
fi
MODEL_PART="${CYAN}${BOLD}${MODEL_NAME}${RESET} ${DIM}(${CTX_SIZE_LABEL})${RESET}"

# --- Cost ---
if (( $(echo "$COST < 0.01" | bc -l 2>/dev/null || echo 0) )); then
    COST_FMT="\$0.00"
else
    COST_FMT=$(printf '$%.2f' "$COST")
fi

# --- Format duration as Xh Ym ---
format_duration() {
    local ms=$1
    local total_s=$(( ms / 1000 ))
    local h=$(( total_s / 3600 ))
    local m=$(( (total_s % 3600) / 60 ))
    if [ "$h" -gt 0 ]; then
        printf "%dh%02dm" "$h" "$m"
    else
        printf "%dm" "$m"
    fi
}

# --- Format time until reset as Xd Xh or Xh Xm ---
format_reset() {
    local reset_at=$1
    local now
    now=$(date +%s)
    local remaining=$(( reset_at - now ))
    if [ "$remaining" -le 0 ]; then
        echo "now"
        return
    fi
    local d=$(( remaining / 86400 ))
    local h=$(( (remaining % 86400) / 3600 ))
    local m=$(( (remaining % 3600) / 60 ))
    if [ "$d" -gt 0 ]; then
        printf "%dd%02dh" "$d" "$h"
    else
        printf "%dh%02dm" "$h" "$m"
    fi
}

# --- Rate limit color ---
rate_color() {
    local pct=$1
    if [ "$pct" -gt 80 ]; then
        echo "$RED"
    elif [ "$pct" -gt 50 ]; then
        echo "$YELLOW"
    else
        echo "$GREEN"
    fi
}

# --- Session rate (5h) ---
SESSION_PART=""
if [ "$RATE_5H_PCT" -ge 0 ]; then
    RESET_FMT=$(format_reset "$RATE_5H_RESET")
    COLOR=$(rate_color "$RATE_5H_PCT")
    SESSION_PART="${DIM}Session:${RESET} ${COLOR}${RATE_5H_PCT}%${RESET} ${DIM}(${RESET_FMT})${RESET}"
fi

# --- Weekly rate (7d) ---
WEEKLY_PART=""
if [ "$RATE_7D_PCT" -ge 0 ]; then
    RESET_FMT=$(format_reset "$RATE_7D_RESET")
    COLOR=$(rate_color "$RATE_7D_PCT")
    WEEKLY_PART="${DIM}Weekly:${RESET} ${COLOR}${RATE_7D_PCT}%${RESET} ${DIM}(${RESET_FMT})${RESET}"
fi

# --- Context bar (12 chars wide, using ▓ and ░) ---
BAR_WIDTH=12
FILLED=$(( CTX_PCT * BAR_WIDTH / 100 ))
[ "$FILLED" -gt "$BAR_WIDTH" ] && FILLED=$BAR_WIDTH
EMPTY=$(( BAR_WIDTH - FILLED ))

if [ "$CTX_PCT" -lt 50 ]; then
    BAR_COLOR="$GREEN"
elif [ "$CTX_PCT" -lt 80 ]; then
    BAR_COLOR="$YELLOW"
else
    BAR_COLOR="$RED"
fi

BAR="[ ${BAR_COLOR}"
for ((i=0; i<FILLED; i++)); do BAR+="▓"; done
for ((i=0; i<EMPTY; i++)); do BAR+="░"; done
BAR+="${RESET} ]"

# --- Current context tokens in k ---
CURRENT_TOKENS=$(( CTX_INPUT + CTX_OUTPUT + CTX_CACHE_READ + CTX_CACHE_CREATE ))
if [ "$CURRENT_TOKENS" -gt 1000 ]; then
    TOKENS_FMT=$(echo "scale=1; $CURRENT_TOKENS / 1000" | bc 2>/dev/null || echo "${CURRENT_TOKENS}")
    TOKENS_FMT="${TOKENS_FMT}k"
else
    TOKENS_FMT="${CURRENT_TOKENS}"
fi

# --- Somnium injection state ---
SOMNIUM_PART=""
STATE_FILE="$HOME/.claude/somnium/state/prompt_context.json"
if [ -f "$STATE_FILE" ]; then
    S_HITS=$(jq -r '.n_hits // 0' "$STATE_FILE" 2>/dev/null || echo 0)
    S_SKILLS=$(jq -r '.n_skills // 0' "$STATE_FILE" 2>/dev/null || echo 0)
    PARTS=""
    if [ "$S_SKILLS" -gt 0 ] 2>/dev/null; then
        PARTS="${S_SKILLS} skills"
    fi
    if [ "$S_HITS" -gt 0 ] 2>/dev/null; then
        [ -n "$PARTS" ] && PARTS+=" & "
        PARTS+="${S_HITS} mem"
    fi
    if [ -n "$PARTS" ]; then
        SOMNIUM_PART="${MAGENTA}${PARTS}${RESET}"
    fi
fi

# --- Assemble the line ---
# Format: Model (Xk context) | $cost | Session: X% (Xh) | Weekly: X% (Xd) | Ctx: [ ▓▓░░ ] - X skills & Y mem - 14.5k
LINE="${MODEL_PART}"
LINE+=" ${DIM}|${RESET} ${WHITE}${COST_FMT}${RESET}"

if [ -n "$SESSION_PART" ]; then
    LINE+=" ${DIM}|${RESET} ${SESSION_PART}"
fi
if [ -n "$WEEKLY_PART" ]; then
    LINE+=" ${DIM}|${RESET} ${WEEKLY_PART}"
fi

LINE+=" ${DIM}|${RESET} ${DIM}Ctx:${RESET} ${BAR}"
if [ -n "$SOMNIUM_PART" ]; then
    LINE+=" ${DIM}-${RESET} ${SOMNIUM_PART}"
fi
LINE+=" ${DIM}-${RESET} ${DIM}${TOKENS_FMT}${RESET}"

echo -e "$LINE"
