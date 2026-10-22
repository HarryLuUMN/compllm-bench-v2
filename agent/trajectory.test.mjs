import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { Trajectory } from './trajectory.mjs';

const here = fileURLToPath(new URL('.', import.meta.url));
const rows = async file => (await readFile(file, 'utf8')).trim().split('\n').map(line => JSON.parse(line));

async function run(args) {
  const child = spawn(process.execPath, [join(here, 'run.mjs'), ...args], { stdio: ['ignore', 'pipe', 'pipe'] });
  let stdout = '', stderr = '';
  child.stdout.on('data', data => { stdout += data; });
  child.stderr.on('data', data => { stderr += data; });
  const timer = setTimeout(() => child.kill('SIGKILL'), 20000);
  try {
    const code = await new Promise((done, reject) => { child.on('error', reject); child.on('close', done); });
    return { code, stdout, stderr };
  } finally {
    clearTimeout(timer);
  }
}

test('trajectory snapshots are incremental, preserve full events and redact credential fields', async () => {
  const root = await mkdtemp(join(tmpdir(), 'agent-trace-'));
  try {
    const trace = new Trajectory({ root, folder: '/papers/paper_test', metadata: { question: 'test input' } });
    const message = { text: 'original', apiKey: 'private-key' };
    trace.event({ type: 'turn_start' });
    trace.record('model_input', message);
    message.text = 'mutated';
    trace.event({ type: 'tool_execution_start', toolCallId: 'call1', toolName: 'read_pdf', args: { file: 'paper.pdf' } });
    trace.event({ type: 'tool_execution_end', toolCallId: 'call1', toolName: 'read_pdf', isError: true, result: { error: 'bad PDF' } });
    trace.event({ type: 'message_update', message: { content: 'large cumulative duplicate' },
      assistantMessageEvent: { type: 'text_delta', delta: 'answer', partial: { text: 'large cumulative duplicate' } } });
    const saved = await rows(trace.file);
    assert.deepEqual(saved.map(row => row.sequence), [0, 1, 2, 3, 4, 5]);
    assert.equal(saved[2].data.text, 'original');
    assert.equal(saved[2].data.apiKey, '[REDACTED]');
    assert.equal(saved[4].data.isError, true);
    assert.equal(saved[5].data.assistantMessageEvent.delta, 'answer');
    assert.ok(!(await readFile(trace.file, 'utf8')).includes('large cumulative duplicate'));
    assert.equal(saved[4].turn, 0);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('recording cannot overwrite an existing trajectory', async () => {
  const root = await mkdtemp(join(tmpdir(), 'agent-trace-'));
  try {
    const file = join(root, 'existing.jsonl');
    await writeFile(file, 'previous trajectory\n');
    assert.throws(() => new Trajectory({ file, folder: root, metadata: {} }), /EEXIST/);
    assert.equal(await readFile(file, 'utf8'), 'previous trajectory\n');
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('actual pi SDK records two model requests, tool arguments/results and final response', async () => {
  const root = await mkdtemp(join(tmpdir(), 'agent-trace-sdk-'));
  let requests = 0;
  const server = createServer(async (request, response) => {
    let body = '';
    for await (const part of request) body += part;
    const payload = JSON.parse(body);
    requests += 1;
    assert.ok(payload.messages.length);
    response.writeHead(200, { 'Content-Type': 'text/event-stream' });
    const chunk = (delta, finish_reason = null) => response.write(`data: ${JSON.stringify({
      id: 'trace-test', object: 'chat.completion.chunk', created: 1, model: 'trace-model',
      choices: [{ index: 0, delta, finish_reason }],
    })}\n\n`);
    if (requests === 1) {
      chunk({ role: 'assistant', tool_calls: [{ index: 0, id: 'call_list', type: 'function',
        function: { name: 'list_pdfs', arguments: '{}' } }] });
      chunk({}, 'tool_calls');
    } else {
      chunk({ role: 'assistant', content: 'No PDFs were found in this folder.' });
      chunk({}, 'stop');
    }
    response.end('data: [DONE]\n\n');
  });
  await new Promise(done => server.listen(0, '127.0.0.1', done));
  try {
    const folder = join(root, 'paper_test'), agentDir = join(root, 'config');
    await mkdir(folder);
    await mkdir(agentDir);
    const secret = 'test-key-never-log-this';
    await writeFile(join(agentDir, 'models.json'), JSON.stringify({ providers: { 'trace-test': {
      baseUrl: `http://127.0.0.1:${server.address().port}/v1`, api: 'openai-completions', apiKey: secret,
      models: [{ id: 'trace-model', name: 'Trace test', input: ['text'], reasoning: false,
        contextWindow: 32768, maxTokens: 1024, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } }],
    } } }));
    const file = join(root, 'trace.jsonl');
    const responseFile = join(root, 'response.txt'), toolsFile = join(root, 'tools.json');
    const result = await run(['--folder', folder, '--agent-dir', agentDir, '--provider', 'trace-test',
      '--model', 'trace-model', '--question', 'List the available PDFs.', '--trajectory', file,
      '--output', responseFile, '--trace', toolsFile]);
    assert.equal(result.code, 0, result.stderr);
    assert.equal(requests, 2);
    const events = await rows(file);
    assert.equal(events.filter(row => row.type === 'model_input').length, 2);
    assert.equal(events.filter(row => row.type === 'provider_request').length, 2);
    assert.equal(events.filter(row => row.type === 'tool_execution_start')[0].data.toolCallId, 'call_list');
    const tool = events.find(row => row.type === 'tool_execution_end');
    assert.equal(tool.data.toolName, 'list_pdfs');
    assert.equal(tool.data.isError, false);
    assert.ok(JSON.stringify(tool.data.result).includes('total'));
    assert.ok(JSON.stringify(events.filter(row => row.type === 'model_input')[1]).includes('call_list'));
    assert.equal(events.at(-1).type, 'run_end');
    assert.equal(events.at(-1).data.status, 'completed');
    assert.equal((await readFile(responseFile, 'utf8')).trim(), 'No PDFs were found in this folder.');
    assert.ok(!(await readFile(file, 'utf8')).includes(secret));
    assert.deepEqual(JSON.parse(await readFile(toolsFile, 'utf8')), [{ tool: 'list_pdfs', success: true }]);
  } finally {
    server.closeAllConnections();
    await new Promise(done => server.close(done));
    await rm(root, { recursive: true, force: true });
  }
});

test('initialization failures still persist an error and failed run status', async () => {
  const root = await mkdtemp(join(tmpdir(), 'agent-trace-failure-'));
  try {
    const file = join(root, 'failure.jsonl');
    const result = await run(['--folder', root, '--agent-dir', root, '--provider', 'nonexistent-provider',
      '--model', 'nonexistent-model', '--question', 'test', '--trajectory', file]);
    assert.notEqual(result.code, 0);
    const events = await rows(file);
    assert.ok(events.some(row => row.type === 'run_error' && row.data.error.message.includes('Unknown model')));
    assert.equal(events.at(-1).data.status, 'failed');
  } finally { await rm(root, { recursive: true, force: true }); }
});
