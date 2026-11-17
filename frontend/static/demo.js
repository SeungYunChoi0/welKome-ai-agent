/* 시연 탭: 저장해 둔 질문 기록(demo_runs.json)을 휴대폰 대화와 검색 패널에 다시 재생하고, 끝나면 처음부터 반복한다.
 * - 서버나 모델을 부르지 않는다. 패널은 pipeline.js를 그대로 쓰고, 이 파일이 기록을 스트림처럼 흘려 준다.
 * - 관람객이 챗봇에서 직접 질문하면 script.js가 demoPlayer.stop()을 먼저 불러 재생을 빠르게 끝낸다.
 * - 화면을 IDLE_MS 동안 아무도 만지지 않으면 시연 탭으로 넘어간다.
 */
(() => {
  const IDLE_MS = 60000;        // 손대지 않으면 시연 탭으로 넘어가는 시간
  const PAUSE_MS = 5000;        // 답변을 보여 준 뒤 다음 질문까지
  const TYPE_MS = 75;           // 질문 한 글자 입력 간격
  const MAX_GAP_MS = 2500;      // 기록 속 대기 시간이 이보다 길면 줄여서 재생(표시되는 경과 시간은 기록 그대로)
  const GREETING = '안녕하세요! 한국의 명소, 맛집, 조선시대 왕들에 대해 물어보세요.';

  const $ = id => document.getElementById(id);
  const realSetTimeout = window.setTimeout.bind(window);
  let fast = false;             // true면 남은 재생을 기다리지 않고 바로 끝낸다
  const wait = ms => new Promise(resolve => realSetTimeout(resolve, fast ? 0 : ms));

  let data = null, index = 0, current = null, replay = null;
  const stats = {completed: 0, rounds: 0, errors: 0};

  /* ── 기록을 /chat/stream 응답처럼 흘려 주기 ── */
  const nextFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const path = new URL(typeof input === 'string' ? input : input.url, location.href).pathname;
    if (path !== '/chat/stream' || !replay) return nextFetch(input, init);
    const run = replay;
    replay = null;
    const encoder = new TextEncoder();
    const body = new ReadableStream({
      async start(controller) {
        let last = 0;
        for (const event of run.events) {
          const at = Number(event.elapsed_ms) || last;
          await wait(Math.min(Math.max(at - last, 0), MAX_GAP_MS));
          last = at;
          controller.enqueue(encoder.encode(`data: ${JSON.stringify({...event, type: 'progress'})}\n\n`));
        }
        await wait(120);
        controller.enqueue(encoder.encode(`data: ${JSON.stringify({
          type: 'result', output: run.output, elapsed_ms: run.elapsed_ms || last,
          chat_history: [{role: 'human', content: run.question}, {role: 'ai', content: run.output}],
        })}\n\n`));
        controller.close();
      },
    });
    return Promise.resolve(new Response(body, {status: 200, headers: {'Content-Type': 'text/event-stream'}}));
  };

  /* ── 휴대폰 시연 화면 ── */
  function bubble(role, text) {
    const win = $('demo-window');
    let el;
    if (role === 'user') {
      el = document.createElement('div');
      el.className = 'chat-bubble user';
      el.textContent = text;
      win.appendChild(el);
    } else {
      const wrap = document.createElement('div');
      const meta = document.createElement('div');
      meta.className = 'chat-meta';
      meta.textContent = '📜 실록이';
      el = document.createElement('div');
      el.className = 'chat-bubble bot';
      if (text === null) el.innerHTML = '<div class="spinner" style="width: 20px; height: 20px; border-width: 2px; margin: 0 auto;"></div>';
      else el.textContent = text;
      wrap.append(meta, el);
      win.appendChild(wrap);
    }
    scroll();
    return el;
  }
  function scroll() {
    const box = $('demo-scroller');
    if (box) box.scrollTo({top: box.scrollHeight, behavior: fast ? 'auto' : 'smooth'});
  }
  function setCount(n) {
    const total = data ? data.runs.length : 0;
    $('demo-count').textContent = total ? `${n} / ${total}` : '';
  }
  async function typeQuestion(text) {
    const box = $('demo-typing');
    box.textContent = '';
    box.classList.add('typing');
    for (const ch of text) {
      if (fast) break;
      box.textContent += ch;
      box.scrollLeft = box.scrollWidth;   // 긴 질문은 입력 중인 끝부분이 보이게
      await wait(TYPE_MS);
    }
    box.textContent = text;
    box.scrollLeft = box.scrollWidth;
    await wait(500);
    box.classList.remove('typing');
    box.textContent = '';
  }
  function resetRound() {
    $('demo-window').replaceChildren();
    bubble('bot', GREETING);
  }

  /* ── 한 질문 재생 ── */
  async function playOne(run, n) {
    setCount(n);
    await typeQuestion(run.question);
    bubble('user', run.question);
    const answer = bubble('bot', null);
    replay = run;
    try {
      const result = await window.pipelineDemo.ask(run.question, []);
      answer.textContent = result.output;
      stats.completed += 1;
    } catch (error) {
      replay = null;
      answer.textContent = '시연 기록을 재생하지 못했습니다.';
      stats.errors += 1;
    }
    scroll();
    document.documentElement.dataset.demoDone = String(stats.completed);
    document.documentElement.dataset.demoErrors = String(stats.errors);
  }

  /* ── 반복 ── */
  const demoActive = () => $('screen-demo') && $('screen-demo').classList.contains('active');
  const userBusy = () => (typeof state !== 'undefined' && state.isBotTyping);   // script.js의 전역 상태
  async function loop() {
    resetRound();
    while (true) {
      if (!demoActive() || userBusy()) { await wait(300); continue; }
      const run = data.runs[index];
      current = playOne(run, index + 1);
      await current;
      current = null;
      index = (index + 1) % data.runs.length;
      await wait(PAUSE_MS);
      if (index === 0) {
        stats.rounds += 1;
        document.documentElement.dataset.demoRounds = String(stats.rounds);
        resetRound();
      }
    }
  }

  /* 관람객이 직접 질문하기 전에 부른다: 재생 중이면 남은 부분을 바로 끝낸다 */
  async function stop() {
    if (!current) return;
    fast = true;
    try { await current; } finally { fast = false; }
  }

  /* ── 손대지 않으면 시연 탭으로 ── */
  let lastTouch = Date.now();
  ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart'].forEach(type =>
    window.addEventListener(type, () => { lastTouch = Date.now(); }, {passive: true, capture: true}));
  setInterval(() => {
    if (Date.now() - lastTouch < IDLE_MS || demoActive() || userBusy()) return;
    if ($('screen-loading') && $('screen-loading').classList.contains('active')) return;
    const button = document.querySelector('.screen.active .nav-item[data-target="demo"]');
    if (button) { button.click(); lastTouch = Date.now(); }
  }, 1000);

  /* ── 시작 ── */
  document.addEventListener('DOMContentLoaded', async () => {
    try {
      const response = await nextFetch('/static/demo_runs.json', {cache: 'no-store'});
      data = await response.json();
      if (!data.runs || !data.runs.length) throw new Error('기록 없음');
    } catch (error) {
      $('demo-count').textContent = '시연 기록 없음';
      return;
    }
    document.documentElement.classList.toggle('demo-sample', Boolean(data.sample));
    setCount(1);
    loop();
  });

  window.demoPlayer = {stop, stats};
})();
