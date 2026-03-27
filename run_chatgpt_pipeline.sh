#!/usr/bin/env bash
set -euo pipefail

cd ~/ai-archive

echo "== PRECHECK: project =="
uv run ai-archive --help >/dev/null

echo "== PRECHECK: CDP =="
curl -fsS http://127.0.0.1:9222/json/version | python3 -m json.tool >/tmp/ai_archive_cdp_version.json
curl -fsS http://127.0.0.1:9222/json/list >/tmp/ai_archive_cdp_list.json
python3 - <<'PY'
import json, sys
from pathlib import Path
items = json.loads(Path("/tmp/ai_archive_cdp_list.json").read_text())
ok = any(
    item.get("type") == "page" and "chatgpt.com" in item.get("url", "")
    for item in items
)
if not ok:
    print("ChatGPT tab not found in CDP")
    sys.exit(3)
print("ChatGPT tab found in CDP")
PY

echo "== CRAWL =="
uv run ai-archive crawl --provider chatgpt "${@}"

echo "== NORMALIZE =="
uv run ai-archive normalize

echo "== CLUSTER =="
uv run ai-archive cluster

echo "== CURATE =="
uv run ai-archive curate

echo "== REPORT =="
uv run ai-archive report

echo "== ARTIFACTS =="
find data/raw -type f | sort | tail -n 20
echo "---"
find data/normalized -type f | sort | tail -n 20
echo "---"
find data/curated -type f | sort | tail -n 20
