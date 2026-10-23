#!/usr/bin/env bash
set -euo pipefail
: "${SLURM_JOB_ID:?Run inside a Slurm allocation}"
: "${CUDA_VISIBLE_DEVICES:?Slurm must assign GPUs}"
module load cuda/13.0.0-gcc-12.4.0
export CUDA_HOME
CUDA_HOME=$(dirname "$(dirname "$(command -v nvcc)")")
export VLLM_USE_FLASHINFER_SAMPLER=0 OMP_NUM_THREADS=4
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
server_python=${VLLM_PYTHON:-/gpfs/projects/p32963/harry_compl_work/runtimes/qwen35-vllm-0.25.1/bin/python}
server_model=${MODEL_PATH:-/gpfs/projects/p32963/huggingface/hub/models--Qwen--Qwen3.8-27B/snapshots/1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0}
exec "$server_python" -m vllm.entrypoints.openai.api_server \
  --model "$server_model" --served-model-name qwen38-27b-review \
  --host 127.0.0.1 --port "${SERVER_PORT:-24200}" \
  --max-model-len 131072 --max-num-seqs 2 --max-num-batched-tokens 8192 \
  --gpu-memory-utilization 0.90 --language-model-only \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 \
  --default-chat-template-kwargs '{"enable_thinking":true,"preserve_thinking":true,"reasoning_effort":"xhigh"}' \
  --override-generation-config '{"temperature":1.0,"top_p":0.95,"top_k":20,"min_p":0.0,"presence_penalty":0.0,"repetition_penalty":1.0}'
