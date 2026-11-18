let papers = [], filtered = [], selected = '', visible = 100, current = null, request = null;
const el = id => document.getElementById(id);
let labelRequest = null;
const labelCards = new Map();
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
  const topics = document.createElement('div');
  topics.className = 'review-labels';
  topics.textContent = 'Loading topics and labels…';
  labelCards.set(section.review_id, topics);
  const summaryHeading = document.createElement('h5');
  summaryHeading.textContent = 'Review summary';
  const original = document.createElement('details');
  original.className = 'original-review';
  const toggle = document.createElement('summary');
  toggle.textContent = section.kind === 'ai' ? 'View original AI audit' : 'View original Human review';
  const body = document.createElement('div');
  original.append(toggle, body);
  let loaded = false, loading = false;
  original.addEventListener('toggle', async () => {
    if (!original.open || loaded || loading) return;
    loading = true;
    body.textContent = 'Loading original text…';
    try {
      const data = await get(`/api/original?paper=${encodeURIComponent(selected)}&review=${encodeURIComponent(section.review_id)}`);
      body.replaceChildren();
      if (data.note) {
        const note = document.createElement('p');
        note.className = 'source-note';
        note.textContent = data.note;
        body.append(note);
      }
      const download = document.createElement('button');
      download.textContent = 'Download original text';
      download.onclick = () => {
        const url = URL.createObjectURL(new Blob([data.text], {type: 'text/plain;charset=utf-8'}));
        const link = document.createElement('a');
        link.href = url; link.download = `${section.review_id.replace('::', '_')}_original.txt`; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      };
      const text = document.createElement('pre');
      text.textContent = data.text;
      body.append(download, text);
      loaded = true;
    } catch (error) {
      body.textContent = `Could not load original text: ${error.message}. Close and reopen to retry.`;
    } finally { loading = false; }
  });
  card.append(title, topics, original, summaryHeading, list);
  el(section.kind === 'ai' ? 'ai' : 'human').append(card);
}
function renderLabels(data) {
  const records = new Map(data.reviews.map(row => [row.review_id, row]));
  for (const [id, container] of labelCards) {
    container.replaceChildren();
    const row = records.get(id);
    if (!data.available || !row) {
      container.textContent = data.available ? 'Pending · this review has not been labeled yet.' : 'Topic-label results are not available.';
      container.classList.add('pending');
      continue;
    }
    container.classList.remove('pending');
    const heading = document.createElement('h5');
    heading.textContent = `Topics and labels · ${row.labels.length} labels`;
    container.append(heading);
    if (!row.labels.length) {
      const empty = document.createElement('p');
      empty.textContent = 'Completed · no problem labels assigned.';
      container.append(empty);
    }
    for (const [topic, labels] of Object.entries(row.topic_labels)) {
      const group = document.createElement('div');
      group.className = 'topic-group';
      const title = document.createElement('h6');
      title.textContent = topic;
      const chips = document.createElement('ul');
      chips.className = 'label-chips';
      for (const label of labels) {
        const chip = document.createElement('li');
        chip.textContent = label;
        chip.title = row.new_labels.includes(label) ? 'New label created for this review' : 'Reused existing label';
        chips.append(chip);
      }
      group.append(title, chips);
      container.append(group);
    }
  }
  const done = data.reviews.filter(row => labelCards.has(row.review_id)).length;
  el('label-state').textContent = data.available
    ? `Topic-label results · ${done}/${labelCards.size} reviews labeled for this paper · ${data.processed_reviews.toLocaleString()} reviews saved across the dataset · run ${data.status} · updated ${new Date(data.updated_at * 1000).toLocaleString()}`
    : 'Topic-label results are not available for this dataset.';
}
async function refreshLabels() {
  if (!current) return;
  const paper = current.paper;
  labelRequest?.abort();
  labelRequest = new AbortController();
  el('refresh-labels').disabled = true;
  try {
    const data = await get(`/api/labels?paper=${encodeURIComponent(paper)}`, labelRequest.signal);
    if (selected === paper) renderLabels(data);
  } catch (error) {
    if (error.name !== 'AbortError' && selected === paper) el('label-state').textContent = `Could not refresh topic-label results: ${error.message}`;
  } finally {
    if (selected === paper) el('refresh-labels').disabled = false;
  }
}
async function selectPaper() {
  const paper = location.hash.slice(1);
  if (!papers.includes(paper)) {
    if (papers.length) location.hash = papers[0];
    return;
  }
  selected = paper;
  request?.abort();
  labelRequest?.abort();
  labelCards.clear();
  request = new AbortController();
  current = null;
  el('download').disabled = true;
  el('refresh-labels').disabled = true;
  el('label-state').textContent = 'Loading topics and labels…';
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
    await refreshLabels();
  } catch (error) {
    if (error.name !== 'AbortError') { el('state').textContent = error.message; el('state').className = 'error'; }
  }
}
el('refresh-labels').onclick = refreshLabels;
setInterval(() => { if (!document.hidden) refreshLabels(); }, 30000);
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
