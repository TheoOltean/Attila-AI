#!/bin/bash
# In-place telemetry dashboard: repaints the snapshot files written by
# src/campaign/camera.lua and src/battle/telemetry.lua without
# scrolling — line positions stay frozen, only the numbers change.
#   bash tools/watch_battle.sh
# Ctrl-C to quit.

DATA="/mnt/c/Program Files (x86)/Steam/steamapps/common/Total War Attila/data"
CAMPAIGN="$DATA/attila_ai_campaign_state.txt"
BATTLE="$DATA/attila_ai_battle_state.txt"

trap 'printf "\033[?25h\n"; exit' INT TERM
printf '\033[2J\033[?25l'    # clear screen once, hide cursor

while true; do
    printf '\033[H'          # cursor home — repaint over previous frame
    shown=0
    for f in "$CAMPAIGN" "$BATTLE"; do
        if [ -f "$f" ]; then
            # \033[K clears each line's stale tail
            awk '{ sub(/\r$/, ""); printf "%s\033[K\n", $0 }' "$f"
            printf '\033[K\n'
            shown=1
        fi
    done
    if [ "$shown" = 0 ]; then
        printf 'waiting for snapshots (load a campaign / start a battle)...\033[K\n'
    fi
    printf '\033[J'          # clear everything below the frame
    sleep 0.2
done
