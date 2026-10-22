# Trace viewer

Run from the repository root:

```sh
python3 trace_ui/server.py
```

Open http://127.0.0.1:8765. Select a JSONL trajectory from `trace_databases`.
The chronological feed shows task inputs, expandable full model contexts, streamed
thinking/output, tool arguments, web search results and the PDF pages actually
returned to the model. Follow polls appended records; auto-scroll is optional.
Completed assistant messages reconcile streamed deltas to avoid duplicated text.
Provider transport events remain in the source JSONL and are omitted from the feed.
No end event means unfinished or interrupted, not necessarily still running.

Use `--root /path/to/trace_databases` or `--port 8766` when needed. The server
binds only to localhost. Trace content stays local; no extra packages are needed.
Treat traces as sensitive experiment data, including prompts and returned thinking.

Tests: `python3 -m unittest discover -s trace_ui -p 'test_*.py'`.

## GitHub Pages

The hosted viewer uses published snapshots. Export only the experiment directory
that you intend to publish:

```sh
python3 trace_ui/export.py \
  --root trace_databases/paper_review/quest_qwen35_9b_thinking_20261022_pilot \
  --output /tmp/trace-site
```

Publish that output at the root of the `gh-pages` branch and configure repository
Settings → Pages → Deploy from a branch → `gh-pages` / `(root)`.
The public site is https://harryluumn.github.io/compllm-bench-v2/.
The export keeps visible events and full model contexts, and omits redundant
provider transport records. Follow is disabled for snapshots. Source JSONL files
remain unchanged. Newly recorded local runs require another export and publish.
