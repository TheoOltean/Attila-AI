#!/bin/bash
# Live, colorized view of the Attila-AI log.
#   bash tools/watch_log.sh        -> follow, starting from the last 40 lines
#   bash tools/watch_log.sh 200    -> follow, starting from the last 200 lines
#   bash tools/watch_log.sh clear  -> truncate the log, then follow
#
# Colors: campaign=green  battle=red  frontend=cyan
#         section headers bold, ERROR/FAILED lines bright red

LOG="/mnt/c/Program Files (x86)/Steam/steamapps/common/Total War Attila/data/attila_ai_log.txt"

LINES=40
if [ "$1" = "clear" ]; then
    : > "$LOG"
elif [ -n "$1" ]; then
    LINES="$1"
fi

# ---disable-inotify: inotify does not fire for writes made by Windows
# programs on /mnt/c, so force polling
tail -n "$LINES" -F ---disable-inotify "$LOG" | awk '
{
    sub(/\r$/, "")
    if ($0 ~ /ERROR|FAILED/)            { print "\033[1;91m" $0 "\033[0m"; next }
    b = ($0 ~ /====/) ? "1;" : ""
    if      ($0 ~ /^\[campaign\]/)      { print "\033[" b "32m" $0 "\033[0m" }
    else if ($0 ~ /^\[battle\+\]/)      { print "\033[" b "35m" $0 "\033[0m" }
    else if ($0 ~ /^\[battle\]/)        { print "\033[" b "31m" $0 "\033[0m" }
    else if ($0 ~ /^\[frontend\]/)      { print "\033[" b "36m" $0 "\033[0m" }
    else                                { print }
}'
