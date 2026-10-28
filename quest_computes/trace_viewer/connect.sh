#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
run_id=${1:-quest_qwen38_27b_medium_full_20261023}
if [[ ! "$run_id" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo 'Invalid experiment name' >&2
  exit 1
fi
local_port=${TRACE_LOCAL_PORT:-8766}
remote_port=${TRACE_REMOTE_PORT:-18765}
if [[ ! "$local_port" =~ ^[0-9]+$ || ! "$remote_port" =~ ^[0-9]+$ ]]; then
  echo 'Ports must be numeric' >&2
  exit 1
fi
remote_root=/gpfs/projects/p32963/harry_compl_work/compllm-bench-v2
target=zgy2900@quest.northwestern.edu
ssh_options=(-i "$HOME/.ssh/codex_quest_northwestern_ed25519" -o BatchMode=yes
  -o ConnectTimeout=15 -o ControlPath=none -o ServerAliveInterval=30 -o ServerAliveCountMax=3)
ssh "${ssh_options[@]}" "$target" "mkdir -p '$remote_root/trace_ui'"
scp "${ssh_options[@]}" "$repo_root"/trace_ui/{server.py,index.html,app.js,style.css} "$target:$remote_root/trace_ui/"
echo "Quest trace viewer: http://127.0.0.1:$local_port · $run_id"
exec ssh -tt "${ssh_options[@]}" -o ExitOnForwardFailure=yes \
  -L "127.0.0.1:$local_port:127.0.0.1:$remote_port" "$target" \
  "exec '$remote_root/quest_computes/pi_runtime/python/bin/python' '$remote_root/trace_ui/server.py' --root '$remote_root/trace_databases/paper_review/$run_id' --port '$remote_port'"
