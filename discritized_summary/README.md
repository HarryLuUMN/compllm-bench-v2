# Discritized summary

DeepSeek summarizes AI final responses and human reviewer comments separately into
high-level English takeaways that group related comments, as plain-text bullet lists. No JSON or source quotes are requested from the model.
No thinking or intermediate AI trace is summarized. BMC author responses and eLife
author responses/editor assessments are excluded before the API call. PLOS/BMJ
histories contain mixed roles; the prompt asks the model to exclude non-reviewer
material. Attribution in these mixed histories still needs human quality review.
Unknown reviewer identities remain `unknown` rather than being invented.

```bash
python3 discritized_summary/run.py \
  --input /path/to/one/completed/review/run --paper paper_0001 --dry-run
# Remove --dry-run to call DeepSeek; no pilot is run automatically.
```

Uses existing PyYAML and standard-library HTTP; no new dependency. The API key is
read from `DEEPSEEK_API_KEY` in root `.env` (existing process environment wins).
Settings are in `quest_computes/discritized_summary.json`; prompts live in
`prompts/discritized_summary.yaml`. Current default is DeepSeek Flash, non-thinking.
Pass `--paper` repeatedly to restrict a run; omit it to process the selected run.
Use `--output` to keep experiments separate. Local results may not contain the
final Quest experiment: supply its actual results directory, do not select an older run.

Each paper has separate `.txt` summaries for the AI output and each human review
document, plus `metadata.json`, `status.json`, and cached per-chunk request/response
JSON with provider usage. Chunks cover every source character without truncation;
16,000-character boundaries can split sentences, so boundary points may be omitted.
Bullet formatting is checked; faithfulness and reviewer attribution remain
model judgments. There is no cross-chunk or cross-reviewer semantic merge.
Incomplete/invalid outputs fail the paper, never pass as a summary. Validated chunks
are reused by input/prompt/config fingerprint on rerun; failed attempts are retained.
`status.json` is authoritative; ignore previous text files if the current status failed.
Usage is stored once per unique successful chunk, including reused chunks; failed
attempt usage remains in `.attempt.json` files. A rerun does not imply new token use.
Run a single process per output directory. Results are git-ignored and contain source
text, so handle them as research data. This pipeline does not launch full-dataset work.
