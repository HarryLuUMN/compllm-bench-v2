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
