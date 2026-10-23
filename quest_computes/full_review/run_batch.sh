#!/usr/bin/env bash
set -euo pipefail
: "${SLURM_JOB_ID:?Run inside a Slurm allocation}"
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$repo_root"
export PATH="$repo_root/quest_computes/pi_runtime/node-v22.22.0-linux-x64/bin:$PATH"
export PYTHON="$repo_root/quest_computes/pi_runtime/python/bin/python"
export PI_SDK_PATH="$repo_root/quest_computes/pi_runtime/node_modules/@earendil-works/pi-coding-agent/dist/index.js"
source quest_computes/pi_runtime/search.env
run_id=${REVIEW_RUN_ID:-quest_qwen38_27b_full_20261023}
runtime="$repo_root/quest_computes/pi_runtime/$run_id"
mkdir -p "$runtime"
exec 9>"$runtime/batch.lock"
flock 9
"$PYTHON" - "$runtime" <<'PY'
import json
import sys
from pathlib import Path
for shard in range(8):
    directory = Path(sys.argv[1]) / f'agent_{shard}'
    directory.mkdir(exist_ok=True)
    model = {'id': 'qwen38-27b-review', 'name': 'Qwen3.8-27B BF16', 'reasoning': True,
             'input': ['text'], 'contextWindow': 131072, 'maxTokens': 32768,
             'thinkingLevelMap': {'high': 'xhigh'},
             'compat': {'supportsDeveloperRole': False, 'supportsReasoningEffort': False,
                        'thinkingFormat': 'chat-template',
                        'requiresReasoningContentOnAssistantMessages': True,
                        'chatTemplateKwargs': {'enable_thinking': {'$var': 'thinking.enabled'},
                                               'preserve_thinking': True,
                                               'reasoning_effort': {'$var': 'thinking.effort'}}},
             'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}}
    config = {'providers': {'quest-review': {'baseUrl': f'http://127.0.0.1:{24200 + shard % 4}/v1',
                                           'api': 'openai-completions', 'apiKey': 'local-review',
                                           'models': [model]}}}
    (directory / 'models.json').write_text(json.dumps(config, indent=2) + '\n')
PY
pids=()
for shard in 0 1 2 3 4 5 6 7; do
  "$PYTHON" review_pipeline/run.py --database paper_database \
    --provider quest-review --model qwen38-27b-review --thinking high \
    --agent-dir "$runtime/agent_$shard" --timeout 3600 \
    --shard-count 8 --shard-index "$shard" --require-full-pdf --require-thinking \
    --prompt prompts/full_paper_review.yaml \
    --trace-root "trace_databases/paper_review/$run_id" \
    --output "review_pipeline/results/$run_id/shard_$shard" "$@" \
    > "$runtime/batch_$shard.log" 2>&1 &
  pids+=("$!")
done
failed=0
for pid in "${pids[@]}"; do
  wait "$pid" || failed=1
done
"$PYTHON" review_pipeline/estimate_usage.py \
  --results "review_pipeline/results/$run_id" \
  --traces "trace_databases/paper_review/$run_id" \
  --output "review_pipeline/results/$run_id/token_usage.json" > "$runtime/token_usage.log"
exit "$failed"
