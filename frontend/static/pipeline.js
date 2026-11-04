/* Real backend events only: animation does not synthesize search steps or results. */
(() => {
  const stages = [
    ['question', '01', '질문 분석'], ['retrieval', '02', '실록 검색'],
    ['ranking', '03', '결과 정리'], ['evidence', '04', '근거 전달'], ['generation', '05', '답변 생성'],
  ];
  const $ = id => document.getElementById(id);
  const make = (tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined) el.textContent = text;
    return el;
  };
  let started = 0, timer, running = false, nodes = new Map(), seenIds = new Set();
  let previousGroup = null, evidenceCount = 0, hasEvidence = false;
  const groupFor = event => {
    if (['request', 'query', 'route'].includes(event.stage)) return 'question';
    if (['fusion', 'sort'].includes(event.stage)) return 'ranking';
    if (event.stage === 'evidence') return 'evidence';
    if (event.stage === 'model') return event.turn > 1 ? 'generation' : 'question';
    if (event.stage === 'tool') return event.status === 'done' ? null : 'retrieval';
    return 'retrieval';
  };
  function setGroup(group, status) {
    if (!group) return;
    const node = nodes.get(group);
    node.dataset.status = status;
  }
  function initialize() {
    $('pipeline-rail').replaceChildren();
    nodes = new Map();
    for (const [key, n, title] of stages) {
      const node = make('div', 'pipeline-step');
      node.dataset.status = 'waiting';
      node.append(make('span', 'pipeline-step-number', n), make('span', '', title));
      $('pipeline-rail').append(node);
      nodes.set(key, node);
    }
  }
  function describe(event) {
    const details = [];
    if (event.query) details.push(`검색어: ${event.query}`);
    if (event.filters && Object.keys(event.filters).length) {
      const names = {king_name: '왕', king_order: '왕 대수', year_solar: '서기', year_reign: '재위', month: '월', month_int: '월'};
      details.push(Object.entries(event.filters).map(([key, value]) => `${names[key] || key}: ${value}`).join(' · '));
    }
    if (event.tokens) details.push(event.tokens.length ? `추출어: ${event.tokens.join(' · ')}` : '추출어 없음');
    if (event.keywords) details.push(`키워드: ${event.keywords}`);
    if (event.count !== undefined) details.push(`${event.count}개 기록${event.documents && event.count > event.documents.length ? ` · ${event.documents.length}개 미리보기` : ''}`);
    if (event.stage === 'route') {
      if (event.route === 'A') details.push('본문 키워드 매칭 + 벡터 검색 · 결과에 따라 필터 스캔으로 전환');
      if (event.route === 'B') details.push('왕·연도 등 조건으로 기록 스캔 · OKT/BM25와 RRF는 실행 시에만 표시');
      if (event.route === 'C') details.push('FAISS MMR + BM25 · RRF로 순위 통합');
    }
    if (event.stage === 'fusion') details.push('두 검색 목록의 순위를 결합합니다');
    return details.join('\n');
  }
  function showRecords(event) {
    if (!event.documents) return;
    if (event.status === 'done' && event.stage === 'evidence') {
      hasEvidence = true;
      evidenceCount = event.count;
    }
    $('pipeline-records-title').textContent = event.stage === 'evidence' ? `모델에 전달한 근거 · ${event.count}개` : event.title;
    $('pipeline-records-note').textContent = event.count > event.documents.length
      ? `총 ${event.count}개 중 ${event.documents.length}개 표시 · 카드 번호는 현재 목록의 순서입니다.`
      : '카드 번호는 현재 목록의 순서입니다. 펼치면 본문 일부를 확인할 수 있습니다.';
    const nextIds = new Set();
    const cards = event.documents.map(doc => {
      nextIds.add(doc.id);
      const card = make('details', 'pipeline-record' + (seenIds.has(doc.id) ? ' retained' : ''));
      card.style.setProperty('--delay', `${Math.min(doc.rank - 1, 7) * 45}ms`);
      const summary = make('summary');
      summary.append(make('span', 'record-rank', String(doc.rank).padStart(2, '0')), make('span', 'record-title', doc.title));
      card.append(summary, make('p', 'record-excerpt', doc.text), make('small', 'record-id', `기록 ${doc.id} · 본문 일부`));
      return card;
    });
    $('pipeline-records').replaceChildren(...cards);
    if (!cards.length) $('pipeline-records').append(make('p', 'pipeline-empty', '조건에 맞는 기록이 없습니다.'));
    seenIds = nextIds;
  }
  function progress(event) {
    const group = groupFor(event);
    if (previousGroup && group && previousGroup !== group && nodes.get(previousGroup).dataset.status === 'running') {
      setGroup(previousGroup, 'done');
    }
    if (group) { setGroup(group, event.status === 'error' ? 'error' : event.status === 'warning' ? 'running' : event.status); previousGroup = group; }
    // Running and completion are separate observations, so fast stages stay visible.
    const row = make('li', 'pipeline-event');
    row.dataset.status = event.status;
    const head = make('div', 'event-head');
    head.append(make('span', 'event-dot'), make('strong', '', event.title), make('time', '', `${(event.elapsed_ms / 1000).toFixed(2)}s`));
    row.append(head);
    const details = describe(event);
    if (details) row.append(make('p', 'event-detail', details));
    $('pipeline-events').append(row);
    const scroller = $('pipeline-events');
    // Keep events bounded over multi-tool calls; card and text rendering use textContent.
    if (scroller.children.length > 120) scroller.firstElementChild.remove();
    scroller.scrollTop = scroller.scrollHeight;
    showRecords(event);
  }
  function finish(ok, elapsed) {
    clearInterval(timer);
    running = false;
    $('pipeline-panel').classList.remove('is-running');
    $('pipeline-status').textContent = ok ? 'COMPLETE' : 'ERROR';
    $('pipeline-status').dataset.state = ok ? 'done' : 'error';
    if (elapsed !== undefined) $('pipeline-clock').textContent = `${(elapsed / 1000).toFixed(2)}초`;
    for (const [key, node] of nodes) {
      if (node.dataset.status === 'running') node.dataset.status = ok ? 'done' : 'error';
      if (ok && node.dataset.status === 'waiting') {
        node.dataset.status = 'skipped';
        node.title = '이번 질문에서는 실행되지 않은 단계';
      }
    }
    if (ok) setGroup('generation', 'done');
  }
  async function ask(question, history) {
    if (running) throw new Error('이미 처리 중입니다.');
    running = true;
    initialize();
    previousGroup = null; seenIds = new Set(); hasEvidence = false; evidenceCount = 0;
    $('pipeline-events').replaceChildren(); $('pipeline-records').replaceChildren();
    $('pipeline-records-title').textContent = '검색된 실록 기록';
    $('pipeline-records-note').textContent = '검색 도구의 결과를 기다리고 있습니다.';
    $('pipeline-question').textContent = question;
    $('pipeline-status').textContent = 'RUNNING'; $('pipeline-status').dataset.state = 'running';
    $('pipeline-panel').classList.add('is-running');
    started = performance.now();
    $('pipeline-clock').textContent = '0.0초';
    timer = setInterval(() => { $('pipeline-clock').textContent = `${((performance.now() - started) / 1000).toFixed(1)}초`; }, 100);
    const controller = new AbortController();
    // A stalled backend cannot leave the demo locked forever.
    const timeout = setTimeout(() => controller.abort(), 300000);
    let reader, result;
    try {
      const response = await fetch('/chat/stream', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({input: question, chat_history: history}), signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error('서버에 연결하지 못했습니다.');
      reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      function frame(text) {
        const payload = text.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
        if (!payload) return;
        const event = JSON.parse(payload);
        if (event.type === 'progress') progress(event);
        if (event.type === 'error') throw new Error(event.message);
        if (event.type === 'result') {
          result = event;
          if (!hasEvidence) $('pipeline-records-note').textContent = '이번 요청에서는 실록 검색 결과가 전달되지 않았습니다.';
          else if (!evidenceCount) $('pipeline-records-note').textContent = '실록 검색 결과가 없어 기록이 전달되지 않았습니다.';
          finish(true, event.elapsed_ms);
        }
      }
      while (!result) {
        const {value, done} = await reader.read();
        buffer += done ? decoder.decode() : decoder.decode(value, {stream: true});
        buffer = buffer.replace(/\r\n/g, '\n');
        let boundary;
        while ((boundary = buffer.indexOf('\n\n')) !== -1) {
          frame(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2);
        }
        if (done) break;
      }
      if (!result) throw new Error('응답이 완료되기 전에 연결이 종료되었습니다.');
      return result;
    } catch (error) {
      const message = error.name === 'AbortError' ? '응답 시간이 초과되었습니다. 다시 질문해 주세요.' : error.message;
      progress({stage: 'connection', status: 'error', title: message, elapsed_ms: performance.now() - started});
      finish(false);
      throw error;
    } finally {
      clearTimeout(timeout); clearInterval(timer);
      if (reader) { try { await reader.cancel(); } catch (_) {} reader.releaseLock(); }
      running = false;
    }
  }
  document.addEventListener('DOMContentLoaded', () => {
    initialize();
    $('pipeline-fullscreen').addEventListener('click', async () => {
      try {
        if (document.fullscreenElement) await document.exitFullscreen();
        else await document.documentElement.requestFullscreen();
      } catch (_) { $('pipeline-fullscreen').textContent = '브라우저 전체 화면 사용'; }
    });
    document.addEventListener('fullscreenchange', () => {
      $('pipeline-fullscreen').textContent = document.fullscreenElement ? '전체 화면 닫기' : '전체 화면';
    });
  });
  window.pipelineDemo = {ask};
})();
