#!/usr/bin/env bash
set -euo pipefail
: "${SLURM_JOB_ID:?Run this inside a Quest GPU allocation}"
: "${CUDA_VISIBLE_DEVICES:?Slurm must assign the pilot GPU}"
module load cuda/13.0.0-gcc-12.4.0
export CUDA_HOME
CUDA_HOME=$(dirname "$(dirname "$(command -v nvcc)")")
export VLLM_USE_FLASHINFER_SAMPLER=0
export OMP_NUM_THREADS=4
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
pilot_python=${VLLM_PYTHON:-/gpfs/projects/p32963/harry_compl_work/runtimes/qwen35-vllm-0.25.1/bin/python}
pilot_model=${MODEL_PATH:-/gpfs/projects/p32963/huggingface/hub/models--Qwen--Qwen3.5-9B/snapshots/c202236235762e1c871ad0ccb60c8ee5ba337b9a}
exec "$pilot_python" -m vllm.entrypoints.openai.api_server \
  --model "$pilot_model" --served-model-name qwen35-9b-pilot \
  --host 127.0.0.1 --port 24181 --max-model-len 65536 \
  --max-num-seqs 1 --gpu-memory-utilization 0.80 --enforce-eager \
  --language-model-only --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3 --default-chat-template-kwargs '{"enable_thinking": true}'
