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
