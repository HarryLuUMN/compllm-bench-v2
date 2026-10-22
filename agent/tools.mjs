import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { realpath, readdir } from 'node:fs/promises';
import { relative, isAbsolute, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const execute = promisify(execFile);
const helper = fileURLToPath(new URL('./pdf_reader.py', import.meta.url));
const string = { type: 'string', minLength: 1 };
const integer = { type: 'integer', minimum: 1 };
const object = (properties, required = []) => ({ type: 'object', properties, required, additionalProperties: false });
const result = (value) => ({ content: [{ type: 'text', text: JSON.stringify(value) }], details: {} });

export async function python(args, signal) {
  const { stdout } = await execute(process.env.PYTHON || 'python3', [helper, ...args], {
    timeout: 60000, maxBuffer: 2 * 1024 * 1024, signal,
  });
  return JSON.parse(stdout);
}

export async function createTools(folder, { fetchImpl = fetch, apiKey = process.env.YDC_API_KEY || process.env.YOU_COM_API_KEY, braveApiKey = process.env.BRAVE_SEARCH_API_KEY } = {}) {
  const root = await realpath(folder);
  return [
    {
      name: 'list_pdfs', label: 'List PDFs', description: 'List PDF paths beneath the selected folder; paginate with offset.',
      parameters: object({ offset: { type: 'integer', minimum: 0 } }),
      async execute(_id, { offset = 0 }) {
        const files = [];
        async function walk(directory) {
          for (const entry of await readdir(directory, { withFileTypes: true })) {
            if (entry.isSymbolicLink()) continue;
            const path = join(directory, entry.name);
            if (entry.isDirectory()) await walk(path);
            else if (entry.isFile() && entry.name.toLowerCase().endsWith('.pdf')) files.push(relative(root, path));
          }
        }
        await walk(root);
        files.sort();
        return result({ total: files.length, files: files.slice(offset, offset + 100),
          next_offset: offset + 100 < files.length ? offset + 100 : null });
      },
    },
    {
      name: 'read_pdf', label: 'Read PDF pages',
      description: 'Extract local PDF text with page numbers. Read up to 10 pages per call; default 5. Cannot interpret figures or scanned images.',
      parameters: object({ file: string, start_page: integer, end_page: integer }, ['file']),
      async execute(_id, { file, start_page = 1, end_page }, signal) {
        const path = await realpath(join(root, file));
        const rel = relative(root, path);
        if (isAbsolute(file) || rel === '..' || rel.startsWith('../') || isAbsolute(rel)) {
          throw new Error('PDF must be inside the selected folder');
        }
        const args = ['pdf', rel, '--root', root, '--start', String(start_page)];
        if (end_page !== undefined) args.push('--end', String(end_page));
        return result(await python(args, signal));
      },
    },
    {
      name: 'web_search', label: 'Search the web', description: 'Search the web for external evidence; returns URLs, descriptions and snippets.',
      parameters: object({ query: { ...string, maxLength: 500 } }, ['query']),
      async execute(_id, { query }, signal) {
        if (!apiKey && !braveApiKey) throw new Error('Set YDC_API_KEY or BRAVE_SEARCH_API_KEY to enable web_search');
        const url = apiKey ? 'https://ydc-index.io/v1/search'
          : 'https://api.search.brave.com/res/v1/web/search?' + new URLSearchParams({ q: query, count: '5' });
        const options = apiKey ? { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
          body: JSON.stringify({ query, count: 5 }) }
          : { method: 'GET', headers: { Accept: 'application/json', 'X-Subscription-Token': braveApiKey } };
        const response = await fetchImpl(url, { ...options,
          signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000),
        });
        if (!response.ok) throw new Error(`Web search failed: HTTP ${response.status}`);
        const data = await response.json();
        return result({ provider: apiKey ? 'you.com' : 'brave', results: (apiKey ? data.results?.web || [] : data.web?.results || []).slice(0, 5).map(item => ({
          title: item.title, url: item.url, description: String(item.description || '').slice(0, 2000),
          snippets: (item.snippets || item.extra_snippets || []).slice(0, 3).map(text => String(text).slice(0, 2000)),
        })) });
      },
    },
  ];
}
