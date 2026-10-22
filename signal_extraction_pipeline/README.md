# Signal extraction pipeline

Input is the audit experiment's `paper_XXXX/response.txt` files. Output is a
shared signal domain and binary signal presence for every response. Responses
with a sibling `status.json` are included only when its status is `completed`;
standalone response files without status metadata are also accepted.

Run from the v2 repository root:

```sh
python3 signal_extraction_pipeline/run.py \
  --input review_pipeline/results \
  --output signal_extraction_pipeline/results \
  --llm-base-url http://localhost:8000/v1 \
  --llm-model YOUR_CHAT_MODEL \
  --embedding-base-url http://localhost:8001/v1 \
  --embedding-model Qwen/Qwen3-Embedding-8B \
  --context-window 32768 \
  --max-output-tokens 4096 \
  --threshold 0.82 \
  --label-signals
```

These URLs are examples, not services started by the pipeline. The chat and
embedding models must already be served. Only existing Python standard-library
and PyYAML functionality is used; no local model packages are installed.
Optional credentials come from `LLM_API_KEY` and `EMBEDDING_API_KEY`.

## Stages

1. Extract exact contiguous quotes with the chat model. The saved structure is
   one array per response: `{ "paper_0001": ["quote1", "quote2"] }`. Paraphrases,
   non-array results, blank quotes, and truncated LLM outputs are rejected.
   Identical quotes within a response are deduplicated; a valid `[]` is preserved.
2. Deduplicate exact quote strings across responses and embed them in batches
   through `/v1/embeddings`. Record every quote's source response IDs. Vectors
   are normalized; empty, zero, nonfinite and inconsistent vectors are rejected.
3. Greedily assign each unique quote to the closest normalized cluster centroid
   meeting `--threshold`, provided the whole prospective cluster fits the chat
   context. Otherwise try another eligible cluster or create a new cluster.
4. Optionally call the chat model once per cluster to obtain a high-level label
   with `--label-signals`. Without it, signal IDs and membership are still output;
   `high_level_label` is null. Labels never change cluster membership.
5. A signal is present in a response when at least one of its claims came from
   that response. Responses with no claims receive an all-zero row.

Prompts live exclusively in `prompts/extract_claims.yaml` and
`prompts/label_signal.yaml`. Override them with `--extract-prompt` and
`--label-prompt`.

## Context budget

The pipeline uses the chat server's matching `/tokenize` endpoint with complete
system/user messages and `add_generation_prompt=true`. The input allowance is
`min(--context-window, server max_model_len) - --max-output-tokens`. The serialized
claims, prompt and chat template are included. Every resulting cluster is checked,
even when optional labeling is disabled, so labeling can be enabled later.

`--tokenize-url` overrides the endpoint inferred from the chat base URL. It must
use the same model and chat template as the completion endpoint. An arbitrary
OpenAI-compatible service without compatible token counting is insufficient.
The pipeline does not silently fall back to character counts or truncate text.
A single oversized claim, oversized audit response, or LLM output cut off by its
output limit fails explicitly; adjust the budget or prompt before rerunning.
The embedding server must also accept the quote lengths without truncation;
configure its input-length policy to reject overflow. Its context is separate
from the merge model's context.

## Files and reruns

- `claims_by_response.json`: required response-to-quote arrays.
- `claims.json`: unique claim IDs, exact quotes and source response IDs.
- `signal_domain.json`: signal IDs, optional labels, claim membership, source
  responses and full input-token counts/budgets.
- `presence.json`: response-to-signal binary mappings.
- `presence.csv`: rows are response IDs; columns are signal IDs.
- `manifest.json`: settings, response hashes, counts and completion status.
- `cache/`: successful extractions, normalized embeddings and labels.

Generated results and caches are ignored by Git. Extraction, embedding and label
caches are keyed by content/configuration. Reruns reuse successful stages and
recompute clusters/matrices. Use a new output directory if a remote model or chat
template changes under an unchanged endpoint/model name. Use separate output
directories for concurrent runs. Only consume final outputs when the manifest
says `completed`; old files may remain after a failed rerun.

Clustering is deterministic for the same input order/vectors/budgets, but greedy
and order-dependent. Cosine similarity indicates related meaning, not logical
equivalence, truth, polarity agreement or scientific validity. A low threshold
may merge distinct concerns. Context limits may split one theme across multiple
signals; labels can repeat and are not identifiers. This implementation does not
construct an N-by-N similarity matrix or automatically download an 8B model.

## Verification

```sh
python3 -m unittest discover -s signal_extraction_pipeline -p 'test_*.py'
```

Tests cover exact quote validation, deduplication, cosine normalization, context
splitting, oversized claims, presence mapping, empty-claim responses, optional
labels, caching, embedding ordering, tokenizer budget handling, and the complete
CLI against local simulated HTTP services. Live LLM extraction and real Qwen3
embedding quality require configured services and actual audit responses.

Official model/interface references:
- https://huggingface.co/Qwen/Qwen3-Embedding-8B
- https://docs.vllm.ai/en/latest/serving/online_serving/index.html
