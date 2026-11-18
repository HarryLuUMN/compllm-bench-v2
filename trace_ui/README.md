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

## Live Quest traces

From the repository root, keep this connection running:

```sh
bash quest_computes/trace_viewer/connect.sh
```

Open http://127.0.0.1:8766. This runs the viewer on Quest and forwards it through
SSH, reading original trajectories on demand without downloading the dataset.
Follow streams appended events from active runs. The default experiment is
`quest_qwen38_27b_medium_full_20261023`; pass another experiment name as the first
argument. Set `TRACE_LOCAL_PORT` or `TRACE_REMOTE_PORT` to use different ports.
Both endpoints bind to localhost. Closing the connection stops the remote viewer;
the review experiment continues independently. Re-run the command to reconnect.
The connection requires the existing Quest SSH key and remote Python runtime.
It copies only the viewer's seven source assets, without modifying any traces.

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

## Review summary browser

Open `/summaries` on the same live viewer (for example
http://127.0.0.1:8767/summaries). Search by paper ID, move between papers, read AI
and human reviewer bullets side by side, or download a paper's plain-text summaries.
The browser reads only completed summaries and displays source text without HTML
interpretation. Full API request records and credentials are not exposed.

The default dataset is `discritized_summary/results/deepseek_discretization_20261103`
under the viewer's repository. Override it with `--summaries-root /path/to/run`.
The Quest connection copies viewer assets only; summary text is fetched on demand
through the same SSH tunnel. Static trace exports keep their existing trace-only UI.

The summary browser also shows each review's topics and labels above its summary.
AI and individual Human review results match by the summary file's review ID.
Pending reviews are shown explicitly; completed reviews without problem labels
are distinguished from reviews still waiting to be processed. Use Refresh labels,
or leave the page visible for a 30-second refresh from the current checkpoint.
The status line reports the saved run status and checkpoint time.

The default label checkpoint is
`signal_extraction_pipeline/results/topic_labels_ai_human_full_20261118/state.json`.
Override it with `--labels-state /path/to/state.json`. The server caches an index
by checkpoint inode, modification time and size, and returns only the selected
paper's saved label records through `/api/labels?paper=paper_0001`; raw prompts,
API records and the full checkpoint are not sent to the browser. Partial runs
remain browsable. Missing label results do not prevent reading summaries.

Each summary card includes an expandable original-source view and text download.
AI originals resolve from summary metadata into review_pipeline/results; Human
originals match the selected source document in paper_database. Original text is
loaded on demand and checked against the summary source SHA256 when available.
Mixed peer-review histories are explicitly marked, including author-response
fallback documents. Originals are plain text and not silently truncated.
