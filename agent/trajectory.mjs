import { appendFileSync, mkdirSync, openSync, closeSync, writeFileSync, renameSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { basename, dirname, join, resolve } from 'node:path';

const secrets = /^(authorization|proxy-authorization|api[-_]?key|x-api-key|x-subscription-token|access[-_]?token|refresh[-_]?token|password|cookie|set-cookie)$/i;

export class Trajectory {
  constructor({ root, file, folder, metadata }) {
    this.runId = randomUUID();
    this.sequence = 0;
    this.turn = -1;
    this.request = 0;
    this.tokenRequests = new Map();
    this.tokenMetadata = { provider: metadata?.requested_provider, model: metadata?.requested_model, thinking: metadata?.thinking_level };
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
    this.recordTokens(type, data);
  }

  recordTokens(type, data) {
    const raw = type === 'provider_stream_event' ? data?.data?.usage : null;
    const fallback = type === 'message_end' && data.message?.role === 'assistant' ? data.message.usage : null;
    if (raw && Number.isFinite(raw.prompt_tokens) && Number.isFinite(raw.completion_tokens)) {
      const cacheRead = raw.prompt_tokens_details?.cached_tokens ?? raw.prompt_cache_hit_tokens ?? raw.cached_tokens ?? 0;
      const cacheWrite = raw.prompt_tokens_details?.cache_write_tokens ?? 0;
      this.tokenRequests.set(this.request, {
        request: this.request, input: Math.max(0, raw.prompt_tokens - cacheRead - cacheWrite),
        output: raw.completion_tokens, cacheRead, cacheWrite,
        reasoning: raw.completion_tokens_details?.reasoning_tokens ?? 0,
        totalTokens: raw.prompt_tokens + raw.completion_tokens, source: 'provider',
      });
    } else if (fallback && !this.tokenRequests.has(this.request) && fallback.totalTokens > 0) {
      const usage = Object.fromEntries(['input', 'output', 'cacheRead', 'cacheWrite', 'reasoning'].map(key => [key, fallback[key] || 0]));
      usage.totalTokens = usage.input + usage.output + usage.cacheRead + usage.cacheWrite;
      this.tokenRequests.set(this.request, { request: this.request, ...usage, source: 'assistant' });
    } else if (!['run_start', 'provider_request', 'run_end'].includes(type)) return;
    const requests = [...this.tokenRequests.values()];
    const totals = Object.fromEntries(['input', 'output', 'cacheRead', 'cacheWrite', 'reasoning', 'totalTokens']
      .map(key => [key, requests.reduce((sum, usage) => sum + usage[key], 0)]));
    const path = this.file + '.tokens.json';
    writeFileSync(path + '.tmp', JSON.stringify({ run_id: this.runId, ...this.tokenMetadata,
      requests_started: this.request, status: type === 'run_end' ? data.status : 'running', requests, totals }, null, 2) + '\n', { mode: 0o600 });
    renameSync(path + '.tmp', path);
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
