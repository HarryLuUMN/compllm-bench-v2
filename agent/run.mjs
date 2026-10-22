import { readFile, writeFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';
import { resolve, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createTools, python } from './tools.mjs';

const here = fileURLToPath(new URL('.', import.meta.url));
const { values } = parseArgs({ options: {
  folder: { type: 'string', default: resolve(here, '../paper_database') },
  prompt: { type: 'string', default: resolve(here, '../prompts/paper_assistant.yaml') },
  question: { type: 'string' }, 'question-file': { type: 'string' }, output: { type: 'string' }, trace: { type: 'string' }, provider: { type: 'string' }, model: { type: 'string' },
  'agent-dir': { type: 'string', default: resolve(here, '../../pi-agent/agent') },
  check: { type: 'boolean' }, help: { type: 'boolean' },
} });
if (values.help) {
  console.log('node new-codebase/agent/run.mjs --folder PATH --question TEXT [--provider NAME --model ID] [--prompt YAML] [--agent-dir PATH] [--check]');
  process.exit(0);
}
const prompt = await python(['prompt', resolve(values.prompt)]);
const tools = await createTools(resolve(values.folder));
const sdkPath = process.env.PI_SDK_PATH || resolve(here, '../../pi-agent/node_modules/@earendil-works/pi-coding-agent/dist/index.js');
const { createAgentSession, DefaultResourceLoader, SessionManager, SettingsManager, ModelRuntime } = await import(pathToFileURL(sdkPath).href);
if (values.check) {
  console.log(JSON.stringify({ prompt: prompt.name, tools: tools.map(tool => tool.name),
    pdfs: JSON.parse((await tools[0].execute('', {})).content[0].text), sdk: 'loaded' }, null, 2));
  process.exit(0);
}
if (values.question && values['question-file']) throw new Error('Choose --question or --question-file');
const question = values['question-file'] ? await readFile(resolve(values['question-file']), 'utf8') : values.question;
if (!question?.trim()) throw new Error('--question or --question-file is required');
if (Boolean(values.provider) !== Boolean(values.model)) throw new Error('Specify --provider and --model together');
const agentDir = resolve(values['agent-dir']);
const runtime = await ModelRuntime.create({ authPath: join(agentDir, 'auth.json'), modelsPath: join(agentDir, 'models.json') });
const model = values.model ? runtime.getModel(values.provider, values.model) : undefined;
if (values.model && !model) throw new Error(`Unknown model: ${values.provider}/${values.model}`);
const settings = SettingsManager.inMemory();
const loader = new DefaultResourceLoader({ cwd: resolve(values.folder), agentDir, settingsManager: settings,
  noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
  systemPromptOverride: () => prompt.system, appendSystemPromptOverride: () => [],
});
await loader.reload();
const { session } = await createAgentSession({ cwd: resolve(values.folder), agentDir,
  modelRuntime: runtime, ...(model ? { model } : {}), resourceLoader: loader,
  tools: tools.map(tool => tool.name), customTools: tools,
  sessionManager: SessionManager.inMemory(), settingsManager: settings,
});
const trace = [];
try {
  session.subscribe(event => {
    if (event.type === 'message_update' && event.assistantMessageEvent.type === 'text_delta') {
      process.stdout.write(event.assistantMessageEvent.delta);
    }
    if (event.type === 'tool_execution_start') console.error(`[tool] ${event.toolName}`);
    if (event.type === 'tool_execution_end') trace.push({ tool: event.toolName, success: !event.isError });
  });
  await session.prompt(question);
  const last = session.messages.filter(message => message.role === 'assistant').at(-1);
  if (last?.stopReason === 'error' || last?.stopReason === 'aborted') {
    throw new Error(last.errorMessage || `Model stopped: ${last.stopReason}`);
  }
  const response = last?.content?.filter(block => block.type === 'text').map(block => block.text).join('\n').trim();
  if (!response) throw new Error('Model returned no final text');
  if (values.output) await writeFile(resolve(values.output), response + '\n');
  console.log();
} finally {
  if (values.trace) await writeFile(resolve(values.trace), JSON.stringify(trace, null, 2) + '\n');
  session.dispose();
}
