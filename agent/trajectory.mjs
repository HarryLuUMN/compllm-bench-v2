import { appendFileSync, mkdirSync, openSync, closeSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { basename, dirname, join, resolve } from 'node:path';

const secrets = /^(authorization|proxy-authorization|api[-_]?key|x-api-key|access[-_]?token|refresh[-_]?token|password|cookie|set-cookie)$/i;

export class Trajectory {
  constructor({ root, file, folder, metadata }) {
    this.runId = randomUUID();
    this.sequence = 0;
    this.turn = -1;
    this.request = 0;
    this.file = resolve(file || join(root, basename(folder), `${new Date().toISOString().replaceAll(':', '-')}_${this.runId}.jsonl`));
    mkdirSync(dirname(this.file), { recursive: true });
    const descriptor = openSync(this.file, 'wx', 0o600);
    closeSync(descriptor);
    this.record('run_start', { folder: resolve(folder), ...metadata });
  }

  record(type, data) {
    const row = { sequence: this.sequence++, timestamp: new Date().toISOString(),
      run_id: this.runId, turn: this.turn, request: this.request, type, data };
    appendFileSync(this.file, JSON.stringify(row, (key, value) => {
      if (secrets.test(key)) return '[REDACTED]';
      if (value instanceof Error) return { name: value.name, message: value.message, stack: value.stack };
      if (typeof value === 'bigint') return value.toString();
      return value;
    }) + '\n');
  }

  event(event) {
    if (event.type === 'turn_start') this.turn += 1;
    if (event.type === 'message_update') {
      const { partial: _partial, ...delta } = event.assistantMessageEvent;
      this.record(event.type, { assistantMessageEvent: delta });
    } else {
      this.record(event.type, event);
    }
  }

  extension() {
    return pi => {
      pi.on('context_with_system', event => {
        this.record('model_input', { messages: event.messages });
      });
      pi.on('before_provider_request', event => {
        this.request += 1;
        this.record('provider_request', { payload: event.payload });
      });
      pi.on('after_provider_response', event => {
        this.record('provider_response', { status: event.status, headers: event.headers });
      });
      pi.on('provider_stream_event', event => {
        this.record('provider_stream_event', event);
      });
    };
  }
}
