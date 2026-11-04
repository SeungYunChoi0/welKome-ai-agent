/* DOM-free frontend checks: real stream parser, UTF-8 fragmentation and terminal states. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor() {
    this.children = []; this.dataset = {}; this.textContent = ''; this.scrollHeight = 0;
    this.style = {setProperty() {}};
    this.classList = {add() {}, remove() {}};
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener() {}
  set innerHTML(_) { throw new Error('Search data must use textContent'); }
}
function context(frames, options = {}) {
  const elements = new Map();
  const listeners = new Map();
  let cancelled = false;
  const bytes = new TextEncoder().encode(frames.join(''));
  let offset = 0;
  const document = {
    getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
    createElement() { return new Element(); },
    addEventListener(type, callback) { listeners.set(type, callback); },
  };
  const sandbox = {document, window: {}, TextDecoder, AbortController, performance, setTimeout, clearTimeout, setInterval, clearInterval,
    fetch: async () => ({ok: options.ok !== false, body: {getReader: () => ({
      async read() {
        if (offset >= bytes.length) return {done: true};
        // Deliberately cut through Korean UTF-8 characters and SSE delimiters.
        const end = Math.min(offset + 5, bytes.length);
        const value = bytes.slice(offset, end); offset = end;
        return {value, done: false};
      },
      async cancel() { cancelled = true; }, releaseLock() {},
    })}}),
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../frontend/static/pipeline.js'), 'utf8'), sandbox);
  listeners.get('DOMContentLoaded')();
  return {sandbox, elements, cancelled: () => cancelled};
}
const frame = event => `data: ${JSON.stringify(event)}\n\n`;
(async () => {
  const history = [{role: 'human', content: '훈민정음'}];
  const success = context([
    ': heartbeat\n\n',
    frame({type: 'progress', stage: 'route', status: 'done', title: '메타데이터 필터 검색', route: 'B', elapsed_ms: 10}),
    frame({type: 'progress', stage: 'evidence', status: 'done', title: '전달 기록', count: 1, documents: [{id: 'test', rank: 1, title: '<script>검증</script>', text: '<img onerror=bad()>실록'}], elapsed_ms: 20}),
    frame({type: 'result', output: '한글 답변', chat_history: history, elapsed_ms: 30}),
  ]);
  const result = await success.sandbox.window.pipelineDemo.ask('훈민정음', []);
  assert.equal(result.output, '한글 답변');
  assert.equal(result.chat_history[0].content, '훈민정음');
  assert.equal(success.elements.get('pipeline-status').textContent, 'COMPLETE');
  assert.equal(success.elements.get('pipeline-events').children.length, 2);
  assert.equal(success.elements.get('pipeline-rail').children[2].dataset.status, 'skipped');
  const card = success.elements.get('pipeline-records').children[0];
  assert.equal(card.children[0].children[1].textContent, '<script>검증</script>');
  assert.equal(card.children[1].textContent, '<img onerror=bad()>실록');
  assert(success.cancelled());
  const interrupted = context([frame({type: 'progress', stage: 'model', status: 'running', title: '질문 분석', elapsed_ms: 1})]);
  await assert.rejects(interrupted.sandbox.window.pipelineDemo.ask('질문', []), /연결이 종료/);
  assert.equal(interrupted.elements.get('pipeline-status').textContent, 'ERROR');
  const error = context([frame({type: 'error', message: '검증 오류'})]);
  await assert.rejects(error.sandbox.window.pipelineDemo.ask('질문', []), /검증 오류/);
  const unavailable = context([], {ok: false});
  await assert.rejects(unavailable.sandbox.window.pipelineDemo.ask('질문', []), /서버에 연결/);
  assert.equal(unavailable.elements.get('pipeline-status').textContent, 'ERROR');
  console.log('Frontend checks passed: fragmented UTF-8/SSE, records, skipped stages, safe text, disconnect, server error, HTTP error.');
})().catch(error => { console.error(error); process.exitCode = 1; });
