const timeline = document.querySelector('#timeline');
const selector = document.querySelector('#traces');
const status = document.querySelector('#status');
const staticMode = window.TRACE_STATIC === true;
let staticEvents = null;
let cursor = 0, generation = 0, busy = false, tools = new Map(), assistant = null, terminal = '';
const pretty = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
function node(tag, text, parent) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (parent) parent.append(el);
  return el;
}
function details(parent, title, value, open = false) {
  const el = node('details', undefined, parent); el.open = open;
  node('summary', title, el); node('pre', pretty(value), el); return el;
}
function card(title, event, kind = '') {
  const el = node('section', undefined, timeline); el.className = `card ${kind}`;
  const heading = node('h2', title, el);
  node('small', `Turn ${event.turn ?? '—'} · ${event.timestamp?.slice(11,19) || ''}`, heading);
  return el;
}
function block(kind, event) {
  if (!assistant) assistant = {};
  if (!assistant[kind]) assistant[kind] = node('pre', '', card(kind, event, kind));
  return assistant[kind];
}
function result(parent, value) {
  const content = value?.content || [];
  for (const part of content) {
    if (!part.text) { details(parent, 'Content', part); continue; }
    let payload;
    try { payload = JSON.parse(part.text); } catch { node('pre', part.text, parent); continue; }
    if (Array.isArray(payload.results)) {
      node('p', `Search results · ${payload.provider || ''}`, parent).className = 'meta';
      for (const item of payload.results) {
        const section = node('div', undefined, parent); section.className = 'result';
        const link = node('a', item.title || item.url, section);
        try { const url = new URL(item.url); if (['https:','http:'].includes(url.protocol)) {link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';} } catch {}
        node('p', item.description || '', section);
        for (const snippet of item.snippets || []) node('p', snippet, section);
      }
    } else if (Array.isArray(payload.pages)) {
      node('p', `${payload.file} · ${payload.total_pages} pages`, parent).className = 'meta';
      for (const page of payload.pages) details(parent, `Page ${page.page}${page.truncated ? ' · truncated by reader' : ''}`, page.text, true);
    } else node('pre', pretty(payload), parent);
  }
}
function render(e) {
  const d = e.data || {};
  switch (e.type) {
    case 'run_start': {
      const el = card('Input · task', e);
      node('p', `${d.requested_model || ''} · thinking ${d.thinking_level || 'off'}`, el).className = 'meta';
      details(el, 'System prompt', d.system_prompt || '');
      details(el, 'User input / human reviews', d.question || '', true); break;
    }
    case 'model_input':
      assistant = null;
      details(card('Input · model context', e), 'Full context sent to the model', d.messages); break;
    case 'message_update': {
      const delta = d.assistantMessageEvent || {};
      if (delta.type === 'thinking_delta') block('thinking', e).textContent += delta.delta || '';
      if (delta.type === 'text_delta') block('output', e).textContent += delta.delta || '';
      break;
    }
    case 'message_end':
      if (d.message?.role === 'assistant') {
        for (const kind of ['thinking','output']) {
          const text = (d.message.content || []).filter(p => p.type === (kind === 'output' ? 'text' : kind)).map(p => p.text || p.thinking || '').join('\n');
          if (text) block(kind, e).textContent = text;
        }
      }
      break;
    case 'tool_execution_start': {
      const el = card(`Event: tool_calling · ${d.toolName}`, e, 'tool');
      details(el, 'Arguments', d.args, true); tools.set(d.toolCallId, el); break;
    }
    case 'tool_execution_end': {
      const el = tools.get(d.toolCallId) || card(`Tool result · ${d.toolName}`, e, 'tool');
      if (d.isError) {el.classList.add('error'); node('p', 'Tool failed', el);}
      result(el, d.result); break;
    }
    case 'run_error': case 'parse_error':
      node('pre', pretty(d.error), card('Error', e, 'error')); break;
    case 'run_end': terminal = d.status || 'ended'; node('p', terminal, card('Run ended', e)); break;
    case 'final_response':
      if (!assistant?.output) block('output', e).textContent = d.text || '';
      break;
  }
}
async function loadList() {
  const response = await fetch(staticMode ? './data/traces.json' : '/api/traces'); if (!response.ok) throw Error('Cannot list traces');
  const traces = await response.json(); const current = selector.value;
  selector.replaceChildren(node('option', traces.length ? 'Select a trace' : 'No traces yet'));
  selector.options[0].value = '';
  for (const trace of traces) {const option = node('option', trace.label || trace.id, selector); option.value = trace.id;}
  selector.value = current;
  if (!selector.value && traces.length) {selector.value = traces[0].id; selectTrace();}
}
function selectTrace() {
  generation++; staticEvents = null; busy = false; cursor = 0; tools = new Map(); assistant = null; terminal = '';
  timeline.replaceChildren(); poll(true);
}
async function poll(force = false) {
  if (busy || !selector.value || (!force && !document.querySelector('#follow').checked)) return;
  busy = true; const version = generation;
  try {
    let more;
    do {
      let payload;
      if (staticMode) {
        if (!staticEvents) {
          const response = await fetch(`./data/${encodeURIComponent(selector.value)}`);
          if (!response.ok) throw Error('Cannot load published trace');
          const events = await response.json();
          if (version !== generation) return;
          staticEvents = events;
        }
        payload = {events: staticEvents.slice(cursor), cursor: staticEvents.length, more: false};
      } else {
        const response = await fetch(`/api/events?trace=${encodeURIComponent(selector.value)}&cursor=${cursor}`);
        payload = await response.json(); if (!response.ok) throw Error(payload.error);
      }
      if (version !== generation) return;
      if (payload.reset) {timeline.replaceChildren(); tools.clear(); assistant = null; terminal = '';}
      for (const event of payload.events) render(event);
      cursor = payload.cursor; more = payload.more;
    } while (more && version === generation);
    status.textContent = staticMode ? `Published snapshot · ${terminal || 'no end event'} · ${cursor} events` : `${terminal || 'Following · no end event yet'} · ${(cursor / 1024).toFixed(0)} KB read`;
    if (document.querySelector('#scroll').checked) window.scrollTo(0, document.body.scrollHeight);
  } catch (error) {if (version === generation) status.textContent = error.message;}
  finally {if (version === generation) busy = false;}
}
selector.addEventListener('change', selectTrace);
document.querySelector('#refresh').addEventListener('click', () => loadList().catch(error => status.textContent = error.message));
document.querySelector('#follow').addEventListener('change', () => poll());
if (staticMode) {
  document.querySelector('#follow').checked = false;
  document.querySelector('#follow').disabled = true;
} else setInterval(poll, 1500);
loadList().catch(error => status.textContent = error.message);
