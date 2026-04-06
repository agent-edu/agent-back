#!/bin/bash
# 네이버 증권 리서치 리포트 크롤링 + ES 적재
# cron 예시: 0 8 * * * /path/to/agent-back/scripts/crawl_research.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIPELINE_DIR="$SCRIPT_DIR/../../pipeline"
LOG_DIR="$SCRIPT_DIR/../logs"
LOG_FILE="$LOG_DIR/crawl_research_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR"

cd "$PIPELINE_DIR"

echo "[$(date)] 크롤링 시작" | tee "$LOG_FILE"

# macOS: date -v-1d, Linux: date -d 'yesterday'
if [[ "$OSTYPE" == "darwin"* ]]; then
    YESTERDAY=$(date -v-1d +%Y-%m-%d)
else
    YESTERDAY=$(date -d 'yesterday' +%Y-%m-%d)
fi

uv run python research_main.py \
    --categories all \
    --since "$YESTERDAY" \
    2>&1 | tee -a "$LOG_FILE"

echo "[$(date)] 크롤링 완료" | tee -a "$LOG_FILE"
