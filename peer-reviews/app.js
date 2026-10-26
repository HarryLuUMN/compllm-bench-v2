const DATA_URL = "./papers.jsonl";
const MANIFEST_URL = "./manifest.json";
const PAGE_SIZE = 12;

const state = {
  records: [],
  manifest: null,
  filtered: [],
  selectedDoi: null,
  page: 1,
  activeTab: "abstract",
};

const els = {
  totalRecords: document.querySelector("#totalRecords"),
  visibleRecords: document.querySelector("#visibleRecords"),
  reviewCoverage: document.querySelector("#reviewCoverage"),
  yearRange: document.querySelector("#yearRange"),
  searchInput: document.querySelector("#searchInput"),
  journalSelect: document.querySelector("#journalSelect"),
  yearSelect: document.querySelector("#yearSelect"),
  sortSelect: document.querySelector("#sortSelect"),
  clearFilters: document.querySelector("#clearFilters"),
  exportFiltered: document.querySelector("#exportFiltered"),
  candidateCount: document.querySelector("#candidateCount"),
  rejectedCount: document.querySelector("#rejectedCount"),
  validationStatus: document.querySelector("#validationStatus"),
  resultHeading: document.querySelector("#resultHeading"),
  recordList: document.querySelector("#recordList"),
  prevPage: document.querySelector("#prevPage"),
  nextPage: document.querySelector("#nextPage"),
  pageStatus: document.querySelector("#pageStatus"),
  emptyDetail: document.querySelector("#emptyDetail"),
  recordDetail: document.querySelector("#recordDetail"),
  detailJournal: document.querySelector("#detailJournal"),
  detailTitle: document.querySelector("#detailTitle"),
  detailMeta: document.querySelector("#detailMeta"),
  doiLink: document.querySelector("#doiLink"),
  reviewLink: document.querySelector("#reviewLink"),
  fullTextLink: document.querySelector("#fullTextLink"),
  tabAbstract: document.querySelector("#tab-abstract"),
  tabReview: document.querySelector("#tab-review"),
  tabFulltext: document.querySelector("#tab-fulltext"),
  tabMetadata: document.querySelector("#tab-metadata"),
};

async function boot() {
  const [records, manifest] = await Promise.all([loadRecords(), loadManifest()]);
  state.records = records;
  state.manifest = manifest;
  state.selectedDoi = readHashDoi() || null;

  fillFilterOptions();
  bindEvents();
  renderManifest();
  applyFilters();
}

async function loadRecords() {
  const response = await fetch(DATA_URL);
  if (!response.ok) {
    throw new Error(`Could not load ${DATA_URL}`);
  }
  const text = await response.text();
  return text
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

async function loadManifest() {
  const response = await fetch(MANIFEST_URL);
  if (!response.ok) {
    return null;
  }
  return response.json();
}

function fillFilterOptions() {
  const journals = unique(state.records.map((record) => record.journal)).sort();
  const years = unique(state.records.map((record) => String(record.year))).sort();
  setOptions(els.journalSelect, ["All journals", ...journals]);
  setOptions(els.yearSelect, ["All years", ...years]);
}

function setOptions(select, values) {
  select.replaceChildren(
    ...values.map((value, index) => {
      const option = document.createElement("option");
      option.value = index === 0 ? "" : value;
      option.textContent = value;
      return option;
    }),
  );
}

function bindEvents() {
  els.searchInput.addEventListener("input", () => {
    state.page = 1;
    applyFilters();
  });
  els.journalSelect.addEventListener("change", () => {
    state.page = 1;
    applyFilters();
  });
  els.yearSelect.addEventListener("change", () => {
    state.page = 1;
    applyFilters();
  });
  els.sortSelect.addEventListener("change", applyFilters);
  els.clearFilters.addEventListener("click", clearFilters);
  els.exportFiltered.addEventListener("click", exportFiltered);
  els.prevPage.addEventListener("click", () => changePage(-1));
  els.nextPage.addEventListener("click", () => changePage(1));

  document.querySelectorAll(".tab-button").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeTab = button.dataset.tab;
      renderTabs();
    });
  });

  window.addEventListener("hashchange", () => {
    const doi = readHashDoi();
    if (doi) {
      state.selectedDoi = doi;
      renderList();
      renderDetail();
    }
  });
}

function clearFilters() {
  els.searchInput.value = "";
  els.journalSelect.value = "";
  els.yearSelect.value = "";
  els.sortSelect.value = "date-desc";
  state.page = 1;
  applyFilters();
}

function applyFilters() {
  const query = els.searchInput.value.trim().toLowerCase();
  const journal = els.journalSelect.value;
  const year = els.yearSelect.value;

  state.filtered = state.records.filter((record) => {
    if (journal && record.journal !== journal) {
      return false;
    }
    if (year && String(record.year) !== year) {
      return false;
    }
    if (!query) {
      return true;
    }
    return searchableText(record).includes(query);
  });

  sortRecords(state.filtered, els.sortSelect.value);
  if (!state.filtered.some((record) => record.doi === state.selectedDoi)) {
    state.selectedDoi = state.filtered[0]?.doi || null;
  }
  clampPage();
  renderSummary();
  renderList();
  renderDetail();
}

function sortRecords(records, sortKey) {
  const byTitle = (a, b) => a.title.localeCompare(b.title);
  const byDate = (a, b) => String(a.publication_date).localeCompare(String(b.publication_date));

  records.sort((a, b) => {
    if (sortKey === "date-asc") {
      return byDate(a, b);
    }
    if (sortKey === "journal") {
      return (
        a.journal.localeCompare(b.journal) ||
        Number(a.year) - Number(b.year) ||
        byTitle(a, b)
      );
    }
    if (sortKey === "title") {
      return byTitle(a, b);
    }
    return byDate(b, a);
  });
}

function renderSummary() {
  const years = unique(state.records.map((record) => Number(record.year))).sort((a, b) => a - b);
  const withReviews = state.records.filter(
    (record) => record.official_review?.status === "collected",
  ).length;
  const coverage = state.records.length ? Math.round((withReviews / state.records.length) * 100) : 0;

  els.totalRecords.textContent = formatNumber(state.records.length);
  els.visibleRecords.textContent = formatNumber(state.filtered.length);
  els.reviewCoverage.textContent = `${coverage}%`;
  els.yearRange.textContent = years.length ? `${years[0]}-${years.at(-1)}` : "-";
  els.resultHeading.textContent = `${formatNumber(state.filtered.length)} records`;
}

function renderManifest() {
  const counts = state.manifest?.counts;
  els.candidateCount.textContent = counts ? formatNumber(counts.papers) : "-";
  els.rejectedCount.textContent = state.manifest ? formatNumber(Object.keys(state.manifest.journals || {}).length) : "-";
  els.validationStatus.textContent = "On demand";
}

function renderList() {
  if (!state.filtered.length) {
    els.recordList.innerHTML = `<div class="empty-list">No records match the current filters.</div>`;
    els.pageStatus.textContent = "0 / 0";
    els.prevPage.disabled = true;
    els.nextPage.disabled = true;
    return;
  }

  const pages = pageCount();
  const start = (state.page - 1) * PAGE_SIZE;
  const pageRecords = state.filtered.slice(start, start + PAGE_SIZE);

  els.recordList.replaceChildren(...pageRecords.map(recordCard));
  els.pageStatus.textContent = `${state.page} / ${pages}`;
  els.prevPage.disabled = state.page <= 1;
  els.nextPage.disabled = state.page >= pages;
}

function recordCard(record) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `record-card${record.doi === state.selectedDoi ? " active" : ""}`;
  button.addEventListener("click", () => selectRecord(record.doi));

  button.innerHTML = `
    <div class="record-meta">
      <span class="pill journal">${escapeHtml(record.journal)}</span>
      <span class="pill">${escapeHtml(String(record.year))}</span>
      <span class="pill review">${escapeHtml(reviewLabel(record))}</span>
    </div>
    <h3>${escapeHtml(displayText(record.title))}</h3>
    <p>${escapeHtml(displayText(record.abstract || record.official_review?.snippet || ""))}</p>
  `;
  return button;
}

function selectRecord(doi) {
  state.selectedDoi = doi;
  window.history.replaceState(null, "", `#${encodeURIComponent(doi)}`);
  renderList();
  renderDetail();
}

async function renderDetail() {
  const record = selectedRecord();
  if (!record) {
    els.emptyDetail.hidden = false;
    els.recordDetail.hidden = true;
    return;
  }

  els.emptyDetail.hidden = true;
  els.recordDetail.hidden = false;
  els.detailJournal.textContent = `${record.journal} · ${record.year}`;
  els.detailTitle.textContent = displayText(record.title);
  els.detailMeta.textContent = [
    record.publication_date,
    record.article_type,
    record.doi,
    `${record.authors?.length || 0} authors`,
  ]
    .filter(Boolean)
    .join(" · ");

  setLink(els.doiLink, record.source_urls?.landing_page || doiUrl(record.doi));
  setLink(els.reviewLink, record.source_urls?.official_review || record.official_review?.source_url);
  setLink(els.fullTextLink, record.source_urls?.full_text || record.full_text?.source_url);

  if (record.detail_url && !record.detailsLoaded) {
    els.tabAbstract.innerHTML = paragraphs(record.abstract || "");
    els.tabReview.textContent = "Loading official review…";
    els.tabFulltext.textContent = "Loading full text…";
    els.tabMetadata.innerHTML = metadataTable(record);
    renderTabs();
    try {
      if (!record.detailsPromise) {
        record.detailsPromise = fetch(record.detail_url).then(async (response) => {
          if (!response.ok) throw new Error("Could not load paper details");
          const stream = response.body.pipeThrough(new DecompressionStream("gzip"));
          return new Response(stream).json();
        }).then((detail) => {
          record.official_review = {...record.official_review, ...detail.official_review};
          record.full_text = {...record.full_text, ...detail.full_text};
          record.detailsLoaded = true;
        }).catch((error) => { delete record.detailsPromise; throw error; });
      }
      await record.detailsPromise;
    } catch (error) {
      if (state.selectedDoi === record.doi) {
        els.tabReview.textContent = "Details could not be loaded. Select this paper again to retry, or open its publisher link.";
        els.tabFulltext.textContent = els.tabReview.textContent;
      }
      return;
    }
    if (state.selectedDoi !== record.doi) return;
  }
  els.tabAbstract.innerHTML = paragraphs(record.abstract || "");
  els.tabReview.innerHTML = paragraphs(record.official_review?.text || "");
  els.tabFulltext.innerHTML = paragraphs(record.full_text?.text || "");
  els.tabMetadata.innerHTML = metadataTable(record);
  renderTabs();
}

function renderTabs() {
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === state.activeTab);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${state.activeTab}`);
  });
}

function metadataTable(record) {
  const rows = [
    ["DOI", record.doi],
    ["Journal", record.journal],
    ["Published", record.publication_date],
    ["Article type", record.article_type],
    ["License", record.license],
    ["Review kind", record.official_review?.kind],
    ["Review raw path", record.official_review?.raw_path],
    ["Full text raw path", record.full_text?.raw_path],
    ["Authors", (record.authors || []).join(", ")],
  ];

  const tableRows = rows
    .filter(([, value]) => value)
    .map(
      ([key, value]) => `
        <tr>
          <th>${escapeHtml(key)}</th>
          <td>${escapeHtml(String(value))}</td>
        </tr>
      `,
    )
    .join("");

  const json = escapeHtml(JSON.stringify(record.source_urls || {}, null, 2));
  return `
    <table class="data-table"><tbody>${tableRows}</tbody></table>
    <h3>Source URLs</h3>
    <pre>${json}</pre>
  `;
}

function paragraphs(text) {
  const chunks = displayText(text)
    .split(/\n{2,}/)
    .map((chunk) => chunk.trim())
    .filter(Boolean);
  if (!chunks.length) {
    return `<div class="empty-list">No text available.</div>`;
  }
  return chunks.map((chunk) => `<p>${escapeHtml(chunk)}</p>`).join("");
}

function setLink(link, url) {
  if (url) {
    link.setAttribute("href", url);
    link.removeAttribute("aria-disabled");
  } else {
    link.href = "#";
    link.setAttribute("aria-disabled", "true");
  }
}

function changePage(delta) {
  state.page += delta;
  clampPage();
  renderList();
}

function clampPage() {
  state.page = Math.min(Math.max(state.page, 1), pageCount());
}

function pageCount() {
  return Math.max(1, Math.ceil(state.filtered.length / PAGE_SIZE));
}

function selectedRecord() {
  return state.records.find((record) => record.doi === state.selectedDoi) || null;
}

function searchableText(record) {
  return [
    record.title,
    record.doi,
    record.journal,
    record.year,
    record.abstract,
    record.official_review?.snippet,
    record.full_text?.snippet,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function reviewLabel(record) {
  const kind = record.official_review?.kind || "official review";
  return kind.replaceAll("_", " ");
}

function exportFiltered() {
  const payload = state.filtered.map((record) => JSON.stringify(Object.fromEntries(Object.entries(record).filter(([key]) => !["detailsPromise", "detailsLoaded"].includes(key))))).join("\n");
  const blob = new Blob([payload, "\n"], { type: "application/x-ndjson" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "filtered-papers.jsonl";
  link.click();
  URL.revokeObjectURL(url);
}

function readHashDoi() {
  return window.location.hash ? decodeURIComponent(window.location.hash.slice(1)) : "";
}

function doiUrl(doi) {
  return doi ? `https://doi.org/${doi}` : "";
}

function unique(values) {
  return [...new Set(values.filter((value) => value !== undefined && value !== null && value !== ""))];
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(value);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function displayText(value) {
  const template = document.createElement("template");
  template.innerHTML = String(value || "");
  return (template.content.textContent || template.innerHTML || "").trim();
}

boot().catch((error) => {
  renderLoadError(error);
});

function renderLoadError(error) {
  els.resultHeading.textContent = "Dataset unavailable";
  els.recordList.innerHTML = `
    <div class="empty-list">
      ${escapeHtml(error.message)}
      <br />
      Please reload the page to retry loading the published dataset.
    </div>
  `;
  els.totalRecords.textContent = "0";
  els.visibleRecords.textContent = "0";
  els.reviewCoverage.textContent = "0%";
  els.yearRange.textContent = "-";
  els.prevPage.disabled = true;
  els.nextPage.disabled = true;
  els.pageStatus.textContent = "0 / 0";
  els.emptyDetail.hidden = false;
  els.recordDetail.hidden = true;
}
