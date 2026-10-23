#!/usr/bin/env bash
set -euo pipefail
: "${SLURM_JOB_ID:?Run inside the four-GPU allocation}"
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$repo_root"
export REVIEW_RUN_ID=${REVIEW_RUN_ID:-quest_qwen38_27b_full_20261023}
runtime="$repo_root/quest_computes/pi_runtime/$REVIEW_RUN_ID"
mkdir -p "$runtime"
server_pids=()
cleanup() {
  for pid in "${server_pids[@]}"; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 143' TERM INT
IFS=, read -r -a devices <<< "$CUDA_VISIBLE_DEVICES"
if [[ ${#devices[@]} -ne 4 ]]; then echo 'Exactly four assigned GPUs are required' >&2; exit 1; fi
for shard in 0 1 2 3; do
  CUDA_VISIBLE_DEVICES="${devices[$shard]}" SERVER_PORT=$((24200 + shard)) \
    bash quest_computes/full_review/start_server.sh > "$runtime/server_$shard.log" 2>&1 &
  server_pids+=("$!")
done
quest_computes/pi_runtime/python/bin/python - <<'PY'
import json
import time
import urllib.request
for attempt in range(360):
    ready = []
    for port in range(24200, 24204):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/v1/models', timeout=3) as response:
                models = json.load(response)['data']
                ready.append(any(m['id'] == 'qwen38-27b-review' for m in models))
        except (OSError, ValueError):
            ready.append(False)
    if all(ready):
        print('ALL_FOUR_MODELS_READY', flush=True)
        break
    if attempt % 12 == 0:
        print(f'Waiting for servers: {ready}', flush=True)
    time.sleep(5)
else:
    raise SystemExit('Servers did not become ready within 30 minutes')
PY
echo 'EIGHT_PAPER_SMOKE_START'
bash quest_computes/full_review/run_batch.sh --limit 1
echo 'EIGHT_PAPER_SMOKE_PASSED'
while [[ ! -f "$runtime/dataset_ready.json" ]]; do
  echo 'Waiting for verified dataset transfer'
  sleep 30
done
echo 'FULL_BATCH_START'
bash quest_computes/full_review/run_batch.sh
echo 'FULL_BATCH_COMPLETE'
