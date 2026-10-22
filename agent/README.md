# Paper assistant (pi harness)

Layout:

```text
new-codebase/
├── agent/
│   ├── run.mjs
│   ├── tools.mjs
│   └── pdf_reader.py
├── prompts/
│   └── paper_assistant.yaml
└── paper_database/
```

Run from the repository root:

```sh
node new-codebase/agent/run.mjs \
  --folder new-codebase/paper_database/paper_0001 \
  --question '阅读论文，概括研究方法，并搜索相关证据'
```

The runner uses the existing pi harness installed in `pi-agent/node_modules` and
its model/auth configuration in `pi-agent/agent`. Select a configured model with
`--provider PROVIDER --model MODEL`. Override configuration with `--agent-dir`.
`PI_SDK_PATH` can point to another installed pi SDK's `dist/index.js`.
No packages are installed by this implementation.

Python uses the existing `pypdf` and `PyYAML` installations. Set `PYTHON` if they
are installed in a specific environment. Web search uses You.com with
`YDC_API_KEY` (or `YOU_COM_API_KEY`), or Brave Search with
`BRAVE_SEARCH_API_KEY` when no You.com key is configured; `.env` is not automatically loaded.

`--folder` defaults to `new-codebase/paper_database`. `--prompt` defaults to
`new-codebase/prompts/paper_assistant.yaml`; alternative YAML files must have a
nonempty `system` string. Prompt loading uses `yaml.safe_load`.

Tools:

- `list_pdfs`: recursive discovery, 100 paths per page; skips symlinks.
- `read_pdf`: relative PDF path, 1-based page range, default 5 / maximum 10 pages,
  maximum 60,000 text characters per call. Rejects paths outside the selected
  folder, including symlink escapes. Returns page numbers and truncation flags.
- `web_search`: up to 5 results with source URLs and snippets, 30-second timeout.

Only these three tools are active. No shell or file editing tools are exposed.
Sessions are in memory; answers stream to stdout and tool names go to stderr.
PDF extraction reads text, not figures or scanned images; OCR is not included.
Search returns snippets and does not fetch the full linked webpage.

Checks:

```sh
node new-codebase/agent/run.mjs --folder new-codebase/paper_database/paper_0001 --check
node --test new-codebase/agent/tools.test.mjs
```

`--check` loads the YAML, imports the actual pi SDK and lists local PDFs, without
calling a model. The tests require the downloaded `paper_0001/paper.pdf` fixture;
they validate real PDF extraction and mock web requests without credentials.

Every actual run now saves its full trajectory under
`trace_databases/paper_review/<folder>/`. This includes each model request/context,
streamed and final outputs, tool arguments/results, retries and errors. Choose a
different category using `--trace-root`, or an exact new path using `--trajectory`.
See [trajectory format](../trace_databases/README.md). `--trace FILE` continues to
save the small tool-success summary used by the review pipeline.

`--thinking off|low|medium|high` selects the pi thinking level (default `off`).
Use a reasoning-capable model and matching server-side thinking settings. Returned
thinking content is retained in the full trajectory alongside final text.
