#!/usr/bin/env bash
set -euo pipefail
: "${SLURM_JOB_ID:?Run this inside a Quest GPU allocation}"
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
export PATH="$repo_root/quest_computes/pi_runtime/node-v22.22.0-linux-x64/bin:$PATH"
command -v node >/dev/null
export PYTHON=${PILOT_PYTHON:-$repo_root/quest_computes/pi_runtime/python/bin/python}
export PI_SDK_PATH="$repo_root/quest_computes/pi_runtime/node_modules/@earendil-works/pi-coding-agent/dist/index.js"
if [[ -f quest_computes/pi_runtime/search.env ]]; then
  source quest_computes/pi_runtime/search.env
fi
if [[ -z ${YDC_API_KEY:-${YOU_COM_API_KEY:-${BRAVE_SEARCH_API_KEY:-}}} ]]; then
  echo 'A working search API key is required' >&2
  exit 1
fi
"$PYTHON" - <<'PY'
import time
import urllib.request
for attempt in range(120):
    try:
        with urllib.request.urlopen('http://127.0.0.1:24181/v1/models', timeout=3) as response:
            if response.status == 200:
                print('MODEL_READY', flush=True)
                break
    except OSError:
        pass
    time.sleep(5)
else:
    raise SystemExit('Model endpoint did not become ready within 10 minutes')
PY
pilot_run=${PILOT_RUN_ID:-quest_qwen35_9b_$(date -u +%Y%m%dT%H%M%SZ)}
echo "PILOT_RUN_ID=$pilot_run"
"$PYTHON" review_pipeline/run.py \
  --database paper_database --limit 2 --timeout 600 \
  --provider quest-pilot --model qwen35-9b-pilot --thinking high \
  --agent-dir quest_computes/pilot_agent \
  --trace-root "trace_databases/paper_review/$pilot_run" \
  --output "review_pipeline/results/$pilot_run"
