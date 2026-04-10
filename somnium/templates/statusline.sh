#!/bin/bash
# Somnium status line for Claude Code.
# Installed by `somnium status --install-line`.
# Receives JSON session data from Claude Code on stdin.
# Reads Somnium injection state from ~/.claude/somnium/state/prompt_context.json.

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

# Read Claude Code JSON from stdin
INPUT=$(cat)

# Check jq
if ! command -v jq &>/dev/null; then
    echo -e "${RED}jq not found${RESET} — install it for the Somnium status line"
    exit 0
fi

# --- Extract Claude Code data ---
MODEL=$(echo "$INPUT" | jq -r '.model.display_name // "?"')
CTX_PCT=$(echo "$INPUT" | jq -r '.context_window.used_percentage // 0' | cut -d. -f1)
CTX_MAX=$(echo "$INPUT" | jq -r '.context_window.context_window_size // 200000')
COST=$(echo "$INPUT" | jq -r '.cost.total_cost_usd // 0')
RATE_5H=$(echo "$INPUT" | jq -r '.rate_limits.five_hour.used_percentage // empty' 2>/dev/null)

# --- Context bar (8 chars wide) ---
BAR_WIDTH=8
FILLED=$(( CTX_PCT * BAR_WIDTH / 100 ))
EMPTY=$(( BAR_WIDTH - FILLED ))

if [ "$CTX_PCT" -lt 50 ]; then
    BAR_COLOR="$GREEN"
elif [ "$CTX_PCT" -lt 80 ]; then
    BAR_COLOR="$YELLOW"
else
    BAR_COLOR="$RED"
fi

BAR="${BAR_COLOR}"
for ((i=0; i<FILLED; i++)); do BAR+="█"; done
for ((i=0; i<EMPTY; i++)); do BAR+="░"; done
BAR+="${RESET}"

# --- Cost formatting ---
if (( $(echo "$COST < 0.01" | bc -l 2>/dev/null || echo 1) )); then
    COST_FMT="<\$0.01"
else
    COST_FMT=$(printf '$%.2f' "$COST")
fi

# --- Rate limit ---
RATE_PART=""
if [ -n "$RATE_5H" ]; then
    RATE_INT=$(echo "$RATE_5H" | cut -d. -f1)
    if [ "$RATE_INT" -gt 80 ]; then
        RATE_PART=" ${RED}5h:${RATE_INT}%${RESET}"
    elif [ "$RATE_INT" -gt 50 ]; then
        RATE_PART=" ${YELLOW}5h:${RATE_INT}%${RESET}"
    else
        RATE_PART=" ${DIM}5h:${RATE_INT}%${RESET}"
    fi
fi

# --- Somnium injection state ---
SOMNIUM_PART=""
STATE_FILE="$HOME/.claude/somnium/state/prompt_context.json"
if [ -f "$STATE_FILE" ]; then
    S_HITS=$(jq -r '.n_hits // 0' "$STATE_FILE" 2>/dev/null)
    S_CHARS=$(jq -r '.chars // 0' "$STATE_FILE" 2>/dev/null)
    if [ "$S_HITS" -gt 0 ] 2>/dev/null; then
        # Format chars as k if > 1000
        if [ "$S_CHARS" -gt 1000 ] 2>/dev/null; then
            S_CHARS_FMT=$(echo "scale=1; $S_CHARS / 1000" | bc 2>/dev/null || echo "$S_CHARS")
            S_CHARS_FMT="${S_CHARS_FMT}k"
        else
            S_CHARS_FMT="$S_CHARS"
        fi
        SOMNIUM_PART=" ${MAGENTA}mem:${S_HITS}${RESET}${DIM}(${S_CHARS_FMT})${RESET}"
    fi
fi

# --- Context size in k ---
CTX_MAX_K=$(( CTX_MAX / 1000 ))

# --- Assemble ---
echo -e "${CYAN}${BOLD}${MODEL}${RESET} ${BAR} ${CTX_PCT}%/${CTX_MAX_K}k ${DIM}|${RESET} ${WHITE}${COST_FMT}${RESET}${RATE_PART}${SOMNIUM_PART}"
