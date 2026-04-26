#!/usr/bin/env bash
# Truncate Lapwing text logs in-place. Safe to run while the process is alive:
# truncate -s 0 keeps the inode so open file descriptors keep writing.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIRS=("$ROOT/data/logs" "$ROOT/logs")

shopt -s nullglob
total_files=0
for dir in "${LOG_DIRS[@]}"; do
  [[ -d "$dir" ]] || continue
  files=("$dir"/*.log)
  for f in "${files[@]}"; do
    before=$(stat -c%s "$f")
    truncate -s 0 "$f"
    printf 'cleared %s (%s bytes)\n' "$f" "$before"
    total_files=$((total_files + 1))
  done
done

if (( total_files == 0 )); then
  echo "no logs to clean"
fi
