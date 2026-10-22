# Complementary review experiment

Run from the v2 repository root:

```sh
python3 review_pipeline/run.py --dry-run --limit 2
python3 review_pipeline/run.py --limit 2 --provider PROVIDER --model MODEL
```

Omit `--limit` to process all paper folders. The model must be configured in pi;
`--agent-dir` selects its configuration directory. The default reuses the sibling
legacy `pi-agent/agent` configuration and installed SDK. `YDC_API_KEY` must be set
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
