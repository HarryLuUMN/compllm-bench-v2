#!/usr/bin/env bash
#SBATCH --job-name=deepseek-topic-labels
#SBATCH --account=p32963
#SBATCH --partition=normal
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=48:00:00
#SBATCH --signal=B:USR1@180
#SBATCH --requeue
set -euo pipefail
repo=/gpfs/projects/p32963/harry_compl_work/compllm-bench-v2
cd "$repo"
runtime="$repo/quest_computes/pi_runtime/topic_labels_ai_human_full_20261118"
mkdir -p "$runtime"
exec 9>"$runtime/job.lock"
flock -n 9 || exit 0
worker=''
stop_worker() {
  if [[ -n "$worker" ]]; then
    kill -TERM "$worker" 2>/dev/null || true
    wait "$worker" || true
  fi
}
resume_later() {
  trap '' USR1
  stop_worker
  flock -u 9
  if (( ${SLURM_RESTART_COUNT:-0} < 2 )); then
    if scontrol requeue "$SLURM_JOB_ID"; then exit 0; fi
  fi
  echo 'Automatic requeue unavailable or restart limit reached; checkpoint retained' >&2
  exit 75
}
trap resume_later USR1
trap 'stop_worker; exit 143' TERM INT
"$repo/quest_computes/pi_runtime/python/bin/python" -u \
  "$runtime/code_snapshot/quest_computes/topic_label_batch.py" \
  --repo "$repo" --runtime "$runtime" &
worker=$!
wait "$worker"
