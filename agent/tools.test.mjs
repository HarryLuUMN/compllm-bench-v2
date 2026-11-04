import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createTools, python, citationCountReasons } from './tools.mjs';

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


test('citation counts are recognized without treating ordinary references as counts', () => {
  for (const text of ['Cited by 123', 'cited by: 1,234', '1.2k citations', '0 citations',
    'Citation count: 42', 'Citation number 24', 'Citations: 123', 'Citations (123)',
    '被引用次数：123', '123次引用']) {
    assert.ok(citationCountReasons({ description: text }).length, text);
  }
  for (const item of [{ citationCount: 0 }, { citation_count: '1,234' },
    { metadata: { cited_by_count: 123 } }, { citations: '1.2k' }]) {
    assert.ok(citationCountReasons(item).length, JSON.stringify(item));
  }
  for (const text of ['Published in 2024; DOI: 10.1234/5678', 'See citation [12] and references 3–5.',
    'See citations 12–15.', 'A randomized trial of 123 patients.', 'Cited by the authors as a limitation.',
    'Citation analysis methods', 'Reference count: 123']) {
    assert.deepEqual(citationCountReasons({ description: text }), [], text);
  }
  assert.deepEqual(citationCountReasons({ citations: [{ title: 'A reference from 2024' }] }), []);
});

test('both search providers filter whole raw results before truncation and keep audit data off the model result', async () => {
  for (const provider of ['you.com', 'brave']) {
    const audits = [];
    const entries = [
      { title: 'Cited by 123', url: 'https://removed.example/1' },
      { title: 'Hidden metadata', metadata: { citationCount: 456 }, url: 'https://removed.example/2' },
      { title: 'Late count', description: 'x'.repeat(2100) + ' 789 citations', url: 'https://removed.example/3' },
      { title: 'Later snippet', snippets: ['a', 'b', 'c', 'Cited by 987'], url: 'https://removed.example/4' },
      { title: 'Kept evidence', description: 'See reference [12]. DOI: 10.1234/5', snippets: ['Evidence'], url: 'https://kept.example' },
    ];
    const tools = await createTools(paper, {
      apiKey: provider === 'you.com' ? 'test-only' : '', braveApiKey: 'test-only',
      onSearchFilter: event => audits.push(event),
      fetchImpl: async () => ({ ok: true, json: async () => provider === 'you.com'
        ? { results: { web: entries } } : { web: { results: entries } } }),
    });
    const response = await tools[2].execute('', { query: 'evidence' });
    const value = JSON.parse(response.content[0].text);
    assert.deepEqual(value.results.map(item => item.url), ['https://kept.example']);
    assert.equal(value.filter.removed_count, 4);
    assert.equal(audits[0].removed.length, 4);
    assert.equal(audits[0].removed[1].reasons[0].field, 'metadata.citationCount');
    assert.ok(audits[0].removed[0].item.title.includes('123'));
    assert.ok(!JSON.stringify(response).includes('removed.example'));
    assert.ok(!JSON.stringify(response).includes('Cited by'));
  }
});

test('all citation-bearing results produce an empty result rather than leaking rejected hits', async () => {
  const tools = await createTools(paper, { apiKey: 'test-only', fetchImpl: async () => ({
    ok: true, json: async () => ({ results: { web: [{ title: 'Cited by 10' }] } }),
  }) });
  const value = JSON.parse((await tools[2].execute('', { query: 'test' })).content[0].text);
  assert.deepEqual(value.results, []);
  assert.equal(value.filter.removed_count, 1);
});
