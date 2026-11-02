import { readFile } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';

export async function stagedReview({ session, tools, trajectory, trace, prompt, question, reviewsFile }) {
  const instructions = prompt.repair;
  if (!instructions || !['chunk', 'continue_notes', 'continue_final', 'compact'].every(key => typeof instructions[key] === 'string')) {
    throw new Error('Staged review requires repair instructions in the prompt YAML');
  }
  const last = () => session.messages.filter(message => message.role === 'assistant').at(-1);
  async function submit(text, continuation) {
    if ((session.getContextUsage()?.tokens ?? 0) > 48000) await session.compact(instructions.compact);
    for (let attempt = 0; attempt < 3; attempt++) {
      const before = session.messages.length;
      let emitted;
      const unsubscribe = session.subscribe?.(event => {
        if (event.type === 'message_end' && event.message?.role === 'assistant') emitted = event.message;
      });
      try {
        await session.prompt(attempt ? continuation : text);
      } finally {
        unsubscribe?.();
      }
      const message = emitted ?? (session.messages.length > before ? last() : undefined);
      if (message?.stopReason === 'error' || message?.stopReason === 'aborted') {
        throw new Error(message.errorMessage || `Model stopped: ${message.stopReason}`);
      }
      if (message?.stopReason === 'stop' && message.content?.some(block => block.type === 'text' && block.text?.trim())) return;
      if (attempt < 2 && (session.getContextUsage()?.tokens ?? 0) > 48000) await session.compact(instructions.compact);
    }
    throw new Error('Staged review exhausted its bounded continuation attempts');
  }
  async function execute(name, args) {
    const toolCallId = `repair_${randomUUID()}`;
    trajectory.event({ type: 'tool_execution_start', toolName: name, toolCallId, args });
    let result;
    try {
      result = await tools.find(tool => tool.name === name).execute(toolCallId, args);
    } catch (error) {
      trajectory.event({ type: 'tool_execution_end', toolName: name, toolCallId, isError: true,
        result: { content: [{ type: 'text', text: error.message }] } });
      trace.push({ tool: name, success: false });
      throw error;
    }
    trajectory.event({ type: 'tool_execution_end', toolName: name, toolCallId, isError: false, result });
    trace.push({ tool: name, success: true });
    return JSON.parse(result.content[0].text);
  }
  async function ingest(text, source, onRead) {
    const characters = Array.from(text);
    for (let start = 0; start < characters.length; start += 24000) {
      const end = Math.min(start + 24000, characters.length);
      const message = instructions.chunk.replaceAll('{{source}}', source)
        .replaceAll('{{start}}', String(start)).replaceAll('{{end}}', String(end))
        .replaceAll('{{total}}', String(characters.length))
        .replace('{{evidence}}', characters.slice(start, end).join(''));
      await submit(message, instructions.continue_notes);
      onRead?.(start, end, characters.length);
    }
  }
  const listed = await execute('list_pdfs', {});
  if (!listed.files.includes('paper.pdf')) throw new Error('Staged review requires paper.pdf');
  const reviews = await readFile(reviewsFile, 'utf8');
  JSON.parse(reviews);
  const sha256 = createHash('sha256').update(reviews).digest('hex');
  await ingest(reviews, 'complete human_reviews/reviews.json', (start, end, total) => {
    trajectory.record('review_chunk_read', { start, end, total, sha256 });
  });
  let totalPages;
  for (let start = 1; totalPages === undefined || start <= totalPages;) {
    const end = totalPages === undefined ? 1 : Math.min(start + 2, totalPages);
    const result = await execute('read_pdf', { file: 'paper.pdf', start_page: start, end_page: end });
    totalPages = result.total_pages;
    if (!Number.isInteger(totalPages) || totalPages < 1) throw new Error('Invalid PDF page count');
    for (let page of result.pages) {
      if (page.truncated) {
        const reread = await execute('read_pdf', { file: 'paper.pdf', start_page: page.page,
          end_page: page.page, max_characters: 240000 });
        page = reread.pages[0];
      }
      if (page.truncated) throw new Error(`PDF page ${page.page} remains truncated`);
      await ingest(page.text || '[No extractable text on this page; figures/scans were not interpreted.]',
        `paper.pdf page ${page.page} of ${totalPages}`);
      trajectory.record('pdf_page_ingested', { page: page.page, total_pages: totalPages });
    }
    start = end + 1;
  }
  await submit(question, instructions.continue_final);
  if (!trace.some(event => event.tool === 'web_search' && event.success)) {
    await submit(instructions.continue_final, instructions.continue_final);
  }
  if (!trace.some(event => event.tool === 'web_search' && event.success)) {
    throw new Error('Staged review did not complete a successful web search');
  }
}
