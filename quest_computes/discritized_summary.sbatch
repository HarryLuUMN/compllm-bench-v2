#!/usr/bin/env bash
#SBATCH --job-name=deepseek-discretization
#SBATCH --account=p32963
#SBATCH --partition=normal
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=24:00:00
#SBATCH --requeue
set -euo pipefail
cd /gpfs/projects/p32963/harry_compl_work/compllm-bench-v2
run_id=deepseek_discretization_20261103
runtime="quest_computes/pi_runtime/$run_id"
output="discritized_summary/results/$run_id"
mkdir -p "$runtime" "$output"
exec 9>"$runtime/job.lock"
flock -n 9 || exit 0
python=quest_computes/pi_runtime/python/bin/python
"$python" - <<'CHECK'
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from discritized_summary.run import load_env

load_env(Path('quest_computes/pi_runtime/deepseek_discretization_20261103/deepseek.env'))
config = json.loads(Path('quest_computes/discritized_summary.json').read_text())
request = Request(config['base_url'].rstrip('/') + '/models',
                  headers={'Authorization': 'Bearer ' + os.environ['DEEPSEEK_API_KEY']})
with urlopen(request, timeout=30) as response:
    models = json.load(response)['data']
assert any(model['id'] == config['model'] for model in models), 'Configured model unavailable'
print('DEEPSEEK_MODEL_ACCESS_VERIFIED', flush=True)
CHECK
pids=()
cleanup() {
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 143' TERM INT
for shard in 0 1 2 3; do
  "$python" -u discritized_summary/run.py \
    --input review_pipeline/results/quest_qwen38_27b_medium_full_20261023 \
    --output "$output" --env "$runtime/deepseek.env" \
    --shard-count 4 --shard-index "$shard" \
    >"$runtime/shard_$shard.log" 2>&1 &
  pids+=("$!")
done
failed=0
for pid in "${pids[@]}"; do wait "$pid" || failed=1; done
exit "$failed"
