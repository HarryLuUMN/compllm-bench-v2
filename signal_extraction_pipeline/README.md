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

## Incremental review labels (no clustering)

`run_incremental.py` implements a separate, sequential route: start with an empty
shared label space; for each complete review, let DeepSeek select existing labels
and create labels for uncovered major problems. Save that review's combined labels
and add only its new labels to the space. Earlier reviews are never relabeled.
AI and human reviews share one space. Each summary document is one review, not one
review per bullet. Mixed review histories remain one document where upstream data
has not separated reviewers. Labels describe reported concerns, not verified flaws.

```sh
python3 signal_extraction_pipeline/run_incremental.py \
  --input discritized_summary/results/deepseek_discretization_20261103 \
  --output signal_extraction_pipeline/results/incremental --dry-run
```

Remove `--dry-run` to invoke the API. Alternatively input a JSON array of records
with `review_id` and `text` strings, plus optional source/paper metadata. JSON array
order is preserved; summary directories use sorted paper IDs and metadata document
order. `.env` supplies `DEEPSEEK_API_KEY`; use `--env` to override. Configuration is
`quest_computes/incremental_review_labels.json`, with an English YAML prompt at
`prompts/incremental_review_labels.yaml`. No embedding or tokenizer service is needed.

`state.json` is the authoritative atomic checkpoint, containing the accumulated
space and each processed review's labels. Restarting skips the processed prefix;
new reviews can be appended. Changing that prefix, prompt or configuration requires
another output directory. A single-process lock protects the space. Invalid model
output stops at the current review without adding labels; validated earlier work
is retained. The full space and complete review are always sent, with an explicit
input-byte guard; overflow fails instead of truncating or dropping existing labels.

On completion, `label_space.json` and `labels_by_review.json` are exported. Check
that `state.json` says `completed` before consuming exports. API attempts save full
requests, responses and provider token usage without credentials. Interrupted API
calls may need to be repeated if their checkpoint was not written. Duplicate labels are rejected after case/space normalization. Reuse versus new
is determined by actual membership in the current space, even if the model puts a
label in the wrong array; existing spellings are preserved and corrections are
flagged in the checkpoint. Raw responses retain the original grouping; semantic synonym avoidance and
coverage rely on the LLM prompt, so review order can affect the resulting domain.
This route does not automatically start experiments or backfill old reviews.

Offline tests: `python3 -m unittest signal_extraction_pipeline.test_incremental`.

## Incremental topic-label tree

Add `--topic-tree` to the incremental entry point to grow a two-level tree from
empty, without changing the flat-label mode:

```sh
python3 signal_extraction_pipeline/run_incremental.py \
  --input /path/to/completed/summary/run --topic-tree --dry-run
```

This command only validates inputs. Remove `--dry-run` to make API calls in a later
experiment. Tree mode uses a separate default output directory,
`signal_extraction_pipeline/results/incremental_topic_tree`.

Tree mode uses at most two LLM calls per review:
1. Select existing labels and generate missing labels. The model sees the entire
   topic-label tree once, followed by the full review. There is no duplicate flat
   `available_labels` list; all existing labels are read from the tree's leaves.
2. In one response, assign new labels to existing topics first, then create topics
   for the remaining labels. This request contains only the full tree and the new
   labels, not the review or its metadata. With an empty tree, all labels go to new
   topics; with no new labels, the second call is skipped.

Both calls start with exactly the same common system message and a dedicated user
message containing the complete tree, serialized consistently. Stage instructions
and changing review/label data appear only after that shared prefix. This makes
unchanged taxonomy prefixes eligible for provider caching across reviews and
stages, rather than placing changing review text or different task instructions
before the tree. Tree updates can reduce matching prefix length; actual cache hits
are not guaranteed. Provider usage, including cache-hit/miss fields, is saved.

No retrieval, pruning or silent truncation is used. The input-byte guard covers
all messages in each stage, including the whole tree. An oversized context stops
processing. Topic placement now relies on label wording without the original
review; semantic grouping quality and caching savings need a later live pilot.

Each label has exactly one parent topic. Reused labels keep that parent. Validators
reject unknown topic/label names, duplicate placement, missing labels, empty topics
and existing topic names presented as new. A label cannot appear both in an existing
assignment and a new topic. Topic names may differ only by case or spacing when
selecting an existing topic; their stored spelling is preserved. Semantic equivalence
of topic names still depends on the LLM.

Label discovery and combined placement are staged in memory and committed together
per review. Invalid placement leaves the earlier tree and reviews intact. The
single-process lock, source-order checks, raw request/response records and token
logging also apply. Resuming skips completed reviews; it may repeat the unfinished
review's calls. Optimized tree mode uses checkpoint format 4, rejecting the previous
three-stage tree checkpoints. Use a new output directory; old pilot results are
not migrated, overwritten or rerun automatically. Flat mode retains its existing
request layout and checkpoint behavior.

`state.json` adds `topic_label_tree`. Each review records its selected `topic_labels`,
new-label `topic_assignments` into existing topics, and any `new_topics` it created.
On completion, `topic_label_tree.json` and `topics_by_review.json` are exported,
alongside the original flat label exports. Only consume exports when the checkpoint
says `completed`.

English YAML prompts in `prompts/` are now:
- `topic_tree_context.yaml`: common prefix and complete taxonomy presentation;
  override with `--tree-context-prompt`.
- `tree_review_labels.yaml`: default tree-mode label generation prompt;
  override with `--prompt`. Flat mode still defaults to `incremental_review_labels.yaml`.
- `place_label_topics.yaml`: combined existing/new topic placement;
  override with `--place-topics-prompt`.

The former `--assign-topics-prompt` and `--create-topics-prompt` flags and separate
stage templates have been replaced by the combined placement prompt. Compute
settings remain in `quest_computes/incremental_review_labels.json`.
