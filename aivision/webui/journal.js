'use strict';
// Журнал по полочкам: строки движка (команда, прогоны, службы) раскладываются в карточки
// задач, внутри — подзадачи агентов и шаги на экране. Рассуждения модели и изменения
// экрана свёрнуты под «почему», шум (служебные пометки) — в метки шага. Порядок —
// хронологический, новое внизу; если читаешь выше — экран не дёргается.

const J_VERB = { click: 'клик', double_click: 'двойной клик', right_click: 'правый клик', type: 'ввод',
  key: 'клавиши', scroll: 'прокрутка', hover: 'наведение', drag: 'перетаскивание', wait: 'ожидание',
  long_press: 'удержание', stroke: 'штрих', focus: 'окно', cast: 'навык' };
const J_SRC = { team: 'команда', run: 'сценарий', svc: 'службы', ui: 'окно' };
const J_END = { done: 'готово', fail: 'не вышло', stuck: 'застрял', limit: 'кончились шаги', aborted: 'остановлено',
  blind: 'не видел экран', partial: 'частично', held: 'ждёт «да»', skipped: 'пропущено', escalated: 'эскалация',
  error: 'ошибка' };
const J_PILL = { done: 'pill--applied', fail: 'pill--alert', stuck: 'pill--alert', blind: 'pill--alert', limit: 'pill--good',
  partial: 'pill--good', aborted: 'pill--bad', held: 'pill--held', skipped: 'pill--bad', work: 'pill--work',
  escalated: 'pill--alert', error: 'pill--alert' };

function jEl(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}
function jTime(t) { return new Date(t * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
function jAction(s) {
  const m = s.match(/^(\w+)\s+(.*)$/);
  let out = m && J_VERB[m[1]] ? `${J_VERB[m[1]]} ${m[2]}` : s;
  return out.replace(/\[\d+\]\s*/g, '').replace(/^клавиши клавиши /, 'клавиши ').replace(/\s+/g, ' ').trim();
}

class Journal {
  constructor(box, { empty = null, sources = null, compactTitle = false } = {}) {
    this.box = box;
    this.empty = empty;
    this.sources = sources;          // null — все источники
    this.compactTitle = compactTitle;
    this.task = null;
    this.sub = null;
    this.step = null;
  }

  clear() {
    this.box.textContent = '';
    this.task = this.sub = this.step = null;
    if (this.empty) this.box.appendChild(this.empty);
  }

  addAll(lines) {
    if (!lines.length) return;
    const stick = this.box.scrollHeight - this.box.scrollTop - this.box.clientHeight < 60;
    lines.forEach((x) => this.add(x));
    if (stick) this.box.scrollTop = this.box.scrollHeight;
  }

  add(x) {
    if (this.sources && !this.sources.includes(x.src)) return;
    if (this.empty && this.empty.parentNode) this.empty.remove();
    const text = String(x.text || '').trim();
    if (!text) return;
    let m;

    // ── начало и конец задачи ──
    if ((m = text.match(/^🧭 (?!план на )(.+)$/)) && x.src === 'team') return this.startTask(m[1], x);
    if ((m = text.match(/^✅ одобрено: (.+?)(?: — выполняю)?$/))) return this.startTask(m[1], x, 'одобрено');
    if ((m = text.match(/^▶ сценарий «(.+)»$/))) return this.startTask(m[1], x, 'сценарий');
    if ((m = text.match(/^↻ заново: (.+)$/))) return this.startTask(m[1], x, 'заново');
    if ((m = text.match(/^⚡ быстрая задача: (.+)$/))) return this.startTask(m[1], x, 'быстро, без плана');
    if ((m = text.match(/^== итог: (\S+) ==$/)) || (m = text.match(/^→ (\w+): (.*)$/))) return this.endTask(m[1], m[2] || '', x);
    if ((m = text.match(/^== (.+) ==$/))) return this.endTask(/^готово/.test(m[1]) ? 'done' : 'fail', m[1], x, true);

    if (x.src === 'svc' || x.src === 'ui') return this.loose(text, x);
    if (!this.task) this.startTask('Работа агента', x, '', true);

    // ── подзадачи оркестратора ──
    if ((m = text.match(/^▶ шаг (\d+): \[(.+?)\] (.+)$/))) return this.startSub(+m[1], m[2], m[3]);
    if ((m = text.match(/^→ шаг (\d+): (\w+): ?(.*)$/))) return this.endSub(m[2], m[3]);

    // ── шаг на экране ──
    if ((m = text.match(/^── шаг (\d+)\/(\d+) · «(.*)» · (\d+) элементов за ([\d.]+) с \(([^)]*)\)/))) return this.startStep(m, x);
    if ((m = text.match(/^▶ (.+?)\s+\[([^\]]*)\] · (\d+) с на решение$/))) return this.stepAction(jAction(m[1]), m[2], m[3]);
    if ((m = text.match(/^👀 (.+)$/))) return this.stepResult(m[1]);
    if ((m = text.match(/^💬 (.+)$/))) return this.stepNote('Почему', m[1]);
    if ((m = text.match(/^✔️ (.+)$/))) return this.stepNote('Прошлый шаг', m[1]);
    if ((m = text.match(/^📝 текстовый шаг: (\d+) с/))) return this.stepTag(`текстом · ${m[1]} с`, 'tag--teal');
    if ((m = text.match(/^🧠 marked: (\d+) с/))) return this.stepTag(`скриншот · ${m[1]} с`);
    if ((m = text.match(/^🧠 zoom\w*: (\d+) с/))) return this.stepTag(`уточнение · ${m[1]} с`);
    if ((m = text.match(/^🖼 модели нужен скриншот: (.*)$/))) return this.stepNote('Нужен скриншот', m[1]);
    if ((m = text.match(/^\+ (.+)$/))) return this.stepExtra(`+ ${jAction(m[1])}`);
    if ((m = text.match(/^🪟 переключился на окно: (.+?)( — вместо клика .*)?$/)) && this.step && this.step.pending) {
      this.stepAction(`переключиться на окно ${m[1].split(' · ')[0]}`);
      this.stepResult(`окно выведено вперёд${m[1].includes(' · ') ? ` · ${m[1].split(' · ').slice(1).join(' · ')}` : ''}`);
      if (m[2]) this.stepTag('вместо панели задач');
      return undefined;
    }
    if ((m = text.match(/^🪟 окна «(.+)» нет среди открытых$/)) && this.step && this.step.pending) {
      this.stepAction(`переключиться на окно «${m[1]}»`);
      this.stepResult(`экран НЕ изменился: такого окна нет среди открытых`);
      return undefined;
    }
    if ((m = text.match(/^🪟 (.+)$/))) return this.action('окно', m[1], 'focus');

    if ((m = text.match(/^⚡ рефлекс: (.+)$/))) return this.action('рефлекс', m[1], 'reflex');
    if ((m = text.match(/^🧠 локальная модель: (.+)$/))) return this.action('локальная модель', m[1], 'local');
    if ((m = text.match(/^🖼 (.+)$/))) return this.action('иконка', m[1], 'icon');
    if ((m = text.match(/^🚫 (.+?) — уже (\d+) раза? (без результата|возвращало назад|приводило в тупик), отклоняю$/)) && this.step) {
      this.stepAction(jAction(m[1]));
      this.step.res.textContent = `не выполнено: уже ${m[2]} раза ${m[3]}`;
      this.step.res.className = 'jstep__res is-none';
      return undefined;
    }
    if (((m = text.match(/^🗔 (.+?) — (мимо всплывающего окна)/)) || (m = text.match(/^🚫 (.+?) — (это не «.+?» из задачи)/))) && this.step) {
      this.stepAction(jAction(m[1]));
      this.step.res.textContent = `не выполнено: ${m[2]}`;
      this.step.res.className = 'jstep__res is-none';
      return undefined;
    }
    if ((m = text.match(/^🏳 не выходит: (.+)$/)) && this.step && this.step.pending) {
      this.stepAction('сдаётся');
      this.step.res.textContent = m[1];
      this.step.res.className = 'jstep__res is-none';
      return undefined;
    }
    if ((m = text.match(/^⤴ рано сдаваться: (.+?) — возвращаюсь/)) && this.step && this.step.pending) {
      this.stepAction('хотела сдаться — вернул исправлять');
      this.step.res.textContent = m[1];
      this.step.res.className = 'jstep__res is-none';
      return undefined;
    }
    if ((m = text.match(/^(⚠️|🚫|✋|🛡|🚦|⤼|⤴|⟲) ?(.+)$/))) return this.note(m[2], 'warn');
    if ((m = text.match(/^(↻|🎯|📋|📚|📝|🧭|🧠|🎧|🎨|✍️|🎬|🖥|⏹|🗔|🎮|🎥|🎞|🖐|🧭|🎯|⌨|👁) ?(.+)$/))) return this.note(m[2], 'info');
    if (/^план готов/.test(text) || /^нажмите «Выполнить»/.test(text)) return this.note(text, 'info');
    if (this.task && this.task.done) { this.task.answer.appendChild(jEl('div', '', text)); return; }   // ответ после итога
    this.note(text, 'plain');
  }

  // ── задача ──
  startTask(title, x, kind = '', implicit = false) {
    this.endTaskQuiet();
    const card = jEl('div', 'jtask');
    const head = jEl('div', 'jtask__head');
    const ttl = jEl('div', 'jtask__title');
    ttl.append(jEl('b', '', title));
    const meta = jEl('div', 'jtask__meta');
    meta.append(jEl('span', `jsrc jsrc--${x.src}`, J_SRC[x.src] || x.src), jEl('time', '', jTime(x.t)));
    if (kind && !implicit) meta.append(jEl('span', '', kind));
    ttl.append(meta);
    const pill = jEl('span', 'pill pill--work', 'идёт');
    head.append(ttl, pill);
    const body = jEl('div', 'jtask__body');
    const end = jEl('div', 'jtask__end hidden');
    const answer = jEl('div', 'jtask__answer');
    end.appendChild(answer);
    card.append(head, body, end);
    this.box.appendChild(card);
    this.task = { card, body, pill, end, answer, steps: 0, done: false };
    this.sub = this.step = null;
  }

  endTask(status, answer, x, keepAnswer = false) {
    if (!this.task) this.startTask('Работа агента', x, '', true);
    const t = this.task;
    t.done = true;
    t.pill.className = `pill ${J_PILL[status] || 'pill--bad'}`;
    t.pill.textContent = keepAnswer ? answer.split(' · ')[0] : (J_END[status] || status);
    t.end.classList.remove('hidden');
    t.answer.textContent = '';
    const first = keepAnswer ? answer.split(' · ').slice(1).join(' · ') : answer;
    if (first) t.answer.appendChild(jEl('div', 'jtask__sum', first));
    this.sub = this.step = null;
  }

  endTaskQuiet() {
    if (this.task && !this.task.done && this.task.pill.textContent === 'идёт') {
      this.task.pill.className = 'pill pill--bad';
      this.task.pill.textContent = 'прервано';
    }
  }

  // ── подзадача ──
  startSub(i, agent, task) {
    const s = jEl('div', 'jsub');
    const head = jEl('div', 'jsub__head');
    head.append(jEl('span', 'jsub__n', String(i + 1)), jEl('b', '', agent));
    const pill = jEl('span', 'pill pill--work', 'идёт');
    head.appendChild(pill);
    s.append(head, jEl('div', 'jsub__task', task));
    this.task.body.appendChild(s);
    this.sub = { el: s, pill };
    this.step = null;
  }

  endSub(status, answer) {
    if (!this.sub) return this.note(`${J_END[status] || status}: ${answer}`, status === 'done' ? 'info' : 'warn');
    this.sub.pill.className = `pill ${J_PILL[status] || 'pill--bad'}`;
    this.sub.pill.textContent = J_END[status] || status;
    if (answer) this.sub.el.appendChild(jEl('div', 'jsub__answer', answer));
    this.sub = null;
    this.step = null;
  }

  // ── шаг на экране ──
  startStep(m, x) {
    const [, n, , win, els, secs, src] = m;
    const row = jEl('div', 'jstep');
    const num = jEl('span', 'jstep__n', n);
    const main = jEl('div', 'jstep__main');
    const act = jEl('div', 'jstep__act is-thinking', 'думает');
    const res = jEl('div', 'jstep__res');
    const details = jEl('details', 'jstep__more');
    details.appendChild(jEl('summary', '', 'почему'));
    const more = jEl('div', 'jstep__notes');
    details.appendChild(more);
    main.append(act, res, details);
    const side = jEl('div', 'jstep__side');
    const tags = jEl('div', 'jstep__tags');
    const w = jEl('span', 'jwin', win || '—');
    w.title = `${win} · ${els} элементов за ${secs} с (${src})`;
    side.append(tags, w, jEl('time', '', jTime(x.t)));
    row.append(num, main, side);
    details.classList.add('hidden');
    (this.sub ? this.sub.el : this.task.body).appendChild(row);
    this.step = { row, act, res, details, more, tags, pending: true };
    this.task.steps += 1;
  }

  ensureStep() {
    if (this.step) return this.step;
    this.startStep([null, '·', '', '', '', '', ''], { t: Date.now() / 1000 });
    return this.step;
  }

  stepAction(text, source) {
    const s = this.ensureStep();
    s.pending = false;
    s.act.classList.remove('is-thinking');
    s.act.textContent = text;
    s.act.title = text;
    if (source && source !== 'mark' && source !== '—') this.stepTag({ grid: 'по сетке', zoom: 'уточнено', snap: 'притянуто' }[source] || source);
  }

  stepResult(text) {
    const s = this.ensureStep();
    const [main, screen] = text.split(' · на экране: ');
    s.res.textContent = main.replace(/^экран НЕ изменился/, 'экран не изменился');
    s.res.className = `jstep__res ${/НЕ изменился/.test(main) ? 'is-none' : 'is-ok'}`;
    if (screen) {
      const list = jEl('ul', 'jstep__screen');
      screen.split('; ').slice(0, 10).forEach((c) => list.appendChild(jEl('li', '', c)));
      this.stepNote('На экране', '', list);
    }
  }

  stepNote(label, text, node = null) {
    const s = this.ensureStep();
    const p = jEl('div', 'jnote');
    p.appendChild(jEl('b', '', label));
    if (node) p.appendChild(node); else p.appendChild(document.createTextNode(text));
    s.more.appendChild(p);
    s.details.classList.remove('hidden');
  }

  stepTag(text, cls = '') {
    const s = this.ensureStep();
    s.tags.appendChild(jEl('span', `tag ${cls}`, text));
  }

  stepExtra(text) {
    const s = this.ensureStep();
    s.act.textContent += `  ${text}`;
  }

  // ── прочее ──
  action(kind, text, cls) {
    const host = this.step ? this.step.row.parentNode : (this.sub ? this.sub.el : this.task.body);
    const row = jEl('div', `jact jact--${cls}`);
    row.append(jEl('span', 'jact__k', kind), jEl('span', '', text));
    host.appendChild(row);
  }

  note(text, cls) {
    const host = this.sub ? this.sub.el : this.task ? this.task.body : this.box;
    host.appendChild(jEl('div', `jline jline--${cls}`, text));
  }

  loose(text, x) {
    if (this.lastLoose === `${x.src}|${text}`) return;
    this.lastLoose = `${x.src}|${text}`;
    const row = jEl('div', `jloose jloose--${x.src}`);
    row.append(jEl('time', '', jTime(x.t)), jEl('span', `jsrc jsrc--${x.src}`, J_SRC[x.src] || x.src), jEl('span', '', text));
    this.box.appendChild(row);
  }
}
