# Agent trajectories

Every normal `agent/run.mjs` invocation saves an independent, incrementally
appended JSONL trajectory. The default layout is:

```text
trace_databases/
├── paper_review/
│   └── paper_0001/
│       ├── timestamp_uuid.jsonl
│       └── another_run.jsonl
└── radiology_review/
```

Direct agent runs choose a timestamp/UUID filename automatically. The review
pipeline assigns a fresh UUID for every attempted paper and records the absolute
path in its `status.json` under `trajectory`. Reruns preserve previous traces.
Resumed papers do not create a new trace because no agent call is made.
`--check` only inspects setup and does not create a trajectory.

Use `--trace-root` on the agent or review pipeline to choose the parent directory;
for example `--trace-root trace_databases/radiology_review`. The agent also accepts
`--trajectory FILE.jsonl` for an exact filename; an existing file is never
replaced. `--trace tools.json` remains the separate compact tool-success summary.

## JSONL schema

Each line is an independent JSON object with:

- `sequence`: monotonically increasing event index within the run.
- `timestamp`: UTC ISO timestamp.
- `run_id`: UUID shared by the run's events.
- `turn`: local model-turn counter, starting at 0; -1 before the first turn.
- `request`: provider-request counter; increments on each outgoing request.
- `type`: event name.
- `data`: complete event payload or a snapshot of the relevant data.

Events include:

- `run_start`: original question, YAML path/system prompt, folder and tool schemas.
- `session_start`: actual selected provider and model.
- `turn_start`, `turn_end`: agent turn boundaries.
- `model_input`: full normalized context, including system messages, after pi's
  context processing. This occurs before its corresponding provider request;
  its request counter therefore still refers to the preceding request.
- `provider_request`: actual outgoing provider payload, including its model,
  message history, tools and generation parameters as supplied by pi.
- `provider_response`, `provider_stream_event`: HTTP response metadata and raw
  parsed streaming events when surfaced by the provider integration.
- `message_start`, `message_update`, `message_end`: user, assistant and tool-result
  messages, streaming deltas and full completed messages. Cumulative `partial`
  snapshots are omitted from update events to avoid duplicating growing text;
  original deltas and completed messages are preserved.
- `tool_execution_start`, `tool_execution_update`, `tool_execution_end`: call ID,
  name, arguments, returned content/partial results and error status.
- `final_response`: the final free-form response.
- `run_error`, `run_end`: error details, completion status and final message history.

Other session events, including retry/compaction events, are also preserved. A
turn may involve retries; use request IDs, event order and tool-call IDs rather
than assuming a strict one-call-per-turn relationship. Recordings contain model
output actually exposed by the SDK, not hidden internal state or unreturned
reasoning. Credentials from auth configuration and outgoing request headers are
not collected; known credential fields in recorded objects are redacted.

Writes are synchronous and incremental, so earlier events remain if an agent is
killed. A hard kill or power loss can leave a trace without `run_end`; inspect the
review pipeline's status/log for timeout information. A failure before the prompt
or input folder can be loaded has no model interaction and may not create a trace.
No historic interactions can be recovered from pre-instrumentation runs.

Generated JSONL files are ignored by Git; code and documentation are tracked.
This is a filesystem JSONL trace store, not a relational database.

## Tests

```sh
node --test agent/trajectory.test.mjs
python3 -m unittest discover -s review_pipeline -p 'test_*.py'
```

Tests use the real installed pi SDK with a local simulated streaming server,
including a tool-call round and a second model round. They verify full request
capture, tool linkage/results, final output, incremental persistence, failure
recording, credential-field redaction and exclusive trace creation.
