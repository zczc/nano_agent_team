#!/bin/bash
# Self-Evolution Loop for nano_agent_team
# Usage: bash evolve.sh [max_rounds]

MAX_ROUNDS=${1:-20}
ROUND=1

mkdir -p evolution_reports

echo "╔════════════════════════════════════════╗"
echo "║   nano_agent_team Self-Evolution Loop  ║"
echo "║   Max Rounds: $MAX_ROUNDS                       ║"
echo "╚════════════════════════════════════════╝"

while [ $ROUND -le $MAX_ROUNDS ]; do
    echo ""
    echo "━━━ Evolution Round $ROUND / $MAX_ROUNDS ━━━"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting..."

    # Ensure we start each round from main branch
    CURRENT_BRANCH=$(git branch --show-current)
    if [ "$CURRENT_BRANCH" != "main" ]; then
        echo "[SAFETY] Not on main (on $CURRENT_BRANCH). Switching to main..."
        git checkout main
    fi

    python main.py --evolution
    EXIT_CODE=$?

    # Safety: always return to main after each round
    CURRENT_BRANCH=$(git branch --show-current)
    if [ "$CURRENT_BRANCH" != "main" ]; then
        echo "[SAFETY] Round ended on branch $CURRENT_BRANCH. Returning to main..."
        git checkout main
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
