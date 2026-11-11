'use strict';
// Окно AI Vision. Каркас и мелочи (подсказки к значкам, свой выпадающий список, график,
// плашки, диалоги) — порт из лаунчера Откликса; данные — из Python через pywebview.api.

// Ошибки окна — в журнал Python (и в «Журнал работы»), иначе их не видно.
function reportError(msg) {
  if (/ResizeObserver loop/.test(msg)) return;     // безвредное предупреждение браузера, не ошибка
  window.__err = msg;
  try { window.pywebview?.api?.ui_error?.(String(msg).slice(0, 500)); } catch { /* окно ещё не готово */ }
}
window.addEventListener('error', (e) => reportError(`${e.message} @ ${(e.filename || '').split('/').pop()}:${e.lineno}`));
window.addEventListener('unhandledrejection', (e) => reportError(`promise: ${e.reason && e.reason.stack ? e.reason.stack : e.reason}`));

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

// ── Мелочи ─────────────────────────────────────────────────────────────────────────────
const fmt = (n) => Number(n || 0).toLocaleString('ru-RU');
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
};
function svgUse(id) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  const use = document.createElementNS(ns, 'use');
  use.setAttribute('href', `#${id}`);
  svg.appendChild(use);
  return svg;
}
function timeLabel(ms) {
  const d = new Date(ms);
  if (d.toDateString() === new Date().toDateString()) return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }).replace('.', '');
}
const clock = (ms) => new Date(ms).toLocaleTimeString('ru-RU');
function plural(n, one, few, many) {
  const m = n % 100; const d = n % 10;
  if (m >= 11 && m <= 14) return many;
  if (d === 1) return one;
  if (d >= 2 && d <= 4) return few;
  return many;
}
function setNum(id, v) {
  const e = $(id);
  if (!e) return;
  const t = fmt(v);
  if (e.textContent !== t) { e.textContent = t; e.classList.remove('bump'); void e.offsetWidth; e.classList.add('bump'); }
  e.closest('.stat-card')?.classList.toggle('has-value', Number(v) > 0);
}
function setBadge(id, n) {
  const b = $(id);
  if (!b) return;
  b.textContent = n > 99 ? '99+' : String(n);
  b.classList.toggle('hidden', n <= 0);
}
function withStableScroll(box, mutate) {
  const top = box.scrollTop; const h = box.scrollHeight;
  mutate();
  if (top > 0) box.scrollTop = top + (box.scrollHeight - h);
}

// ── Подсказки к значкам (порт из Откликса) ─────────────────────────────────────────────
const ttip = el('div', 'ttip');
ttip.setAttribute('role', 'tooltip');
document.body.appendChild(ttip);
let tipEl = null;
let tipTimer = 0;
function hideTip() { clearTimeout(tipTimer); tipEl = null; ttip.classList.remove('is-on'); }
function showTip(t) {
  if (!t.isConnected || !t.dataset.tip) return;
  ttip.textContent = t.dataset.tip;
  const r = t.getBoundingClientRect();
  const w = ttip.offsetWidth; const h = ttip.offsetHeight; const gap = 8;
  let x; let y; let tx = '0px'; let ty = '4px';
  if (t.closest('.rail')) { x = r.right + gap; y = r.top + r.height / 2 - h / 2; tx = '-4px'; ty = '0px'; } else if (r.bottom + gap + h < innerHeight) { x = r.left + r.width / 2 - w / 2; y = r.bottom + gap; ty = '-4px'; } else { x = r.left + r.width / 2 - w / 2; y = r.top - gap - h; }
  x = Math.max(6, Math.min(innerWidth - w - 6, x));
  y = Math.max(6, Math.min(innerHeight - h - 6, y));
  ttip.style.left = `${x}px`; ttip.style.top = `${y}px`;
  ttip.style.setProperty('--tx', tx); ttip.style.setProperty('--ty', ty);
  ttip.classList.add('is-on');
}
document.addEventListener('mouseover', (e) => {
  const t = e.target.closest?.('[title], [data-tip]');
  if (!t || t.matches('input, textarea, select') || t === tipEl) return;
  if (t.hasAttribute('title')) {
    t.dataset.tip = t.getAttribute('title');
    t.removeAttribute('title');
    if (!t.textContent.trim() && !t.hasAttribute('aria-label')) t.setAttribute('aria-label', t.dataset.tip);
  }
  hideTip();
  tipEl = t;
  tipTimer = setTimeout(() => showTip(t), 500);
});
document.addEventListener('mouseout', (e) => { if (tipEl && !tipEl.contains(e.relatedTarget)) hideTip(); });
document.addEventListener('mousedown', hideTip, true);
addEventListener('blur', hideTip);

// ── Свой выпадающий список (порт из Откликса) ──────────────────────────────────────────
function enhanceSelect(select) {
  const wrap = el('div', 'sel');
  select.parentNode.insertBefore(wrap, select);
  wrap.appendChild(select);
  select.tabIndex = -1;
  select.setAttribute('aria-hidden', 'true');
  const btn = el('button', 'sel__btn');
  btn.type = 'button';
  const label = el('span');
  btn.append(label, svgUse('i-chev'));
  const menu = el('div', 'sel__menu');
  wrap.append(btn, menu);
  const sync = () => {
    const o = select.options[select.selectedIndex];
    label.textContent = o ? o.textContent : '';
    opts.forEach((b) => b.setAttribute('aria-selected', String(b.dataset.value === select.value)));
  };
  const outside = (e) => { if (!wrap.contains(e.target)) close(); };
  function openMenu() {
    const r = btn.getBoundingClientRect();
    wrap.classList.toggle('sel--up', r.bottom + opts.length * 34 + 24 > innerHeight);
    wrap.classList.add('is-open');
    document.addEventListener('pointerdown', outside, true);
  }
  function close() {
    wrap.classList.remove('is-open');
    document.removeEventListener('pointerdown', outside, true);
  }
  const opts = [...select.options].map((o) => {
    const b = el('button', 'sel__opt');
    b.type = 'button';
    b.dataset.value = o.value;
    b.append(el('span', '', o.textContent), svgUse('i-check'));
    b.addEventListener('click', () => {
      if (select.value !== o.value) { select.value = o.value; select.dispatchEvent(new Event('change', { bubbles: true })); }
      close();
    });
    menu.appendChild(b);
    return b;
  });
  btn.addEventListener('click', () => (wrap.classList.contains('is-open') ? close() : openMenu()));
  const desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
  Object.defineProperty(select, 'value', { configurable: true, get() { return desc.get.call(this); }, set(v) { desc.set.call(this, v); sync(); } });
  select.addEventListener('change', sync);
  sync();
}
function fillOptions(select, items) {
  select.textContent = '';
  items.forEach(([value, text]) => { const o = el('option', null, text); o.value = value; select.appendChild(o); });
}

// ── Плашки и диалоги ───────────────────────────────────────────────────────────────────
function toast({ kind = 'ok', title, text = '', panel = '' }) {
  const box = $('toasts');
  const t = el('div', `toast toast--${kind}`);
  const ico = el('span', 'toast__ico');
  ico.appendChild(svgUse(kind === 'warn' ? 'i-bell' : 'i-check'));
  const body = el('div');
  body.append(el('b', '', title), el('span', '', text));
  const x = el('button', 'toast__x');
  x.type = 'button';
  x.appendChild(svgUse('i-x'));
  t.append(ico, body, x);
  const close = () => { t.classList.add('is-out'); setTimeout(() => t.remove(), 300); };
  x.addEventListener('click', (e) => { e.stopPropagation(); close(); });
  t.addEventListener('click', () => {
    if (panel === 'scenarios') switchView('scenarios');
    else if (panel) switchPanel(panel);
    close();
  });
  box.appendChild(t);
  while (box.children.length > 4) box.firstElementChild.remove();
  setTimeout(close, 7000);
}
function modal({ title, text, ok = 'Да', tone = 'dark', icon = 'i-send', input = null }) {
  return new Promise((resolve) => {
    const back = el('div', 'modal-backdrop');
    const m = el('div', 'modal modal--confirm');
    m.setAttribute('role', 'alertdialog');
    const ico = el('span', `modal__ico${tone === 'danger' ? ' modal__ico--danger' : ''}`);
    ico.appendChild(svgUse(icon));
    const actions = el('div', 'modal__actions');
    const cancel = el('button', 'btn btn--line', 'Отмена');
    const yes = el('button', `btn ${tone === 'danger' ? 'btn--danger' : 'btn--dark'}`, ok);
    cancel.type = 'button'; yes.type = 'button';
    actions.append(cancel, yes);
    m.append(ico, el('h2', '', title), el('p', '', text));
    let field = null;
    if (input) { field = el('input'); field.type = 'text'; field.placeholder = input; m.appendChild(field); }
    m.appendChild(actions);
    back.appendChild(m);
    const onKey = (e) => { if (e.key === 'Escape') close(false); if (e.key === 'Enter' && field) close(true); };
    function close(v) { document.removeEventListener('keydown', onKey); back.remove(); resolve(v ? (field ? field.value.trim() || input : true) : false); }
    cancel.addEventListener('click', () => close(false));
    yes.addEventListener('click', () => close(true));
    back.addEventListener('mousedown', (e) => { if (e.target === back) close(false); });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(back);
    (field || cancel).focus();
  });
}

// ── Разделы и панели ───────────────────────────────────────────────────────────────────
const PANEL_TITLES = { overview: 'Обзор', quick: 'Быстрая задача', task: 'Задача команде', feed: 'Что происходит', approvals: 'Одобрения',
  inbox: 'Входящие', vision: 'Зрение', tasks: 'Задачи', log: 'Журнал работы' };
const PANEL_EL = (p) => (p === 'overview' ? 'overview' : `${p}-panel`);
let currentView = 'pilot';
let currentPanel = 'overview';

function headButton(text, icon, onClick, cls = 'btn btn--line btn--sm', id = '') {
  const b = el('button', cls);
  b.type = 'button';
  if (id) b.id = id;
  if (icon) b.appendChild(svgUse(icon));
  b.append(text);
  b.addEventListener('click', onClick);
  return b;
}
function chip(text, icon) {
  const c = el('span', 'chip');
  if (icon) c.appendChild(svgUse(icon));
  c.append(text);
  return c;
}
function setHead(title, actions = []) {
  $('main-title').textContent = title;
  const box = $('main-actions');
  box.textContent = '';
  actions.forEach((a) => box.appendChild(a));
}

function switchView(view) {
  currentView = view;
  document.querySelectorAll('.rail__btn[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  document.querySelectorAll('.side__nav').forEach((n) => n.classList.toggle('hidden', n.dataset.for !== view));
  document.querySelectorAll('.view').forEach((v) => v.classList.toggle('hidden', v.id !== `view-${view}`));
  $('main-scroll').scrollTop = 0;
  if (view === 'pilot') switchPanel(currentPanel);
  else if (view === 'scenarios') showScenarioHead();
  else { setHead('Настройки'); loadSettings(); }
}
document.querySelectorAll('.rail__btn[data-view]').forEach((b) => b.addEventListener('click', () => switchView(b.dataset.view)));

function switchPanel(panel) {
  currentPanel = panel;
  if (currentView !== 'pilot') {
    currentView = 'pilot';
    document.querySelectorAll('.rail__btn[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === 'pilot'));
    document.querySelectorAll('.side__nav').forEach((n) => n.classList.toggle('hidden', n.dataset.for !== 'pilot'));
    document.querySelectorAll('.view').forEach((v) => v.classList.toggle('hidden', v.id !== 'view-pilot'));
  }
  document.querySelectorAll('.nav-i[data-panel]').forEach((b) => b.classList.toggle('active', b.dataset.panel === panel));
  Object.keys(PANEL_TITLES).forEach((p) => $(PANEL_EL(p))?.classList.add('hidden'));
  $(PANEL_EL(panel))?.classList.remove('hidden');
  $('main-scroll').scrollTop = 0;
  renderHead();
  if (panel === 'overview') refreshOverview();
  if (panel === 'feed') renderFeed();
  if (panel === 'approvals') renderApprovals();
  if (panel === 'inbox') renderInbox();
  if (panel === 'tasks') renderTasks();
  if (panel === 'vision') startVision(); else stopVision();
  if (panel === 'task') setTimeout(() => $('t-req').focus(), 60);
  if (panel === 'quick') setTimeout(() => $('q-req').focus(), 60);
}
document.querySelectorAll('.nav-i[data-panel]').forEach((b) => b.addEventListener('click', () => switchPanel(b.dataset.panel)));
document.querySelectorAll('[data-panel-go]').forEach((b) => b.addEventListener('click', () => switchPanel(b.dataset.panelGo)));

let team = { busy: false };
function renderHead() {
  if (currentView !== 'pilot') return;
  const a = [];
  if (currentPanel === 'overview') a.push(chip('Последние 14 дней'));
  if ((currentPanel === 'task' || currentPanel === 'quick') && team.busy) a.push(headButton('Остановить', 'i-stop', stopAll));
  if (currentPanel === 'feed' || currentPanel === 'tasks') a.push(chip('Свежее сверху'));
  if (currentPanel === 'vision') a.push(headButton('Перезапустить', 'i-refresh', restartServices));
  if (currentPanel === 'log') a.push(headButton('Очистить', 'i-trash', () => J_ALL.clear(), 'btn btn--ghost btn--sm'));
  setHead(PANEL_TITLES[currentPanel] || '', a);
}

// Настройки: пункты ведут к секциям, подсветка следует за прокруткой (порт из Откликса).
let jumpLock = 0;
document.querySelectorAll('.nav-i[data-jump]').forEach((btn) => btn.addEventListener('click', () => {
  jumpLock = Date.now() + 700;
  btn.parentElement.querySelectorAll('.nav-i[data-jump]').forEach((b) => b.classList.toggle('active', b === btn));
  const sec = $(btn.dataset.jump);
  if (!sec) return;
  sec.scrollIntoView({ block: 'start' });
  sec.classList.remove('is-flash');
  void sec.offsetWidth;
  sec.classList.add('is-flash');
  setTimeout(() => sec.classList.remove('is-flash'), 1800);
}));
$('main-scroll').addEventListener('scroll', () => {
  if (currentView !== 'settings' || Date.now() < jumpLock) return;
  const nav = document.querySelector(`.side__nav[data-for="${currentView}"]`);
  const box = $('main-scroll').getBoundingClientRect();
  let active = null;
  nav.querySelectorAll('.nav-i[data-jump]').forEach((b) => {
    const sec = $(b.dataset.jump);
    if (sec && sec.getBoundingClientRect().top - box.top < 80) active = b;
  });
  const sc = $('main-scroll');
  if (sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 4) active = [...nav.querySelectorAll('.nav-i[data-jump]')].pop();
  active = active || nav.querySelector('.nav-i[data-jump]');
  nav.querySelectorAll('.nav-i[data-jump]').forEach((b) => b.classList.toggle('active', b === active));
}, { passive: true });

// ── Статусы ────────────────────────────────────────────────────────────────────────────
const STATUS = {
  done: ['pill--applied', 'готово'], fail: ['pill--alert', 'не вышло'], aborted: ['pill--bad', 'остановлено'],
  held: ['pill--held', 'ждёт «да»'], in_progress: ['pill--work', 'в работе'], new: ['pill--bad', 'новая'],
  escalated: ['pill--alert', 'эскалация'], error: ['pill--alert', 'ошибка'],
  skipped: ['pill--bad', 'пропущено'], partial: ['pill--good', 'частично'], stuck: ['pill--alert', 'застрял'],
  limit: ['pill--good', 'лимит шагов'], blind: ['pill--alert', 'не видел экран'],
};
function pill(status) {
  const [cls, text] = STATUS[status] || ['pill--bad', status || '—'];
  return el('span', `pill ${cls}`, text);
}

// ── Обзор и график (порт из Откликса) ──────────────────────────────────────────────────
let lastDays = [];
async function refreshOverview() {
  const ov = await api().overview().catch(() => null);
  if (!ov) return;
  setNum('stat-done', ov.done_today);
  setNum('stat-today', ov.today);
  setNum('stat-runs', ov.runs_today);
  $('chart-today').textContent = `+${fmt(ov.today)}`;
  setNum('ov-approvals', ov.approvals);
  setNum('ov-inbox', ov.inbox);
  setNum('ov-active', ov.active);
  setNum('ov-failed', ov.failed);
  $('today-badge').textContent = `сегодня ${ov.today} ${plural(ov.today, 'задача', 'задачи', 'задач')}`;
  lastDays = ov.series;
  drawChart();
  const list = $('ov-recent');
  list.textContent = '';
  if (!ov.recent.length) list.appendChild(el('div', 'empty', 'Задач пока нет — поставьте первую команде.'));
  for (const t of ov.recent) {
    const row = el('button', 'rres');
    row.type = 'button';
    const ico = el('span', 'rres__ico');
    ico.appendChild(svgUse(t.status === 'done' ? 'i-check' : t.status === 'held' ? 'i-hand' : 'i-send'));
    const txt = el('span');
    const [, word] = STATUS[t.status] || ['', t.status];
    txt.append(el('b', '', t.goal), el('small', '', `${word} · ${t.assignee || 'оркестратор'} · ${timeLabel(t.created * 1000)}`));
    const go = el('span', 'rres__go');
    go.appendChild(svgUse('i-chev'));
    row.append(ico, txt, go);
    row.addEventListener('click', () => { openTaskId = t.id; switchPanel('tasks'); });
    list.appendChild(row);
  }
}
function drawChart() {
  const box = $('chart');
  if (!box) return;
  const days = lastDays;
  const total = days.reduce((s, d) => s + d.count, 0);
  $('chart-total').textContent = fmt(total);
  box.textContent = '';
  if (!box.clientWidth) return;
  const W = Math.max(280, box.clientWidth); const H = Math.max(160, box.clientHeight || 196);
  const padL = 30; const padR = 8; const padT = 10; const padB = 22;
  const max = Math.max(4, ...days.map((d) => d.count));
  const step = Math.ceil(max / 4);
  const top = step * 4;
  const x = (i) => padL + (i * (W - padL - padR)) / Math.max(1, days.length - 1);
  const y = (v) => padT + (1 - v / top) * (H - padT - padB);
  const ns = 'http://www.w3.org/2000/svg';
  const mk = (tag, attrs) => { const n = document.createElementNS(ns, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); return n; };
  const svg = mk('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none' });
  const defs = mk('defs', {});
  const grad = mk('linearGradient', { id: 'chartFill', x1: 0, y1: 0, x2: 0, y2: 1 });
  grad.append(mk('stop', { offset: '0', 'stop-color': '#12edcf', 'stop-opacity': '.28' }), mk('stop', { offset: '1', 'stop-color': '#12edcf', 'stop-opacity': '0' }));
  defs.appendChild(grad); svg.appendChild(defs);
  const grid = mk('g', { class: 'grid' });
  for (let k = 0; k <= 4; k += 1) {
    const v = step * k;
    grid.appendChild(mk('line', { x1: padL, x2: W - padR, y1: y(v), y2: y(v) }));
    const t = mk('text', { x: padL - 6, y: y(v) + 3, 'text-anchor': 'end', class: 'ax' }); t.textContent = v; svg.appendChild(t);
  }
  svg.insertBefore(grid, svg.firstChild.nextSibling);
  if (days.length) {
    const pts = days.map((d, i) => [x(i), y(d.count)]);
    let dLine = `M ${pts[0][0]} ${pts[0][1]}`;
    for (let i = 1; i < pts.length; i += 1) {
      const [x0, y0] = pts[i - 1]; const [x1, y1] = pts[i]; const mx = (x0 + x1) / 2;
      dLine += ` C ${mx} ${y0}, ${mx} ${y1}, ${x1} ${y1}`;
    }
    svg.appendChild(mk('path', { d: `${dLine} L ${pts[pts.length - 1][0]} ${y(0)} L ${pts[0][0]} ${y(0)} Z`, class: 'area' }));
    const line = mk('path', { d: dLine, class: 'lineP' });
    svg.appendChild(line);
    const fmtDay = (ms) => new Date(ms).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }).replace('.', '');
    [0, days.length - 1].forEach((i, k) => { const t = mk('text', { x: x(i), y: H - 4, 'text-anchor': k ? 'end' : 'start', class: 'ax' }); t.textContent = fmtDay(days[i].date); svg.appendChild(t); });
    const last = pts[pts.length - 1];
    svg.appendChild(mk('circle', { cx: last[0], cy: last[1], r: 3.5, class: 'dotP' }));
    const hov = mk('line', { y1: padT, y2: H - padB, class: 'hov', visibility: 'hidden' });
    const hdot = mk('circle', { r: 3.5, class: 'dotP', visibility: 'hidden' });
    svg.append(hov, hdot);
    const tip = el('div', 'chart__tip hidden');
    box.append(svg, tip);
    // линия «рисуется» слева направо при каждом показе
    const len = line.getTotalLength();
    line.style.strokeDasharray = `${len}`; line.style.strokeDashoffset = `${len}`;
    line.animate([{ strokeDashoffset: len }, { strokeDashoffset: 0 }], { duration: 900, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'forwards' });
    svg.addEventListener('mousemove', (ev) => {
      const r = svg.getBoundingClientRect();
      const px = ((ev.clientX - r.left) / r.width) * W;
      const i = Math.max(0, Math.min(days.length - 1, Math.round(((px - padL) / (W - padL - padR)) * (days.length - 1))));
      hov.setAttribute('x1', pts[i][0]); hov.setAttribute('x2', pts[i][0]); hov.setAttribute('visibility', 'visible');
      hdot.setAttribute('cx', pts[i][0]); hdot.setAttribute('cy', pts[i][1]); hdot.setAttribute('visibility', 'visible');
      tip.textContent = '';
      tip.append(el('b', '', fmt(days[i].count)), document.createTextNode(` · ${fmtDay(days[i].date)}`));
      tip.style.left = `${(pts[i][0] / W) * 100}%`; tip.style.top = `${(pts[i][1] / H) * 100}%`;
      tip.classList.remove('hidden');
    });
    svg.addEventListener('mouseleave', () => { hov.setAttribute('visibility', 'hidden'); hdot.setAttribute('visibility', 'hidden'); tip.classList.add('hidden'); });
  } else box.appendChild(svg);
  if (!total) box.appendChild(el('div', 'chart__empty', 'Здесь появится график, как только команда получит первые задачи.'));
}
new ResizeObserver(() => { if (lastDays.length) drawChart(); }).observe($('chart'));

// ── Задача команде ─────────────────────────────────────────────────────────────────────
const EXAMPLES = [
  'Открой Яндекс Метрику Откликса и напиши, что изменилось за неделю',
  'Найди в почте последнее письмо от хостинга и перескажи',
  'Нажми «Опубликовать»',
];
EXAMPLES.forEach((t) => {
  const b = el('button', 'ex', t.length > 34 ? `${t.slice(0, 33)}…` : t);
  b.type = 'button';
  b.title = t;
  b.addEventListener('click', () => { $('t-req').value = t; $('t-req').focus(); });
  $('t-examples').appendChild(b);
});
async function teamGo(execute) {
  const req = $('t-req').value.trim();
  if (!req) { $('t-req').focus(); return; }
  const r = await api().team_go(req, execute);
  if (r && r.error) { toast({ kind: 'warn', title: 'Не запустилось', text: r.error }); return; }
  $('t-plan-list').dataset.sig = '';
  if (execute) { $('t-req').value = ''; launched(); }
}
$('t-plan').addEventListener('click', () => teamGo(false));
$('t-run').addEventListener('click', () => teamGo(true));
$('t-req').addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.ctrlKey) { e.preventDefault(); teamGo(true); } });
// ── Быстрая задача: без оркестратора — услышал, сразу сделал ──
const QUICK_EXAMPLES = [
  'Открой Telegram и найди чат «Гера»',
  'Открой Яндекс Метрику Откликса и скажи, сколько визитов за неделю',
  'Нажми «Опубликовать»',
];
QUICK_EXAMPLES.forEach((t) => {
  const b = el('button', 'ex', t.length > 34 ? `${t.slice(0, 33)}…` : t);
  b.type = 'button';
  b.title = t;
  b.addEventListener('click', () => { $('q-req').value = t; $('q-req').focus(); });
  $('q-examples').appendChild(b);
});
let quickLast = '';                                 // последняя запущенная — её и сохраняем, если поле пустое
async function quickGo() {
  const goal = $('q-req').value.trim();
  if (!goal) { $('q-req').focus(); return; }
  const r = await api().quick_go(goal);
  if (r && r.error) { toast({ kind: 'warn', title: 'Не запустилось', text: r.error }); return; }
  quickLast = goal;
  $('q-req').value = '';
  launched();
}
async function quickSave() {
  const r = await api().quick_save($('q-req').value.trim() || quickLast);
  if (r && r.error) { toast({ kind: 'warn', title: 'Не сохранилось', text: r.error }); $('q-req').focus(); return; }
  toast({ kind: 'ok', title: `Сценарий «${r.name}» сохранён`,
    text: r.recipe ? 'С рецептом удачного прогона — повтор пойдёт быстрым путём.' : 'Найдёте его во вкладке «Сценарии».', panel: 'scenarios' });
}
$('q-run').addEventListener('click', quickGo);
$('q-save').addEventListener('click', quickSave);
$('q-req').addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.ctrlKey) { e.preventDefault(); quickGo(); } });

async function stopAll() { await api().stop_all(); toast({ kind: 'warn', title: 'Останавливаю', text: 'Текущее действие завершится, новые не начнутся.' }); }
$('stop-all-btn').addEventListener('click', stopAll);
$('s-siberi-login').addEventListener('click', async () => {
  await api().siberi_login();
  toast({ kind: 'ok', title: 'Окно для входа в Google', text: 'Войдите в аккаунт — окно закроется само, мозг перезапустится.' });
});

function renderPlan(steps) {
  const sig = JSON.stringify(steps);
  const list = $('t-plan-list');
  if (list.dataset.sig === sig) return;
  list.dataset.sig = sig;
  $('t-plan-card').classList.toggle('hidden', !steps.length);
  $('t-plan-n').textContent = `${steps.length} ${plural(steps.length, 'шаг', 'шага', 'шагов')}`;
  list.textContent = '';
  steps.forEach((s, i) => {
    const row = el('div', 'step');
    row.style.animationDelay = `${i * 60}ms`;
    const who = el('div', 'step__who', s.name || 'агент');
    if (s.depends_on && s.depends_on.length) who.appendChild(el('small', '', `после шага ${s.depends_on.map((d) => d + 1).join(', ')}`));
    const body = el('div');
    body.append(who, el('div', 'step__task', s.task));
    row.append(el('span', 'step__n', String(i + 1)), body);
    list.appendChild(row);
  });
}

// ── Журналы: по задачам и шагам (journal.js) ───────────────────────────────────────────
const J_ALL = new Journal($('log-feed'), { empty: $('log-empty') });
const J_TEAM = new Journal($('t-log'), { empty: $('t-log-empty'), sources: ['team'] });
const J_RUN = new Journal($('run-log'), { empty: $('run-empty'), sources: ['run'] });

// ── Что происходит: журнал команды ─────────────────────────────────────────────────────
async function renderFeed() {
  const items = await api().journal(200);
  const box = $('feed');
  box.textContent = '';
  $('feed-empty').hidden = items.length > 0;
  const today = new Date().toISOString().slice(0, 10);
  for (const j of items) {
    const row = el('div', 'ev ev--journal');
    const when = el('time', '', j.date === today ? j.time.slice(0, 5) : j.date.slice(5).split('-').reverse().join('.'));
    when.title = `${j.date} ${j.time}`;
    const what = el('div', 'ev__what');
    const title = el('b', '', j.full || j.task);
    title.title = j.full || j.task;
    what.append(title, el('small', '', `${j.agent}${j.answer ? ` → ${j.answer}` : ''}`));
    row.append(when, j.rerun ? rerunPill(j) : pill(j.status), what);
    row.classList.add('ev--row');
    row.addEventListener('click', () => { if (!String(window.getSelection())) row.classList.toggle('is-open'); });
    box.appendChild(row);
  }
}

// Статус «остановлено», «ждёт «да»», «не вышло»… — кнопка: задача запускается заново.
const RERUNNABLE = new Set(['aborted', 'held', 'fail', 'stuck', 'limit', 'blind', 'skipped', 'escalated', 'error']);
function rerunPill(j) {
  const b = pill(j.status);
  const p = el('button', `${b.className} pill--action`);
  p.type = 'button';
  p.append(b.textContent, svgUse('i-refresh'));
  p.title = j.status === 'held' ? 'Одобрить и выполнить' : 'Запустить заново';
  p.addEventListener('click', (e) => { e.stopPropagation(); rerunTask(j); });
  return p;
}
async function rerunTask(j) {
  if (j.status === 'held') {                       // это и есть «да» человека — спросим явно
    const ok = await modal({ title: 'Одобрить и выполнить?', text: `${j.agent}: ${j.full}`, ok: 'Одобрить и выполнить', icon: 'i-hand' });
    if (!ok) return;
  }
  const r = await api().rerun(j.tid);
  if (r && r.error) { toast({ kind: 'warn', title: 'Не запустилось', text: r.error }); return; }
  launched();
}
// Запустили задачу — сразу в «Журнал работы», к её началу.
function launched() {
  switchPanel('log');
  const box = $('log-feed');
  setTimeout(() => { box.scrollTop = box.scrollHeight; }, 50);
}

// ── Нужно вам ──────────────────────────────────────────────────────────────────────────
function card({ icon, teal, title, meta, why, actions }) {
  const c = el('div', 'mcard');
  const head = el('div', 'mcard__head');
  const ico = el('span', `mcard__ico${teal ? ' mcard__ico--teal' : ''}`);
  ico.appendChild(svgUse(icon));
  const t = el('div', 'mcard__title', title);
  t.appendChild(el('div', 'mcard__meta', meta));
  head.append(ico, t);
  c.appendChild(head);
  if (why) c.appendChild(el('div', 'mcard__why', why));
  const row = el('div', 'mcard__row');
  actions.forEach((a) => row.appendChild(a));
  c.appendChild(row);
  return c;
}
function gone(c, after) { c.classList.add('is-gone'); setTimeout(() => { c.remove(); after?.(); }, 280); }
async function renderApprovals() {
  const items = await api().approvals();
  const box = $('approvals-list');
  box.textContent = '';
  $('approvals-empty').hidden = items.length > 0;
  for (const a of items) {
    const no = headButton('Отклонить', null, async () => { await api().reject(a.id); gone(c, renderApprovals); });
    const yes = headButton('Одобрить и выполнить', 'i-check', async () => {
      const r = await api().approve(a.id);
      if (r && r.error) { toast({ kind: 'warn', title: 'Не запустилось', text: r.error }); return; }
      launched();
    }, 'btn btn--dark btn--sm');
    const c = card({ icon: 'i-hand', title: a.task, meta: `${a.agent || 'агент'} · ${timeLabel((a.created || 0) * 1000)}`, why: a.why, actions: [no, yes] });
    box.appendChild(c);
  }
}
async function renderInbox() {
  const items = await api().inbox();
  const box = $('inbox-list');
  box.textContent = '';
  $('inbox-empty').hidden = items.length > 0;
  for (const it of items) {
    const drop = headButton('Убрать', 'i-trash', async () => { await api().inbox_drop(it.name); gone(c, renderInbox); }, 'btn btn--ghost btn--sm');
    const run = headButton('Отдать команде', 'i-send', async () => {
      const r = await api().inbox_run(it.name);
      if (r && r.error) { toast({ kind: 'warn', title: 'Не запустилось', text: r.error }); return; }
      launched();
    }, 'btn btn--dark btn--sm');
    const c = card({ icon: 'i-inbox', teal: true, title: it.task, meta: `${it.area || 'пульс'} · ${timeLabel((it.created || 0) * 1000)}`, why: it.why, actions: [drop, run] });
    box.appendChild(c);
  }
}

// ── Задачи ─────────────────────────────────────────────────────────────────────────────
let openTaskId = '';
async function renderTasks() {
  const items = await api().tasks(150);
  const box = $('tasks-list');
  box.textContent = '';
  $('tasks-empty').hidden = items.length > 0;
  for (const t of items) {
    const row = el('div', 'ev ev--task ev--row');
    const what = el('div', 'ev__what');
    what.append(el('b', '', `${t.parent ? '↳ ' : ''}${t.goal}`), el('small', '', [t.assignee || 'оркестратор', t.answer].filter(Boolean).join(' · ')));
    const chev = el('span', 'ev__chev');
    chev.appendChild(svgUse('i-chev'));
    const again = RERUNNABLE.has(t.status) && t.assignee;
    row.append(el('time', '', timeLabel(t.created * 1000)),
      again ? rerunPill({ status: t.status, tid: t.id, agent: t.assignee, full: t.goal }) : pill(t.status), what, chev);
    const detail = el('div', 'detail hidden');
    row.addEventListener('click', async () => {
      if (String(window.getSelection())) return;   // выделяли текст, а не кликали
      const open = !row.classList.contains('is-open');
      row.classList.toggle('is-open', open);
      detail.classList.toggle('hidden', !open);
      if (open && !detail.dataset.loaded) {
        const d = await api().task_detail(t.id);
        detail.dataset.loaded = '1';
        if (d.report) { detail.append(el('div', 'detail__label', 'Отчёт'), el('div', 'detail__text selectable', d.report)); }
        if (d.log) { detail.append(el('div', 'detail__label', 'Ход задачи'), el('div', 'detail__text selectable', d.log)); }
        if (!d.report && !d.log) detail.append(el('div', 'detail__note', 'Отчёта по задаче нет.'));
      }
    });
    box.append(row, detail);
    if (t.id === openTaskId) { setTimeout(() => { row.click(); row.scrollIntoView({ block: 'center' }); }, 50); openTaskId = ''; }
  }
}

// ── Зрение ─────────────────────────────────────────────────────────────────────────────
const KIND = { window: 'окно', ui: 'элементы', text: 'текст', motion: 'движение', vision: 'картинка' };
let vSince = -1;
let vTimer = 0;
let vLogTimer = 0;
function kindOf(text) {
  if (text.startsWith('window:') || (text.startsWith('@') && text.includes('window:'))) return 'window';
  if (/^(▶|■) motion/.test(text)) return 'motion';
  if (text.startsWith('seen')) return 'vision';
  if (/^[+\-~] «/.test(text)) return 'text';
  return 'ui';
}
function vLine(kind, text, at) {
  const lines = text.split('\n');
  const head = lines[0];
  const extra = kind === 'window' && lines.length > 1 ? ` · ${lines.length - 1} ${plural(lines.length - 1, 'элемент', 'элемента', 'элементов')}` : '';
  const row = el('div', `vline vline--${kind}`);
  row.append(el('time', '', at), el('span', 'vline__k', KIND[kind] || kind), el('span', 'vline__t', kind === 'window' ? head + extra : text));
  if (kind === 'window' && lines.length > 1) row.title = lines.slice(1, 25).join('\n');
  return row;
}
function pushVision(rows) {
  const box = $('v-feed');
  if (!rows.length) return;
  $('v-empty')?.remove();
  withStableScroll(box, () => {
    rows.forEach((r) => box.prepend(r));
    while (box.children.length > 600) box.lastElementChild.remove();
  });
}
async function tickVision() {
  const v = await api().vision(vSince).catch(() => null);
  if (!v) return;
  const s = v.stats || {};
  const st = v.status.retina;
  $('v-state').textContent = { ok: 'работает', external: 'внешняя', starting: 'запускается…', down: 'не отвечает', off: 'выключена', idle: 'спит до задачи' }[st] || st;
  setNum('v-events', s.events || 0);
  $('v-lat').textContent = s.latency_ms_p50 != null ? `${s.latency_ms_p50} мс` : '—';
  $('v-saving').textContent = s.times_fewer ? `×${Math.round(s.times_fewer)}` : '—';
  if (vSince < 0) {
    const rows = (v.initial || []).map((line) => {
      const m = line.match(/^\[(\d\d:\d\d:\d\d)\] ([\s\S]*)$/);
      const text = m ? m[2] : line;
      return vLine(kindOf(text), text, m ? m[1] : '');
    });
    pushVision(rows);
  } else {
    const now = clock(Date.now());
    pushVision((v.events || []).map((e) => vLine(e.kind, e.text, now)));
  }
  vSince = v.last || 0;
}
async function tickVisionLog() {
  if ($('v-svclog').closest('details').open) $('v-svclog').textContent = await api().vision_log();
}
function startVision() {
  if (vTimer) return;
  tickVision();
  vTimer = setInterval(tickVision, 1000);
  tickVisionLog();
  vLogTimer = setInterval(tickVisionLog, 5000);
}
function stopVision() { clearInterval(vTimer); clearInterval(vLogTimer); vTimer = 0; vLogTimer = 0; }
$('v-svclog').closest('details').addEventListener('toggle', tickVisionLog);
async function restartServices() {
  await api().services_restart();
  toast({ title: 'Перезапускаю зрение', text: 'Через несколько секунд всё поднимется само.', panel: 'vision' });
}

// ── Службы ─────────────────────────────────────────────────────────────────────────────
const SVC_TEXT = {
  retina: { ok: 'работает', external: 'внешняя', starting: 'запуск…', down: 'не отвечает', off: 'выключено', idle: 'спит' },
  ollama: { ok: 'готова', starting: 'запуск…', missing: 'нет Ollama', down: 'не отвечает', off: 'выключена' },
  siberi: { ok: 'работает', starting: 'запуск…', missing: 'нет Node.js', down: 'не запустился', off: 'выключен', remote: 'удалённый' },
};
function siberiText(st) {
  const t = SVC_TEXT.siberi[st.siberi] || st.siberi || '…';
  return st.siberi === 'ok' && st.signed_in === false ? `${t}, без входа` : t;
}
function dot(id, state) {
  const d = $(id);
  if (!d) return;
  d.className = 'status-dot';
  if (state === 'ok' || state === 'external' || state === 'ready') d.classList.add('on');
  else if (state === 'starting') d.classList.add('warn');
  else if (state === 'down' || state === 'missing') d.classList.add('off');
}
function renderServices(st) {
  if (!st) return;
  lastServices = st;
  const ollama = st.ollama === 'ok' && st.model === 'missing' ? 'missing' : st.ollama;
  dot('svc-retina-dot', st.retina);
  dot('svc-ollama-dot', ollama);
  dot('svc-siberi-dot', st.siberi === 'ok' && st.signed_in === false ? 'starting' : st.siberi);
  $('svc-siberi').textContent = siberiText(st);
  dot('s-siberi-dot', st.siberi === 'ok' && st.signed_in === false ? 'starting' : st.siberi);
  $('s-siberi').textContent = `Мозг (Siberi Local) — ${siberiText(st)}${st.siberi === 'ok' && st.signed_in === false ? ': войдите в Google, иначе не видит картинки' : ''}`;
  $('svc-retina').textContent = SVC_TEXT.retina[st.retina] || st.retina;
  $('svc-ollama').textContent = st.model === 'missing' ? 'нет модели' : (SVC_TEXT.ollama[st.ollama] || st.ollama);
  const vd = $('vision-dot');
  vd.className = `vdot${st.retina === 'ok' || st.retina === 'external' ? ' on' : st.retina === 'down' ? ' off' : ''}`;
  $('stat-vision').textContent = SVC_TEXT.retina[st.retina] || st.retina;
  dot('s-retina-dot', st.retina);
  dot('s-ollama-dot', ollama);
  $('s-retina').textContent = `Зрение (Screen Retina) — ${SVC_TEXT.retina[st.retina] || st.retina}`;
  $('s-ollama').textContent = `Локальная модель (Ollama) — ${st.model === 'missing' ? 'нет модели qwen2.5:7b' : SVC_TEXT.ollama[st.ollama] || st.ollama}`;
}
$('s-restart').addEventListener('click', restartServices);

// ── Сценарии ───────────────────────────────────────────────────────────────────────────
let scenarios = [];
let current = null;
let scnRev = -1;
let lastRun = { running_id: null, log_for: null, last_kind: '', last_text: '' };

async function loadScenarios(selectId) {
  scenarios = await api().list_scenarios();
  renderScnList();
  const pick = scenarios.find((s) => s.id === (selectId || current?.id)) || scenarios[0];
  if (pick) await selectScenario(pick.id, false);
  else { current = null; $('scn-form').classList.add('hidden'); $('scn-empty').classList.remove('hidden'); showScenarioHead(); }
}
function renderScnList() {
  const box = $('scn-list');
  box.textContent = '';
  for (const s of scenarios) {
    const b = el('button', `nav-i scn-i${current && s.id === current.id ? ' active' : ''}${s.running ? ' is-running' : ''}${s.queued ? ' is-queued' : ''}`);
    b.type = 'button';
    b.append(svgUse('i-doc'), el('span', 'nav-i__txt', s.name || 'Без названия'), el('span', 'sdot'));
    if (s.queued) b.title = `в очереди: ${s.queue_pos}`;
    b.addEventListener('click', () => { selectScenario(s.id, true); });
    box.appendChild(b);
  }
}
async function selectScenario(id, show) {
  const s = await api().get_scenario(id);
  if (!s) return;
  current = s;
  $('scn-empty').classList.add('hidden');
  $('scn-form').classList.remove('hidden');
  $('f-name').value = s.name || '';
  $('f-goal').value = s.goal || '';
  $('f-hints').value = (s.hints || []).join('\n');
  $('f-browser').value = s.browser || '';
  $('f-url').value = s.start_url || '';
  $('f-incognito').checked = !!s.incognito;
  $('f-steps').value = s.max_steps;
  $('f-monitor').value = String(s.monitor || 0);
  $('f-speed').value = s.speed;
  $('f-noise').value = s.noise;
  $('f-refine').checked = !!s.refine;
  $('f-learn').checked = !!s.learn;
  $('f-record').checked = !!s.record;
  $('f-fast').checked = !!s.fast_replay;
  J_RUN.clear();
  if (s._log) J_RUN.addAll(s._log.split('\n').filter(Boolean).map((t) => ({ t: Date.now() / 1000, src: 'run', text: t })));
  renderScnList();
  renderRunState();
  if (show || currentView === 'scenarios') { if (currentView !== 'scenarios') switchView('scenarios'); else showScenarioHead(); }
}
function showScenarioHead() {
  if (currentView !== 'scenarios') return;
  if (!current) { setHead('Сценарии', [headButton('Новый сценарий', 'i-plus', newScenario, 'btn btn--dark btn--sm')]); return; }
  const running = lastRun.running_id === current.id;
  const acts = [
    headButton('', 'i-copy', duplicateScenario, 'btn btn--ghost btn--sm'),
    headButton('', 'i-trash', deleteScenario, 'btn btn--ghost btn--sm'),
  ];
  acts[0].title = 'Дублировать';
  acts[1].title = 'Удалить';
  if (running) acts.push(headButton('Стоп', 'i-stop', () => api().stop_run(), 'btn btn--line btn--sm'));
  const run = headButton(running ? 'Идёт…' : 'Запустить', 'i-play', runScenario, 'btn btn--dark btn--sm', 'h-run');
  run.disabled = running;
  acts.push(run);
  setHead(current.name || 'Без названия', acts);
}
function renderRunState() {
  if (!current) return;
  const mine = lastRun.running_id === current.id;
  const done = !mine && lastRun.log_for === current.id && lastRun.last_kind;
  dot('run-dot', mine ? 'starting' : done ? (lastRun.last_kind === 'done' ? 'ok' : 'down') : '');
  if (mine) $('run-dot').className = 'status-dot busy';
  $('run-text').textContent = mine ? (lastRun.last_text || 'идёт…') : done ? lastRun.last_text : 'готов';
}
let saveTimer = 0;
function scheduleSave() { clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 400); }
async function saveNow() {
  if (!current) return;
  const data = {
    name: $('f-name').value.trim() || 'Без названия',
    goal: $('f-goal').value,
    hints: $('f-hints').value.split('\n').map((x) => x.trim()).filter(Boolean),
    browser: $('f-browser').value,
    start_url: $('f-url').value.trim(),
    incognito: $('f-incognito').checked,
    max_steps: parseInt($('f-steps').value, 10) || 15,
    monitor: parseInt($('f-monitor').value, 10) || 0,
    speed: parseFloat($('f-speed').value) || 1,
    noise: parseFloat($('f-noise').value) || 0,
    refine: $('f-refine').checked,
    learn: $('f-learn').checked,
    record: $('f-record').checked,
    fast_replay: $('f-fast').checked,
  };
  await api().save_scenario(current.id, data);
  Object.assign(current, data);
  const item = scenarios.find((s) => s.id === current.id);
  if (item && item.name !== data.name) { item.name = data.name; renderScnList(); showScenarioHead(); }
}
['f-name', 'f-goal', 'f-hints', 'f-url', 'f-steps', 'f-speed', 'f-noise'].forEach((id) => $(id).addEventListener('input', scheduleSave));
['f-browser', 'f-monitor', 'f-incognito', 'f-refine', 'f-learn', 'f-record', 'f-fast'].forEach((id) => $(id).addEventListener('change', saveNow));

async function newScenario() { const s = await api().new_scenario(); await loadScenarios(s.id); switchView('scenarios'); setTimeout(() => $('f-name').select(), 80); }
async function duplicateScenario() { if (!current) return; await saveNow(); const s = await api().duplicate_scenario(current.id); await loadScenarios(s ? s.id : null); toast({ title: 'Сценарий скопирован' }); }
async function deleteScenario() {
  if (!current) return;
  const ok = await modal({ title: 'Удалить сценарий?', text: `«${current.name || 'Без названия'}» исчезнет из списка. Отменить это нельзя.`, ok: 'Удалить', tone: 'danger', icon: 'i-trash' });
  if (!ok) return;
  const r = await api().delete_scenario(current.id);
  if (r && r.error) { toast({ kind: 'warn', title: 'Не удалось', text: r.error }); return; }
  current = null;
  await loadScenarios(null);
}
async function runScenario() {
  if (!current) return;
  await saveNow();
  const r = await api().run_scenario(current.id);
  if (r && r.error) { toast({ kind: 'warn', title: 'Не запустилось', text: r.error }); return; }
  J_RUN.clear();
  toast({ title: 'Запускаю', text: 'Через 3 секунды агент возьмёт мышь — переключитесь в нужное окно.' });
  launched();
}
$('btn-new').addEventListener('click', newScenario);
let recording = false;
$('btn-record').addEventListener('click', async () => {
  if (recording) { await api().record_stop(); return; }
  const goal = await modal({ title: 'Записать сценарий показом', text: 'Что показываете? После «Начать» переключитесь в нужное окно и сделайте всё мышью и клавиатурой. Закончить — Ctrl+Alt+S или пункт трея «Стоп запись».', ok: 'Начать запись', icon: 'i-rec', input: 'Записанный сценарий' });
  if (!goal) return;
  const r = await api().record_start(goal);
  if (r && r.error) toast({ kind: 'warn', title: 'Запись не началась', text: r.error });
  else toast({ title: 'Идёт запись', text: 'Покажите действия. Закончить — Ctrl+Alt+S.' });
});

// ── Настройки ──────────────────────────────────────────────────────────────────────────
let lastServices = null;
function loadSettings() { if (lastServices) renderServices(lastServices); }
$('s-default-browser').addEventListener('change', (e) => api().set_default_browser(e.target.value));
$('open-data-btn').addEventListener('click', () => api().open_data_dir());
$('open-data-link').addEventListener('click', (e) => { e.preventDefault(); api().open_data_dir(); });
$('win-min').addEventListener('click', () => api().win_minimize());
$('win-close').addEventListener('click', () => api().win_hide());

// ── Опрос раз в секунду ────────────────────────────────────────────────────────────────
let since = 0;
let lastCounts = { approvals: -1, inbox: -1 };
async function poll() {
  const p = await api().poll(since).catch(() => null);
  if (!p) return;
  since = p.last;
  J_ALL.addAll(p.logs);
  J_TEAM.addAll(p.logs);
  if (current && current.id === p.log_for) J_RUN.addAll(p.logs);
  for (const n of p.notes) toast(n);

  // команда
  team = { busy: p.team_busy };
  const busy = p.team_busy || !!p.running_id;
  $('team-dot').className = `status-dot${busy ? ' busy' : ''}`;
  $('team-text').textContent = p.quick ? 'Быстрая задача' : p.team_busy ? 'Команда работает' : p.running_id ? 'Идёт сценарий' : p.recording ? 'Идёт запись' : 'Команда свободна';
  $('stop-all-btn').classList.toggle('hidden', !busy);
  $('t-run').disabled = p.team_busy;
  $('t-plan').disabled = p.team_busy;
  $('q-run').disabled = busy;
  ['l-run', 'l-match', 'd-run', 'd-match'].forEach((id) => { $(id).disabled = busy; });
  renderPlan(p.team_plan || []);
  if (currentView === 'pilot' && (currentPanel === 'task' || currentPanel === 'quick')) renderHead();

  // счётчики «нужно вам»
  setBadge('badge-approvals', p.approvals);
  setBadge('badge-inbox', p.inbox);
  if (p.approvals !== lastCounts.approvals && currentPanel === 'approvals' && currentView === 'pilot') renderApprovals();
  if (p.inbox !== lastCounts.inbox && currentPanel === 'inbox' && currentView === 'pilot') renderInbox();
  lastCounts = { approvals: p.approvals, inbox: p.inbox };
  renderServices(p.services);

  // сценарии
  if (p.scn_rev !== scnRev) { scnRev = p.scn_rev; await loadScenarios(current?.id); }
  let changed = false;
  scenarios.forEach((s) => {
    const r = p.running_id === s.id; const q = p.queue.indexOf(s.id);
    if (!!s.running !== r || !!s.queued !== q >= 0) changed = true;
    s.running = r; s.queued = q >= 0; s.queue_pos = q + 1;
  });
  if (changed) renderScnList();
  const runChanged = lastRun.running_id !== p.running_id || lastRun.last_text !== p.last_text;
  lastRun = { running_id: p.running_id, log_for: p.log_for, last_kind: p.last_kind, last_text: p.last_text };
  if (runChanged) { renderRunState(); showScenarioHead(); }
  if (recording !== p.recording) {
    recording = p.recording;
    const b = $('btn-record');
    b.textContent = '';
    b.append(svgUse(recording ? 'i-stop' : 'i-rec'), recording ? 'Остановить запись' : 'Записать показом');
  }
}

// ── Старт ──────────────────────────────────────────────────────────────────────────────
let booted = false;
async function boot() {
  if (booted) return;
  booted = true;
  const info = await api().bootstrap();
  const browsers = info.browsers.map((b) => [b, b || 'по умолчанию']);
  fillOptions($('f-browser'), browsers);
  fillOptions($('s-default-browser'), info.browsers.map((b) => [b, b || 'системный']));
  const mons = [['0', 'авто']];
  for (let i = 1; i < (info.monitors || 1); i += 1) mons.push([String(i), `монитор ${i}`]);
  fillOptions($('f-monitor'), mons);
  document.querySelectorAll('select').forEach(enhanceSelect);
  $('s-default-browser').value = info.default_browser || '';
  $('s-vision').textContent = info.vision || '';
  $('s-vendored').textContent = info.vendored || '—';
  renderServices(info.services);
  await loadScenarios(null);
  const h = location.hash.slice(1);                 // #vision, #scenarios… — сразу в раздел
  if (h === 'scenarios' || h === 'settings') switchView(h);
  else switchPanel(PANEL_TITLES[h] ? h : 'overview');
  await poll();
  setInterval(poll, 1000);
  setInterval(() => { if (currentView === 'pilot' && currentPanel === 'overview') refreshOverview(); }, 15000);
}

const apiReady = () => window.pywebview && window.pywebview.api && typeof window.pywebview.api.bootstrap === 'function';
function tryBoot() { if (apiReady()) boot(); }
function startWhenReady() {
  if (apiReady()) { boot(); return; }
  window.addEventListener('pywebviewready', tryBoot, { once: true });
  let tries = 0;
  const t = setInterval(() => {
    if (booted) { clearInterval(t); return; }
    if (apiReady()) { clearInterval(t); boot(); }
    if (++tries > 60) clearInterval(t);
  }, 150);
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', startWhenReady);
else startWhenReady();
