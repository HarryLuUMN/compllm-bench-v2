let papers = [], filtered = [], selected = '', visible = 100, current = null, request = null;
const el = id => document.getElementById(id);
async function get(url, signal) {
  const response = await fetch(url, {signal});
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || 'Could not load results');
  return data;
}
function renderList() {
  const query = el('search').value.trim().toLowerCase();
  filtered = papers.filter(p => p.includes(query));
  el('matches').textContent = `${filtered.length.toLocaleString()} matching papers`;
  el('papers').replaceChildren();
  for (const paper of filtered.slice(0, visible)) {
    const button = document.createElement('button');
    button.textContent = paper;
    button.setAttribute('aria-current', String(paper === selected));
    button.onclick = () => { location.hash = paper; };
    el('papers').append(button);
  }
  el('more').hidden = visible >= filtered.length;
  updateNavigation();
}
function updateNavigation() {
  const i = filtered.indexOf(selected);
  el('previous').disabled = i <= 0;
  el('next').disabled = i < 0 || i >= filtered.length - 1;
}
function label(section) {
  const match = section.document.match(/^ReviewerReport_V(\d+)_R(\d+)$/);
  if (match) return `Reviewer ${match[2]} · Round ${match[1]}`;
  if (section.kind === 'ai') return 'Final AI review';
  if (section.document === 'reviewer_comments_quoted_in_author_response') return 'Reviewer comments from response history';
  return section.document.replaceAll('_', ' ');
}
function renderSection(section) {
  const card = document.createElement('article');
  card.className = `card ${section.kind === 'human' ? 'human' : ''}`;
  const title = document.createElement('h4');
  title.textContent = label(section);
  const list = document.createElement('ul');
  for (const line of section.text.split('\n').filter(l => l.trim())) {
    const item = document.createElement('li');
    item.textContent = line.trim().replace(/^-\s+/, '');
    list.append(item);
  }
  card.append(title, list);
  el(section.kind === 'ai' ? 'ai' : 'human').append(card);
}
async function selectPaper() {
  const paper = location.hash.slice(1);
  if (!papers.includes(paper)) {
    if (papers.length) location.hash = papers[0];
    return;
  }
  selected = paper;
  request?.abort();
  request = new AbortController();
  current = null;
  el('download').disabled = true;
  el('title').textContent = paper;
  el('state').className = '';
  el('state').textContent = 'Loading summaries…';
  el('ai').replaceChildren(); el('human').replaceChildren();
  renderList();
  try {
    const data = await get(`/api/summary?paper=${encodeURIComponent(paper)}`, request.signal);
    if (selected !== paper) return;
    current = data;
    data.sections.forEach(renderSection);
    el('state').textContent = `DeepSeek summaries · ${data.sections.filter(s => s.kind === 'human').length} human review documents`;
    el('download').disabled = false;
  } catch (error) {
    if (error.name !== 'AbortError') { el('state').textContent = error.message; el('state').className = 'error'; }
  }
}
el('search').oninput = () => { visible = 100; renderList(); };
el('more').onclick = () => { visible += 100; renderList(); };
el('previous').onclick = () => { location.hash = filtered[filtered.indexOf(selected) - 1]; };
el('next').onclick = () => { location.hash = filtered[filtered.indexOf(selected) + 1]; };
el('download').onclick = () => {
  if (!current) return;
  const text = current.sections.map(s => `${label(s)}\n${s.text.trim()}`).join('\n\n');
  const url = URL.createObjectURL(new Blob([`${current.paper}\n\n${text}\n`], {type:'text/plain;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = `${current.paper}_summaries.txt`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
window.addEventListener('hashchange', selectPaper);
try {
  const data = await get('/api/summaries'); papers = data.papers;
  el('count').textContent = `${papers.length.toLocaleString()} papers`;
  renderList();
  if (!papers.length) el('state').textContent = 'No summary results are available in this dataset.';
  else await selectPaper();
} catch (error) { el('count').textContent = 'Could not load papers'; el('state').textContent = error.message; el('state').className = 'error'; }
