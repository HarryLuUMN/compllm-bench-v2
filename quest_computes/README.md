# Quest compute settings

Statistical modeling settings are in `statistical_modeling.yaml`.

## Two-paper Qwen3.5-9B agent pilot

Run inside a Slurm GPU allocation using one GPU. The recorded pilot uses an
A100 80GB; smaller GPUs may require a shorter context or different memory settings. An example new allocation is:

```sh
srun -A p32963 -p gengpu --gres=gpu:1 --cpus-per-task=4 \
  --mem=40G --time=00:40:00 --pty bash -l
```

`start_qwen9b_pilot.sh` loads the installed CUDA 13 toolkit and serves the cached
Qwen3.5-9B model through the existing vLLM 0.25.1 runtime. It enables thinking,
uses Qwen's tool-call parser, a 65536-token context, a single sequence and eager
execution. The FlashInfer sampler is disabled to avoid unnecessary sampling JIT
compilation during the pilot. The server binds only to localhost port 24181.
Override `VLLM_PYTHON` or `MODEL_PATH` when the installation/cache changes.

The PDF reader and review driver use a separate small Python environment in
`pi_runtime/python`, containing the existing `pypdf` and `PyYAML` dependencies.
Create it with a Python 3.12 interpreter and install those two packages there;
this avoids modifying the shared model-serving environment. `PILOT_PYTHON` can
override its interpreter path.

The agent configuration `pilot_agent/models.json` enables reasoning and allows
8192 output tokens per model call. The dummy `pilot-local` API key is not a secret;
the localhost server has no external authentication requirement.

Node 22.22.0 and the pi SDK are installed in the ignored `pi_runtime` directory.
The GPU nodes do not have the login node's system Node executable; the pilot
runner prepends this local runtime to PATH. Install it from the official Node
release archive when bootstrapping a fresh copy:

```sh
mkdir -p quest_computes/pi_runtime
curl -fsSL https://nodejs.org/dist/v22.22.0/node-v22.22.0-linux-x64.tar.xz \
  -o quest_computes/pi_runtime/node.tar.xz
tar -xJf quest_computes/pi_runtime/node.tar.xz -C quest_computes/pi_runtime
```

The pi SDK is installed in that same ignored runtime directory. To reproduce the
existing harness installation on Quest:

```sh
npm --prefix quest_computes/pi_runtime install --ignore-scripts --no-audit \
  --no-fund @earendil-works/pi-coding-agent@1.0.0
```

Export `YDC_API_KEY` or `YOU_COM_API_KEY` for You.com search. Brave Search can
instead use `BRAVE_SEARCH_API_KEY` if no You.com credential is configured. Keep
credentials out of source control. The pilot runner may load a private
`quest_computes/pi_runtime/search.env`; all of `pi_runtime` is ignored by Git.
The local `.env` is also ignored and is not implicitly loaded by the agent.

From the v2 repository root, after supplying the two paper folders and credentials:

```sh
bash quest_computes/start_qwen9b_pilot.sh > quest_computes/pi_runtime/vllm.log 2>&1 &
pilot_server_pid=$!
trap 'kill "$pilot_server_pid" 2>/dev/null || true' EXIT
bash quest_computes/run_agent_pilot.sh
```

`run_agent_pilot.sh` waits up to ten minutes for readiness, then runs the first two
eligible papers through the review pipeline with `--thinking high` and a ten-minute
per-paper timeout. `PILOT_RUN_ID` selects the result/trace subdirectory; otherwise
a UTC timestamp is used. It writes results beneath `review_pipeline/results/`
and full trajectories beneath `trace_databases/paper_review/`, including returned
thinking/reasoning content, tool calls and final answers. These artifacts remain
local/untracked. Finish by stopping only the server and interactive step created
for this pilot; do not cancel a pre-existing allocation used by other work.

## Verified pilot (2026-10-22)

The thinking-mode pilot completed on `qgpu2011` with one A100 80GB, using an idle
GPU in the user's existing allocation. Both final runs read every PDF page,
performed real You.com searches, and saved returned CoT plus the final response.

| Paper | Pages read | Model calls | Search calls | Thinking characters | Agent seconds |
| --- | --- | --- | --- | --- | --- |
| paper_0001 | 13/13 | 7 | 3 | 10504 | 181.6 |
| paper_0002 | 10/10 | 8 | 5 | 15510 | 242.7 |

The final batch summary was 2 completed, 0 failed. An initial attempt exposed a
missing pypdf dependency and was correctly rejected by the PDF/tool-use gate;
its traces were preserved. The isolated helper environment fixed it. The model
server and pilot step were stopped after the test, leaving the pre-existing
allocation intact. Results and trajectories were copied back to the local v2
workspace and the timestamped Quest-results archive. This is functional pilot
evidence, not a validation of the scientific correctness of proposed omissions.

## Full Qwen3.8-27B review

`full_review/` runs four independent BF16 replicas of official
`Qwen/Qwen3.8-27B`, revision `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`,
one per A100 80GB. Each server uses a 131072-token context, a 32768-token
per-call output budget and Qwen's tool/reasoning parsers. Pi's `high` maps to
Qwen's `xhigh`; thinking is preserved between turns. The existing vLLM runtime
is reused. Sampling follows the official thinking-mode recommendations.

Inside an allocated four-GPU Slurm step, from the v2 repository root:

```sh
bash quest_computes/full_review/launch.sh
```

The launcher waits for all four endpoints, then performs one paper per shard.
All four must pass PDF page coverage, successful search, nonempty thinking and
non-truncated final output checks before the full batch starts. Use
`prompts/full_paper_review.yaml` for these runs. Numeric dataset indices are
sharded deterministically; the same command resumes completed matching inputs.
One missing PDF (`paper_0699`) is recorded as missing input.

Run `python quest_computes/full_review/wait_dataset.py` alongside the transfer.
It checks all 5023 expected PDF signatures and available source checksums before
writing the dataset readiness marker. The full batch waits for this marker;
the four-paper smoke can run while the remaining inputs transfer.

Default run ID: `quest_qwen38_27b_full_20261023` (`REVIEW_RUN_ID` overrides it).
Results are in `review_pipeline/results/<run>/shard_<0..3>/paper_<index>/`;
`response.txt`, `thinking.txt`, `status.json` and tool logs are saved per paper.
All JSONL trajectories are in `trace_databases/paper_review/<run>/paper_<index>/`.
Server and batch logs are under the ignored `quest_computes/pi_runtime/<run>/`.
The launcher cleans up its own four model servers when the batch ends or the
step exits; the parent allocation remains intact. Failed papers remain marked
failed and can be retried by resuming the batch.
