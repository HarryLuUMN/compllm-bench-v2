import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { stagedReview } from './repair.mjs';

const prompt = { repair: {
  chunk: '{{source}} {{start}} {{end}} {{total}}\n{{evidence}}',
  continue_notes: 'finish notes', continue_final: 'finish audit with search', compact: 'retain evidence',
} };
const response = value => ({ content: [{ type: 'text', text: JSON.stringify(value) }] });

test('staged review delivers complete Unicode reviews and every PDF page, rereading truncation', async () => {
  const root = await mkdtemp(join(tmpdir(), 'staged-review-'));
  try {
    const reviews = JSON.stringify({ text: 'Evidence 😀\u2028'.repeat(6000) });
    const reviewsFile = join(root, 'reviews.json');
    await writeFile(reviewsFile, reviews);
    const events = [], trace = [], inputs = [], readCalls = [];
    let usage = 0, compactions = 0;
    const session = {
      messages: [], getContextUsage: () => ({ tokens: usage }),
      async compact(instruction) { assert.equal(instruction, 'retain evidence'); compactions++; usage = 1000; },
      async prompt(text) {
        inputs.push(text); usage = 49000;
        if (text === 'AUDIT') trace.push({ tool: 'web_search', success: true });
        this.messages.push({ role: 'assistant', stopReason: 'stop', content: [{ type: 'text', text: 'evidence notes' }] });
      },
    };
    const tools = [
      { name: 'list_pdfs', execute: async () => response({ files: ['paper.pdf'] }) },
      { name: 'read_pdf', execute: async (_id, args) => {
        readCalls.push(args);
        const pages = [];
        for (let page = args.start_page; page <= args.end_page; page++) {
          pages.push({ page, text: `Complete evidence on page ${page}.`, truncated: page === 4 && !args.max_characters });
        }
        return response({ file: 'paper.pdf', total_pages: 7, pages });
      } },
    ];
    await stagedReview({ session, tools, trace, prompt, question: 'AUDIT', reviewsFile,
      trajectory: { event: event => events.push({ type: event.type, data: event }),
        record: (type, data) => events.push({ type, data }) } });
    const chunks = events.filter(event => event.type === 'review_chunk_read').map(event => event.data);
    const characters = Array.from(reviews);
    assert.equal(chunks[0].start, 0);
    assert.equal(chunks.at(-1).end, characters.length);
    for (let i = 0; i < chunks.length; i++) {
      assert.equal(chunks[i].start, i ? chunks[i - 1].end : 0);
      assert.ok(inputs[i].endsWith(characters.slice(chunks[i].start, chunks[i].end).join('')));
    }
    assert.deepEqual(events.filter(event => event.type === 'pdf_page_ingested').map(event => event.data.page), [1, 2, 3, 4, 5, 6, 7]);
    assert.ok(readCalls.some(args => args.start_page === 4 && args.end_page === 4 && args.max_characters === 240000));
    assert.equal(inputs.at(-1), 'AUDIT');
    assert.ok(compactions > 0);
    assert.equal(events[0].data.toolName, 'list_pdfs');
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('thinking-only length stops receive bounded continuation and never mark evidence read', async () => {
  const root = await mkdtemp(join(tmpdir(), 'staged-review-'));
  try {
    const reviewsFile = join(root, 'reviews.json');
    await writeFile(reviewsFile, '{}');
    const events = [], inputs = [];
    const session = { messages: [], getContextUsage: () => ({ tokens: 1000 }),
      async prompt(text) {
        inputs.push(text);
        this.messages.push({ role: 'assistant', stopReason: 'length', content: [{ type: 'thinking', thinking: 'unfinished' }] });
      } };
    await assert.rejects(stagedReview({ session, tools: [{ name: 'list_pdfs', execute: async () => response({ files: ['paper.pdf'] }) }],
      trace: [], prompt, question: 'AUDIT', reviewsFile,
      trajectory: { event() {}, record: (type, data) => events.push({ type, data }) } }), /bounded continuation/);
    assert.equal(inputs.length, 3);
    assert.equal(inputs[1], 'finish notes');
    assert.equal(events.length, 0);
  } finally { await rm(root, { recursive: true, force: true }); }
});
