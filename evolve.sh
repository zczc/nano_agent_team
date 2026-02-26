#!/bin/bash
# Self-Evolution Loop for nano_agent_team
# Usage: bash evolve.sh [max_rounds]

MAX_ROUNDS=${1:-20}
ROUND=1

# Resolve python: prefer .venv/bin/python, fall back to python3
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
else
    echo "Error: cannot find python. Tried .venv/bin/python and python3."
    exit 1
fi

# Track the branch we started on (stay on it between rounds)
START_BRANCH=$(git branch --show-current)

mkdir -p evolution_reports

echo "╔════════════════════════════════════════╗"
echo "║   nano_agent_team Self-Evolution Loop  ║"
echo "║   Max Rounds: $MAX_ROUNDS                       ║"
echo "╚════════════════════════════════════════╝"

while [ $ROUND -le $MAX_ROUNDS ]; do
    echo ""
    echo "━━━ Evolution Round $ROUND / $MAX_ROUNDS ━━━"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting..."

    # Ensure we start each round from the starting branch
    CURRENT_BRANCH=$(git branch --show-current)
    if [ "$CURRENT_BRANCH" != "$START_BRANCH" ]; then
        echo "[SAFETY] Not on $START_BRANCH (on $CURRENT_BRANCH). Switching back..."
        git checkout "$START_BRANCH"
    fi

    "$PYTHON" main.py --evolution
    EXIT_CODE=$?

    # Safety: always return to starting branch after each round
    CURRENT_BRANCH=$(git branch --show-current)
    if [ "$CURRENT_BRANCH" != "$START_BRANCH" ]; then
        echo "[SAFETY] Round ended on branch $CURRENT_BRANCH. Returning to $START_BRANCH..."
        git checkout "$START_BRANCH"
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Round $ROUND finished (exit: $EXIT_CODE)"

    # List evolution branches
    echo "[BRANCHES] Evolution branches:"
    git branch --list 'evolution/*'

    # Stop signal check
    if [ -f ".evolution_stop" ]; then
        echo "[STOP] Stop signal detected. Cleaning up."
        rm -f .evolution_stop
        break
    fi

    ROUND=$((ROUND + 1))
    sleep 5  # cooldown between rounds
done

echo ""
echo "════════════════════════════════════════"
echo "Evolution complete. $((ROUND - 1)) rounds executed."
echo "Reports: ls evolution_reports/"
echo "State:   cat evolution_state.json"
echo "════════════════════════════════════════"
