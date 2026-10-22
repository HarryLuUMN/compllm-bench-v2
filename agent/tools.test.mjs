import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createTools, python } from './tools.mjs';

const paper = fileURLToPath(new URL('../paper_database/paper_0001', import.meta.url));

test('real PDF text includes page numbers and rejects invalid ranges', async () => {
  const tools = await createTools(paper);
  const read = tools.find(tool => tool.name === 'read_pdf');
  const value = JSON.parse((await read.execute('', { file: 'paper.pdf', start_page: 1, end_page: 1 })).content[0].text);
  assert.equal(value.pages[0].page, 1);
  assert.ok(value.pages[0].text.includes('Characteristics'));
  assert.ok(value.total_pages > 1);
  await assert.rejects(read.execute('', { file: 'paper.pdf', start_page: 0 }));
  await assert.rejects(read.execute('', { file: 'paper.pdf', start_page: 1, end_page: 11 }));
});

test('PDF tools block traversal and symlink escapes', async () => {
  const root = await mkdtemp(join(tmpdir(), 'paper-agent-'));
  try {
    await mkdir(join(root, 'local'));
    await symlink(join(paper, 'paper.pdf'), join(root, 'local', 'outside.pdf'));
    const tools = await createTools(join(root, 'local'));
    const read = tools.find(tool => tool.name === 'read_pdf');
    await assert.rejects(read.execute('', { file: 'outside.pdf' }), /inside/);
    await assert.rejects(read.execute('', { file: join(paper, 'paper.pdf') }));
    const listed = JSON.parse((await tools[0].execute('', {})).content[0].text);
    assert.deepEqual(listed.files, []);
    await assert.rejects(python(['pdf', join(root, 'local', 'outside.pdf'), '--root', join(root, 'local')]));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('web search sends expected request and handles missing keys and HTTP errors', async () => {
  const tools = await createTools(paper, { apiKey: 'test-only', fetchImpl: async (url, options) => {
    assert.equal(url, 'https://ydc-index.io/v1/search');
    assert.equal(JSON.parse(options.body).query, 'test query');
    assert.equal(options.headers['X-API-Key'], 'test-only');
    return { ok: true, json: async () => ({ results: { web: [{ title: 'Evidence', url: 'https://example.org', snippets: ['source'] }] } }) };
  } });
  const value = JSON.parse((await tools[2].execute('', { query: 'test query' })).content[0].text);
  assert.equal(value.results[0].url, 'https://example.org');
  const missing = await createTools(paper, { apiKey: '' });
  await assert.rejects(missing[2].execute('', { query: 'test' }), /YDC_API_KEY/);
  const failed = await createTools(paper, { apiKey: 'test-only', fetchImpl: async () => ({ ok: false, status: 429 }) });
  await assert.rejects(failed[2].execute('', { query: 'test' }), /429/);
});

test('Brave search uses the configured key when You.com is not configured', async () => {
  const tools = await createTools(paper, { apiKey: '', braveApiKey: 'test-only', fetchImpl: async (url, options) => {
    const parsed = new URL(url);
    assert.equal(parsed.hostname, 'api.search.brave.com');
    assert.equal(parsed.searchParams.get('q'), 'test query');
    assert.equal(options.method, 'GET');
    assert.equal(options.headers['X-Subscription-Token'], 'test-only');
    return { ok: true, json: async () => ({ web: { results: [{ title: 'Evidence', url: 'https://example.org', extra_snippets: ['evidence'] }] } }) };
  } });
  const value = JSON.parse((await tools[2].execute('', { query: 'test query' })).content[0].text);
  assert.equal(value.provider, 'brave');
  assert.deepEqual(value.results[0].snippets, ['evidence']);
});
