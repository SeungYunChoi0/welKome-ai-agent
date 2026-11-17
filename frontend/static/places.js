/* 홈 화면 「장소 사진 고르기」: 장소 사진을 옆으로 넘겨 고르면 아래 설명 카드와 실록 카드에 그 장소 정보가 나온다.
 * - 정보는 미리 받아 둔 places.json(tools/record_places.py)에서 읽는다. 서버를 부르지 않는다(부스에서 즉시 · 무료).
 * - 기록이 없는 장소는 짧은 설명만 보여 주고 실록 카드는 숨긴다.
 * - 움직임: 슬라이드 위치(pos)를 연속값으로 두고 매 프레임 각 사진의 위치 · 크기 · 흐림을 계산한다.
 *   끝에서 처음으로 이어지도록 거리는 늘 가장 가까운 쪽으로 잰다.
 */
(() => {
  const SLIDE_MS = 460;        // 한 칸 넘어가는 시간(ease-out)
  const SETTLE_MS = 400;       // 멈춘 뒤 정보를 바꾸기까지
  const FADE_MS = 220;         // 카드 글이 바뀔 때 흐려졌다 돌아오는 시간
  const STEP = 0.88;           // 옆 사진 중심까지 거리(사진 폭 기준, 가운데 사진 뒤로 조금 겹침)
  const SIDE_SCALE = 0.82;     // 옆 사진 크기
  const DRAG_PX = 36;          // 이만큼 밀면 넘어간다

  const $ = id => document.getElementById(id);
  const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  let places = [], slides = [], dots = [];
  let pos = 0;                 // 지금 보이는 위치(연속값)
  let current = 0;             // 정보가 나와 있는 장소
  let anim = null, settleTimer = null, swapTimer = null;

  /* ── 안전하게 글 넣기: <br> <b> <strong> 만 살리고 나머지는 글자로 ── */
  function safeHtml(text) {
    const escaped = String(text || '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    return escaped
      .replace(/&lt;(\/?)(br|b|strong)\s*\/?&gt;/gi, (m, slash, tag) => (tag.toLowerCase() === 'br' ? '<br>' : `<${slash}${tag.toLowerCase()}>`))
      .replace(/\r?\n/g, '<br>');
  }

  const wrap = i => ((i % places.length) + places.length) % places.length;
  /* 위치 p 에서 본 i 번 사진의 거리(-n/2 ~ n/2) */
  function offset(i, p) {
    const n = places.length;
    let d = i - p;
    d -= Math.round(d / n) * n;
    return d;
  }

  /* ── 그리기 ── */
  function render() {
    slides.forEach((el, i) => {
      const d = offset(i, pos);
      const a = Math.min(Math.abs(d), 1);
      const far = Math.max(Math.abs(d) - 1, 0);
      const scale = 1 - (1 - SIDE_SCALE) * a;
      el.style.transform = `translateX(${(d * STEP * 100).toFixed(3)}%) scale(${scale.toFixed(4)})`;
      el.style.opacity = String(Math.max(0, 1 - 0.45 * a - far * 1.2).toFixed(3));
      el.style.filter = a > 0.01 ? `blur(${(a * 2).toFixed(2)}px) saturate(${(1 - 0.25 * a).toFixed(3)})` : 'none';
      el.style.zIndex = String(100 - Math.round(Math.abs(d) * 10));
      el.setAttribute('aria-hidden', Math.abs(d) < 0.5 ? 'false' : 'true');
    });
    const near = wrap(Math.round(pos));
    dots.forEach((dot, i) => dot.classList.toggle('active', i === near));
  }

  /* ── 움직이기 ── */
  const easeOut = t => 1 - Math.pow(1 - t, 3);
  function stopAnim() {
    if (!anim) return;
    cancelAnimationFrame(anim.raf);
    clearTimeout(anim.safety);
    anim = null;
  }
  function animateTo(target) {
    stopAnim();
    clearTimeout(settleTimer);
    const from = pos, dist = target - from;
    const ms = reduceMotion ? 0 : SLIDE_MS * Math.min(1.6, Math.max(0.55, Math.abs(dist)));
    const t0 = performance.now();
    const me = {target};
    const finish = () => {
      if (anim !== me) return;
      stopAnim();
      pos = wrap(Math.round(target));
      render();
      settleTimer = setTimeout(() => show(pos), SETTLE_MS);
    };
    const step = now => {
      if (anim !== me) return;
      const t = ms ? Math.min(1, (now - t0) / ms) : 1;
      pos = from + dist * easeOut(t);
      render();
      if (t < 1) me.raf = requestAnimationFrame(step);
      else finish();
    };
    anim = me;
    me.raf = requestAnimationFrame(step);
    // 화면이 그려지지 않는 동안(숨은 탭 등) 프레임이 멈춰도 끝 위치에는 닿게
    me.safety = setTimeout(finish, ms + 120);
  }
  /* 지금 목표 칸에서 by 칸 옮기기(빨리 여러 번 눌러도 칸을 건너뛰지 않음) */
  function go(by) {
    const base = anim ? anim.target : Math.round(pos);
    animateTo(base + by);
  }
  function goTo(i) {
    const base = anim ? anim.target : Math.round(pos);
    const d = Math.round(offset(i, base));
    if (d) animateTo(base + d);
  }

  /* ── 손가락 · 마우스로 밀기 ── */
  function setupDrag(stage) {
    let drag = null;
    stage.addEventListener('pointerdown', e => {
      if (e.button !== 0 || e.target.closest('button')) return;
      stopAnim();
      clearTimeout(settleTimer);
      const w = slides[0] ? slides[0].getBoundingClientRect().width : 0;
      drag = {x: e.clientX, start: pos, step: Math.max(1, w * STEP), id: e.pointerId, moved: false};
      stage.setPointerCapture(e.pointerId);
      stage.classList.add('dragging');
    });
    stage.addEventListener('pointermove', e => {
      if (!drag || e.pointerId !== drag.id) return;
      const dx = e.clientX - drag.x;
      if (Math.abs(dx) > 3) drag.moved = true;
      pos = drag.start - dx / drag.step;
      render();
    });
    const end = e => {
      if (!drag || e.pointerId !== drag.id) return;
      const dx = e.clientX - drag.x;
      const base = Math.round(drag.start);
      let target = base;
      if (dx <= -DRAG_PX) target = base + Math.max(1, Math.round(-dx / drag.step));
      else if (dx >= DRAG_PX) target = base - Math.max(1, Math.round(dx / drag.step));
      // 옆 사진을 그냥 누르면 그쪽으로
      if (!drag.moved && e.type === 'pointerup') {
        const slide = e.target.closest && e.target.closest('.pp-slide');
        if (slide) {
          const d = Math.round(offset(Number(slide.dataset.index), drag.start));
          if (d) target = base + d;
        }
      }
      drag = null;
      stage.classList.remove('dragging');
      animateTo(target);
    };
    stage.addEventListener('pointerup', end);
    stage.addEventListener('pointercancel', end);
  }

  /* ── 아래 카드에 정보 넣기 ── */
  function fill(place) {
    const title = $('desc-title'), text = $('desc-text'), label = $('place-label-text');
    const sillokCard = $('sillok-card'), sillokText = $('sillok-text');
    const hasRecord = place.recorded && place.description;
    if (label) label.textContent = place.name;
    if (title) title.textContent = `${place.name}에 오신 것을 환영합니다.`;
    if (text) text.innerHTML = safeHtml(hasRecord ? place.description : place.short);
    if (sillokCard) {
      const sillok = place.recorded ? place.sillok_summary : '';
      sillokCard.style.display = sillok ? 'block' : 'none';
      if (sillokText) sillokText.innerHTML = sillok ? safeHtml(sillok) : '';
    }
    if (typeof state !== 'undefined' && state) state.currentLandmark = place.name;   // script.js 전역 상태
    document.documentElement.dataset.placeShown = place.id;
  }
  function show(i, instant) {
    i = wrap(Math.round(i));
    if (i === current && !instant) return;
    current = i;
    const cards = [$('main-desc-card'), $('sillok-card'), $('place-label-text')].filter(Boolean);
    clearTimeout(swapTimer);
    if (instant || reduceMotion) { fill(places[i]); return; }
    cards.forEach(c => c.classList.add('pp-swap'));
    swapTimer = setTimeout(() => {
      fill(places[i]);
      setTimeout(() => cards.forEach(c => c.classList.remove('pp-swap')), 30);
    }, FADE_MS);
  }

  /* ── 만들기 ── */
  function build(root) {
    const track = root.querySelector('.pp-track');
    const dotBox = root.querySelector('.pp-dots');
    places.forEach((p, i) => {
      const fig = document.createElement('figure');
      fig.className = 'pp-slide';
      fig.dataset.index = String(i);
      fig.setAttribute('role', 'group');
      fig.setAttribute('aria-label', `${i + 1} / ${places.length} ${p.name}`);
      const img = document.createElement('img');
      img.src = p.image;
      img.alt = `${p.name} 사진`;
      img.draggable = false;
      const name = document.createElement('figcaption');
      name.className = 'pp-name';
      name.textContent = p.name;
      fig.append(img, name);
      if (img.decode) img.decode().catch(() => {});   // 처음 넘길 때 빈 사진이 보이지 않도록 미리 풀어 둠
      if (p.credit) {
        const credit = document.createElement('small');
        credit.className = 'pp-credit';
        credit.textContent = `사진: ${p.credit.author} · ${p.credit.license}`;
        fig.appendChild(credit);
      }
      track.appendChild(fig);
      slides.push(fig);

      const dot = document.createElement('button');
      dot.type = 'button';
      dot.className = 'pp-dot';
      dot.setAttribute('aria-label', `${p.name} 보기`);
      dot.addEventListener('click', () => goTo(i));
      dotBox.appendChild(dot);
      dots.push(dot);
    });
    root.querySelector('.pp-prev').addEventListener('click', () => go(-1));
    root.querySelector('.pp-next').addEventListener('click', () => go(1));
    const stage = root.querySelector('.pp-stage');
    stage.addEventListener('keydown', e => {
      if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); }
      if (e.key === 'ArrowRight') { e.preventDefault(); go(1); }
    });
    setupDrag(stage);
    render();
    show(0, true);
  }

  document.addEventListener('DOMContentLoaded', async () => {
    const root = $('place-picker');
    if (!root) return;
    try {
      const response = await fetch('/static/places.json?v=booth13b', {cache: 'no-cache'});
      const data = await response.json();
      places = (data.places || []).filter(p => p && p.image && p.name);
      if (!places.length) throw new Error('장소 없음');
    } catch (error) {
      console.error('장소 목록을 읽지 못했습니다:', error);
      root.classList.add('pp-empty');
      return;
    }
    build(root);
    root.classList.add('pp-ready');
  });

  window.placePicker = {
    next: () => go(1), prev: () => go(-1), goTo,
    get index() { return current; },
    _frame(p) { stopAnim(); clearTimeout(settleTimer); pos = p; render(); },   // 화면 시험용: 움직이는 중간 모습 고정
  };
})();
