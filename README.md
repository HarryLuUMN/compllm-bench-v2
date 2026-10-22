# Human-AI Complementarity Benchmark

Run from the repository root:

```sh
python3 build_paper_database.py --dataset ../data_5000
```

Each nonblank dataset record is expected to be one JSON line. The 1-based line
number determines the directory name (`paper_0001`, etc.). The source dataset
currently contains 5024 records.

Each paper contains `human_reviews/reviews.json` (the complete `official_review`
object) and `metadata.json` with its index, DOI, title, source URLs, and PDF status.
The database-level `manifest.json` maps all folders back to their source records.

A locally available PDF referenced by `full_text.raw_path` is copied as
`paper.pdf`. Review PDFs are never used as paper PDFs. This dataset currently
stores article bodies as HTML, so paper PDFs remain missing; this script does
not download them or create placeholder PDFs.

The output must be empty to prevent overwriting an existing database. Generated
data is excluded from Git. The script uses only Python's standard library and
has no dependency on the old codebase.

## Fetch paper PDFs via PubMed Central

After building the database:

```sh
python3 fetch_pubmed_pdfs.py --limit 3
```

Omit `--limit` to attempt the entire database. Add `--email` to identify your
maintainer contact to NCBI. Existing PDFs with a PDF signature are skipped;
unavailable and failed papers are retried on subsequent runs. Reviews are preserved.

The downloader matches DOIs using the official PMC ID Converter, lists available
versions in the public PMC Cloud Service, and uses each version's `pdf_url`.
Published versions are preferred over author manuscripts; within either category
the lowest available version number is chosen, since version numbers do not
indicate recency. Version, license, PMCID, PMID, URL and checksum are recorded in
`metadata.json`. PDF signatures and supplied MD5 checksums are checked before
atomic replacement. Requests are paced and transient failures retried.

PubMed is an index; PDFs come from PMC's downloadable article dataset. Some indexed
papers have no downloadable PDF. These receive `unavailable`; request errors and
invalid downloads receive `failed` with the reason. The manifest records statuses
and counts, including partial runs. No paywall or HTML scraping is used.

Official interfaces:
- https://pmc.ncbi.nlm.nih.gov/tools/id-converter-api/
- https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/
- https://pmc-oa-opendata.s3.amazonaws.com/README.txt


## Review and signal experiments

Run commands from this v2 repository root. The two experiment stages are:

- `review_pipeline/`: compare local paper PDFs with human reviews using the pi
  agent and web search; save free-form `response.txt` per paper. See
  [review pipeline instructions](review_pipeline/README.md).
- `signal_extraction_pipeline/`: extract exact claim quotes from audit responses,
  embed and cluster them with cosine similarity under a tokenizer-checked context
  budget, optionally label clusters, and export the signal domain and response
  presence matrix. See [signal pipeline instructions](signal_extraction_pipeline/README.md).

All LLM prompts live in `prompts/`. Compute configuration files belong in
`quest_computes/`. Generated paper data and experiment result directories are
excluded from version control. Signal extraction requires served chat, matching
chat tokenizer, and embedding endpoints; it does not launch GPU services.

## Statistical modeling

`statistical_modeling_pipeline/` consumes completed signal extraction outputs,
joins paper-level citation metadata, and runs the original four fixed-signal
logistic models. The audit signal block feeds Model 4 directly; Models 1–3
additionally require paper/human signals and, for Model 1, LLM citation predictions.
Missing inputs are reported explicitly. See
[statistical modeling instructions](statistical_modeling_pipeline/README.md) for
input formats, model definitions, outputs and statistical interpretation limits.
Compute settings and the reused legacy numerical environment specification live
in `quest_computes/statistical_modeling.yaml` and
`quest_computes/statistical_modeling_requirements.txt`.

## Agent trajectory records

Agent interactions are saved incrementally under `trace_databases/paper_review/`,
with a separate JSONL file per attempt and paper. They capture full model inputs,
outputs, tool arguments/results and failure events. The review pipeline links
each attempt's file from `status.json`. See
[trace database format](trace_databases/README.md) for event fields and usage.
