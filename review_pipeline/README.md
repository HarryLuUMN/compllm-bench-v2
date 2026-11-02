# Complementary review experiment

Run from the v2 repository root:

```sh
python3 review_pipeline/run.py --dry-run --limit 2
python3 review_pipeline/run.py --limit 2 --provider PROVIDER --model MODEL
```

Omit `--limit` to process all paper folders. The model must be configured in pi;
`--agent-dir` selects its configuration directory. The default reuses the sibling
legacy `pi-agent/agent` configuration and installed SDK. `YDC_API_KEY` (or `YOU_COM_API_KEY`) or `BRAVE_SEARCH_API_KEY` must be set
for actual experiments. Python needs existing PyYAML and pypdf installations;
Node.js runs the pi harness. `.env` is not automatically loaded.

For each `paper_XXXX`, the pipeline checks `paper.pdf` and
`human_reviews/reviews.json`, embeds the entire human review JSON in the task,
and starts a fresh pi session with access to that folder's PDFs and web search.
The agent compares reviewer comments with paper evidence and searches for
potential omissions. The task and system prompts live in
`prompts/review_experiment.yaml`, overridable with `--prompt`.

The final answer is free-form prose saved to:

```text
review_pipeline/results/paper_0001/response.txt
```

Each result folder also contains the supplied `input.txt`, tool completion trace
`tools.json`, `run.log`, and `status.json`. These experiment artifacts are ignored
by Git. The aggregate `summary.json` reports counts for the current invocation.
`--output` selects a separate result directory for a different experiment.

A paper is marked completed only when the agent exits successfully, returns
nonempty final text, and successfully calls both `read_pdf` and `web_search`.
This checks tool use, not scientific correctness or exhaustive page coverage.
PDF figures and scanned text are not interpreted. Search provides snippets,
not complete linked webpages. Conclusions require scientific review.

Completed responses with matching PDF/review/prompt/agent-code/configuration
fingerprints are skipped on rerun. Failures are retried; missing inputs are
recorded and skipped so the rest of the batch can proceed. `--limit` counts new
attempts, excluding resumed or missing papers. The default timeout is 900 seconds
per paper; override with `--timeout`. Each paper has an independent subprocess
and context. Run only one process per output directory.

Changing a remote model behind the same configured name is not detected by the
fingerprint: use a fresh output directory when changing model deployments.
The pipeline does not start PDF downloads. Papers without PDFs must be populated
with `fetch_pubmed_pdfs.py` before they can run.

Tests (no live model or web credentials needed):

```sh
python3 -m unittest discover -s review_pipeline -p 'test_*.py'
```

## Full agent trajectories

Each agent attempt also saves an independent full JSONL trajectory under
`trace_databases/paper_review/<paper>/`. Its path is stored as `trajectory` in the
paper's `status.json`, including failed attempts once an agent is launched.
`--trace-root` selects a different category/destination. Attempts get fresh files;
previous trajectories are preserved. The compact `tools.json` remains available
for the successful-tool-use gate. See [trajectory details](../trace_databases/README.md).

`--thinking off|low|medium|high` is forwarded to the agent and included in the
resume fingerprint and per-paper status. The Quest pilot enables `high`; its
model-serving and harness settings live under `quest_computes/`.
# Targeted retries

For context overflow, omitted pages, or thinking-only length stops, use
`--staged-evidence --prompt prompts/repair_paper_review.yaml` together with an
explicit `--paper-list`. This repair mode sends the entire review JSON and all
PDF pages through bounded evidence inputs before the audit. It keeps short notes
and compacts context proactively; original evidence, intermediate thinking, actual
PDF tool executions and compaction calls remain in the trajectory. Continuation is
limited to three attempts per stage. Acceptance additionally requires gap-free,
hash-matched review ingestion and every PDF page ingested by the model.

On Quest set `REVIEW_STAGED_EVIDENCE=1`,
`REVIEW_PROMPT=prompts/repair_paper_review.yaml`, `REVIEW_PAPER_LIST` and optionally
`REVIEW_PAPER_TIMEOUT=7200`. Keep `PDF_TEXT_BACKEND=pdftotext` for complex PDFs.
These repair attempts are methodologically distinct from the original single-task
agent run; their prompt, configuration, ingestion and compaction records identify
the difference. Successful original papers remain untouched.

`PDF_TEXT_BACKEND=pdftotext` selects the existing Quest Poppler module for
complex PDFs. Each returned page records its extraction backend. Page numbers,
character limits, truncation flags and the strict full-page audit remain in
effect. This backend is optional and requires an already installed `pdftotext`;
the Quest launcher loads its Poppler and libpng modules without installing packages.

Use `--paper-list retry.json` with a nonempty JSON array such as
`["paper_0017", "paper_0235"]` to restrict processing, including when code or
configuration changes invalidate fingerprints. Unlisted results are untouched.
Quest's batch launcher accepts the same selection through `REVIEW_PAPER_LIST`.

For vector-heavy PDFs that exceed pypdf's content-stream parsing budget, set
`PDF_STREAM_PARSING_BUDGET=8000000` on a targeted retry. This opt-in setting uses
the installed pypdf configuration API (Quest uses 6.21.2), caps the budget at
eight million parsing operations, and retains the PDF tool's timeout and output
limits. These operations are PDF parser work, not LLM tokens. Full-page coverage
and all other experiment acceptance checks remain required. Archive previous
attempt artifacts before retrying; JSONL trajectories remain distinct per attempt.
