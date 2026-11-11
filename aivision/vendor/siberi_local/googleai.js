'use strict';
/*
  Копия siberiai-v5/server/services/googleai.js (origin/main 98d1c888, 14.10.2026) для Siberi
  Local. Отличие одно: playwright-core вместо playwright — браузеры Playwright не скачиваем,
  работаем с установленным Edge. Обновлять — копией из siberiai-v5 с той же заменой.

  GoogleAiService — драйвер «Режима ИИ» Google Поиска через Playwright по CDP.

  Зачем так: gemini.google.com из РФ не работает, а Gemini внутри google.com/Режим ИИ
  — работает. Поэтому ходим не в Gemini напрямую, а в поиск, включаем режим ИИ и
  читаем ответ оттуда.

  Проверено на сервере 15 августа 2026: полный путь (главная → «Режим ИИ» → промт →
  ответ) отрабатывает, ответ снимается из [data-container-id="main-col"].

  ВАЖНО про капчу: прямой заход на /search?...&udm=50 без кук с серверного IP ловит
  «Мы зарегистрировали подозрительный трафик» (/sorry/index). Лечится тем, что мы
  СНАЧАЛА открываем главную google.com (получаем куки) и только потом жмём режим ИИ.
  Этот порядок здесь обязателен, не «лишний шаг».

  Селекторы принципиально НЕ по классам: Google крутит их (L3eUgb, RNNXgb и т.п.)
  и любой такой путь развалится через неделю. Ищем по подписи и по data-атрибутам.
*/
const { chromium } = require('playwright-core');
const fs = require('fs');
const os = require('os');
const path = require('path');

const CDP_ENDPOINT = process.env.GOOGLEAI_CDP || 'http://127.0.0.1:9223';
const HOME = 'https://www.google.com/';

const CONFIG = {
  // Кнопка включения режима ИИ — по видимой подписи/aria-label, не по классу.
  aiModeButton: 'button:has-text("Режим ИИ"), a:has-text("Режим ИИ"), [aria-label*="Режим ИИ"], [aria-label*="AI Mode"]',
  // ВАЖНО: input[name="q"] здесь быть НЕ должно. Это обычная строка поиска, она
  // живёт на той же странице, цеплялась первой — и Enter уносил в обычную выдачу
  // вместо режима ИИ (симптом: «GoogleAI: пустой ответ» на каждой задаче).
  input: 'textarea:visible, [contenteditable="true"]:visible',
  // До какой длины промт уходит прямо в URL. Это самый быстрый путь: первые
  // буквы ответа приходят за ~5с против ~10с у пути с вложением, так что держать
  // порог низким — значит без нужды гонять всех по медленной дороге.
  // Потолок нащупан замерами 20.08.2026: 2400 символов проходят, 3000 — Google
  // отвечает «400. That's an error… malformed or illegal request». Берём 1900 с
  // запасом; всё длиннее по-прежнему уходит вложением.
  urlLimit: 1900,
  // С 11.10.2026 порог — по длине ЗАКОДИРОВАННОГО запроса: Google ограничивает адрес, а
  // кириллица кодируется в 6 байт на символ, латиница в 1-3. 14400 = проверенные 2400
  // кириллических символов; смешанный и латинский текст теперь чаще идёт быстрым путём.
  urlBytesLimit: 14400,
  // Контейнер ответа. data-container-id держится стабильнее классов.
  answer: '[data-container-id="main-col"]',
  captchaRe: /\/sorry\/|подозрительный трафик|unusual traffic/i,
  // Лимит режима ИИ на аккаунт: Google отвечает этой фразой вместо ответа. До 11.10.2026
  // она уходила клиентам как обычный «ответ» (60 символов) — теперь это ошибка, и Google
  // пропускается на limitCooldownMs, чтобы не тратить на него время каждого запроса.
  limitRe: /исчерпали лимит на ответы от ИИ|reached (?:your|the) (?:daily )?limit for AI/i,
  limitCooldownMs: 10 * 60 * 1000,
  // Признак «ответ дописан»: под готовым ответом Google показывает кнопки оценки и
  // копирования. Замер 11.10.2026: в режиме по ссылке ответ приходит целиком за ~2.4с,
  // вместе с этими кнопками, — и ждать ещё 2-4с «пока текст не перестанет меняться»
  // было чистой потерей на каждом запросе.
  doneLabelRe: 'Хороший ответ|Плохой ответ|Скопировать текст|Good response|Bad response|Copy text',
  pollMs: 150,
  // Запасной признак конца, если кнопок нет (вёрстка поменялась): текст неподвижен.
  stableMs: 1500,
  appearTimeoutMs: 45000,
  // Сколько ждём ПЕРВЫЕ буквы ответа. Раньше этого предела не было: если ответ
  // не появлялся вовсе, цикл крутился до общего дедлайна (2 минуты) и только
  // потом отдавал задачу следующему провайдеру. Под нагрузкой это и создавало
  // затор — успешный ответ приходит за 7-8с, так что ждать дольше смысла нет.
  firstTokenMs: 25000,
  minAnswer: 40,
  // Прикрепить можно только картинку или PDF — .txt инпуты не принимают,
  // поэтому контекст печатаем в PDF.
  // ВАЖНО: инпута с accept*="pdf" у Google больше НЕТ (проверено 20.08.2026).
  // На странице режима ИИ живут два инпута: image/… и универсальный */*. Старый
  // селектор не находил ничего, setInputFiles падал по таймауту в 30с — и весь
  // /app сваливался на медленный фолбэк. Целимся в НЕ-картиночный инпут.
  fileInput: 'input[type="file"]:not([accept*="image/"])',
  // Второй, картиночный инпут — тот самый, который выше сознательно обходили для
  // PDF. До 11.09.2026 картинки, приложенные пользователем в /chat (pendingImages
  // в AiService), сюда просто не доезжали: generate() не принимал изображение
  // вообще, только текст — вложение читалось на сервере и тут же терялось.
  imageFileInput: 'input[type="file"][accept*="image/"]',
  // Готовность вложения ждём опросом, а не сном. Замер: чип файла появляется в
  // UI за ~0.5с, так что прежний глухой sleep на 8с был чистой потерей времени
  // на КАЖДОМ запросе. Здесь — только потолок ожидания.
  uploadReadyMs: 6000,
  // Уборка истории. Аккаунт залогинен, и КАЖДЫЙ запрос оседает «цепочкой» в
  // боковой панели — за день их набираются сотни, владелец чистил руками.
  // Путь до удаления: гамбургер → кебаб строки → «Удалить цепочку» → подтвердить.
  // Целимся по aria-label и тексту: классы (w7Y0zd, VdQ69b, j8c53) Google крутит.
  sidebarButton: '[aria-label="Открыть боковую панель"], [aria-label*="боковую панель"]',
  historyRow: 'li.j8c53, [role="listitem"]',
  rowKebab: 'Другие действия',
  deleteButton: 'Удалить цепочку',
};

// Хвосты интерфейса, которые попадают в innerText вместе с ответом.
const JUNK = [
  /ИИ может ошибаться\.?\s*Проверяйте его ответы\.?/gi,
  // «В ответах искусственного интеллекта могут быть ошибки. Если вам требуется … Подробнее…»
  /(?:В ответах )?искусственного интеллекта могут (?:быть|содержаться) ошибки\.[^\n]*/gi,
  /AI responses may include mistakes\.?/gi,
  /Показать больше|Показать меньше|Похожие результаты|Поделиться|Копировать|Хороший ответ|Плохой ответ|Ещё варианты/gi,
];

function clean(text) {
  let t = String(text || '');
  for (const re of JUNK) t = t.replace(re, ' ');
  return t.replace(/[ \t]+/g, ' ').replace(/\s*\n\s*/g, '\n').trim();
}

const escapeHtml = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');

/**
 * Печатает длинный контекст в PDF — единственный формат вложения, который
 * режим ИИ принимает наравне с картинками. Идём через CDP Page.printToPDF:
 * обычный page.pdf() в НЕ headless-браузере не работает, а браузер у нас
 * headed (он же обслуживает остальные драйверы).
 */
async function _makePdf(ctx, text) {
  const base = path.join(os.tmpdir(), `gai-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
  const htmlPath = `${base}.html`;
  const pdfPath = `${base}.pdf`;
  fs.writeFileSync(htmlPath,
    `<html><meta charset="utf-8"><body style="font:13px sans-serif;white-space:pre-wrap">${escapeHtml(text)}</body></html>`,
    'utf8');

  const tmp = await ctx.newPage();
  try {
    await tmp.goto('file://' + htmlPath, { waitUntil: 'domcontentloaded', timeout: 20000 });
    const cdp = await ctx.newCDPSession(tmp);
    const { data } = await cdp.send('Page.printToPDF', { printBackground: false, paperWidth: 8.27, paperHeight: 11.69 });
    fs.writeFileSync(pdfPath, Buffer.from(data, 'base64'));
  } finally {
    await tmp.close().catch(() => {});
    fs.promises.unlink(htmlPath).catch(() => {});
  }
  return pdfPath;
}

/**
 * Убирает за собой цепочку из истории режима ИИ.
 * Ищем строку СТРОГО по нашему тексту запроса: удалять «самую верхнюю» нельзя —
 * владелец пользуется этим же аккаунтом, и под руку попадёт его диалог.
 * Не нашли свою строку — молча выходим, лучше оставить след, чем снести чужое.
 */
async function _deleteThread(page, queryText) {
  const mark = String(queryText || '').trim().slice(0, 25);
  if (!mark) return;

  await page.locator(CONFIG.sidebarButton).first().click({ timeout: 8000 });
  await page.waitForTimeout(1500);

  const opened = await page.evaluate(([rowSel, kebabLabel, m]) => {
    const li = [...document.querySelectorAll(rowSel)].find((x) => (x.innerText || '').includes(m));
    if (!li) return false;
    const kebab = [...li.querySelectorAll('button')]
      .find((x) => new RegExp(kebabLabel, 'i').test(x.getAttribute('aria-label') || ''));
    if (!kebab) return false;
    kebab.click();
    return true;
  }, [CONFIG.historyRow, CONFIG.rowKebab, mark]);
  if (!opened) return;
  await page.waitForTimeout(1200);

  const asked = await page.evaluate((label) => {
    const btn = [...document.querySelectorAll('button')]
      .find((x) => new RegExp('^' + label, 'i').test(x.getAttribute('aria-label') || ''));
    if (!btn) return false;
    btn.click();
    return true;
  }, CONFIG.deleteButton);
  if (!asked) return;
  await page.waitForTimeout(1200);

  // Подтверждение — отдельное окно с кнопкой «Удалить» (у Google это div, не button).
  await page.evaluate(() => {
    const el = [...document.querySelectorAll('div,button')]
      .filter((x) => x.offsetParent !== null && x.children.length === 0)
      .find((x) => /^Удалить$/i.test((x.textContent || '').trim()));
    if (el) el.click();
  });
  await page.waitForTimeout(1200);
}

let _browser = null;
async function _browserRef() {
  if (_browser && _browser.isConnected && _browser.isConnected()) return _browser;
  _browser = await chromium.connectOverCDP(CDP_ENDPOINT, { timeout: 30000 });
  return _browser;
}

// Куки главной google.com живут в профиле браузера, и ходить на главную перед каждым
// вопросом не нужно. С 11.10.2026 прогрев вообще ушёл с пути запроса: заранее держим
// ОДНУ запасную вкладку на google.com (renderer уже поднят, соединения с Google открыты,
// куки свежие). Запрос забирает её и сразу идёт на страницу ответа, а на её место в фоне
// готовится новая. Раз в HOME_TTL_MS запасная вкладка обновляет главную — куки не
// протухают, даже когда запросов долго нет.
const HOME_TTL_MS = 8 * 60 * 1000;
let _homeWarmedAt = 0;
// Запасные тёплые вкладки (по умолчанию 2: задачи демо-пути теперь идут параллельно,
// и вторая подряд не должна открывать вкладку с нуля). Каждая ~50-80 МБ.
const SPARES = Math.max(0, Number(process.env.GOOGLEAI_SPARES ?? 2));
const _spares = [];         // [{ page, warmedAt }]
let _spareFilling = null;   // промис наполнения — чтобы не плодить вкладки
let _keepWarmTimer = null;
let _limitedUntil = 0;      // Google ответил «исчерпали лимит» — пропускаем до этого момента

async function _warmHome(page) {
  await page.goto(HOME, { waitUntil: 'domcontentloaded', timeout: 40000 });
  if (CONFIG.captchaRe.test(page.url())) {
    _homeWarmedAt = 0;
    throw new Error('GoogleAI: капча на входе (серверный IP)');
  }
  _homeWarmedAt = Date.now();
}

const _fresh = (s) => s && !s.page.isClosed() && Date.now() - s.warmedAt < HOME_TTL_MS;

// Открывает на вкладке интерактивный режим ИИ (кнопкой — по адресу он без поля вложения)
// и ждёт поле вложения. Запасные вкладки держат его открытым заранее: длинный запрос
// и запрос с картинкой тогда сразу вкладывают файл — замер 11.10.2026: открытие режима
// стоило 1-3.3с на каждом таком запросе.
async function _openAiMode(page) {
  await page.locator(CONFIG.aiModeButton).first().click({ timeout: 15000 });
  await page.waitForFunction(
    () => document.querySelectorAll('input[type="file"]').length > 0,
    { timeout: 20000 },
  );
}

async function _prepareSpare(page) {
  await _warmHome(page);
  let aiMode = false;
  try { await _openAiMode(page); aiMode = true; } catch { /* короткому пути хватит и главной */ }
  return { page, warmedAt: Date.now(), aiMode };
}

// Дополняет запас тёплых вкладок до SPARES, по одной. Устаревшие обновляет на месте.
// Ошибки глотаем: запрос тогда просто откроет вкладку сам.
function _fillSpare() {
  if (_spareFilling) return _spareFilling;
  _spareFilling = (async () => {
    try {
      const browser = await _browserRef();
      const ctx = browser.contexts()[0];
      if (!ctx) return;
      // Закрытые выбрасываем, устаревшие — освежаем главной (куки вместе с ней).
      for (let i = _spares.length - 1; i >= 0; i -= 1) {
        const s = _spares[i];
        if (s.page.isClosed()) { _spares.splice(i, 1); continue; }
        if (!_fresh(s)) {
          try { _spares[i] = await _prepareSpare(s.page); }
          catch { _spares.splice(i, 1); await s.page.close().catch(() => {}); }
        }
      }
      while (_spares.length < SPARES) {
        const page = await ctx.newPage();
        try { _spares.push(await _prepareSpare(page)); }
        catch (e) { await page.close().catch(() => {}); throw e; }
      }
    } catch (e) {
      console.warn(`⚠️ GoogleAI: запасная вкладка не прогрелась (${e.message})`);
    } finally {
      _spareFilling = null;
    }
  })();
  _ensureKeepWarm();
  return _spareFilling;
}

function _ensureKeepWarm() {
  if (_keepWarmTimer) return;
  _keepWarmTimer = setInterval(() => {
    if (_spares.length < SPARES || _spares.some((s) => !_fresh(s))) _fillSpare();
  }, 60 * 1000);
  if (_keepWarmTimer.unref) _keepWarmTimer.unref();
}

// Вкладка под запрос: тёплая запасная, если есть, иначе новая. Второе значение —
// прогрета ли она (тогда путь с вложением может не ходить на главную ещё раз).
async function _takePage(ctx) {
  let page = null; let warm = false; let aiMode = false;
  while (_spares.length && !page) {
    const s = _spares.shift();
    if (_fresh(s)) { page = s.page; warm = true; aiMode = !!s.aiMode; }
    else if (!s.page.isClosed()) await s.page.close().catch(() => {});
  }
  if (!page) page = await ctx.newPage();
  setImmediate(() => _fillSpare());
  return { page, warm, aiMode };
}

/** Можно ли сейчас идти в Google (не упёрлись в лимит режима ИИ). */
function isAvailable() {
  return Date.now() >= _limitedUntil;
}

// Ждём готовность вложения опросом, а не сном (см. комментарий у uploadReadyMs) —
// общий для PDF- и картиночной ветки, отличается только сам readyCheck.
async function _waitAttachmentReady(page, readyCheck, arg) {
  const deadline = Date.now() + CONFIG.uploadReadyMs;
  while (Date.now() < deadline) {
    await page.waitForTimeout(250);
    const ready = await page.evaluate(readyCheck, arg).catch(() => false);
    if (ready) break;
  }
}

/**
 * Задаёт вопрос «Режиму ИИ» и возвращает текст ответа.
 * Сигнатура намеренно совпадает с ultragpt.generate — чтобы врезаться в
 * AiService тем же вызовом и без адаптеров.
 *
 * @param {string}   prompt        запрос пользователя
 * @param {string}   systemPrompt  системная часть (склеивается с запросом)
 * @param {Function} onPartial     колбэк для стриминга «на лету» (опц.)
 * @param {object}   opts          { timeoutMs, imageBase64 } — imageBase64: скриншот,
 *                                 прикреплённый пользователем в /chat (data:image/...;base64,...),
 *                                 см. AiService.pendingImages. До 11.09.2026 сюда не долетал
 *                                 вовсе: generate() принимал только текст, вложение читалось
 *                                 на сервере и терялось молча — картинка просто не доезжала.
 */
async function generate(prompt, systemPrompt = '', onPartial = null, opts = {}) {
  if (!isAvailable()) {
    throw new Error(`GoogleAI: лимит режима ИИ, пропускаю ещё ${Math.ceil((_limitedUntil - Date.now()) / 1000)}с`);
  }
  const timeoutMs = opts.timeoutMs || 120000;
  // opts.images — список data:image/...;base64 (до 4); opts.imageBase64 — одна, как раньше.
  const images = (Array.isArray(opts.images) ? opts.images : [opts.imageBase64]).filter(Boolean).slice(0, 4);
  const imageBase64 = images[0] || null;
  // GOOGLEAI_DEBUG=1 — засечки по шагам в лог (где уходит время конкретного запроса).
  const t0 = Date.now();
  const marks = [];
  const mark = (k) => { if (process.env.GOOGLEAI_DEBUG) marks.push(`${k} ${Date.now() - t0}`); };
  const deadline = Date.now() + timeoutMs;
  const text = [systemPrompt, prompt].filter(Boolean).join('\n\n').trim();
  // Три пути. Короткий — сразу по адресу с udm=50: это готовая страница-ответа,
  // самый быстрый и надёжный вариант (~2.5с), но БЕЗ вложений — там нет файловых
  // инпутов вовсе. Длинный — интерактивный режим ИИ: нужен, если текст не помещается
  // в URL и/или пользователь приложил картинку (её можно вложить только там же).
  const needsPdf = encodeURIComponent(text).length > CONFIG.urlBytesLimit;
  const viaFile = needsPdf || !!imageBase64;
  const browser = await _browserRef();
  const ctx = browser.contexts()[0];
  if (!ctx) throw new Error('GoogleAI: нет контекста браузера');

  // PDF с длинным контекстом печатаем ОДНОВРЕМЕННО с подготовкой режима ИИ (клик по
  // кнопке, ожидание поля вложения), а не до неё — это две независимые секунды.
  const pdfPromise = needsPdf ? _makePdf(ctx, text) : null;
  pdfPromise?.catch(() => {});
  let pdfPath = null;

  const { page, warm, aiMode } = await _takePage(ctx);
  mark(aiMode ? 'тёплая вкладка с режимом ИИ' : warm ? 'тёплая вкладка' : 'новая вкладка');
  let asked = '';          // текст, который ушёл в строку запроса — по нему ищем свою цепочку
  try {
    if (!viaFile) {
      asked = text;
      // Куки протухли и тёплой вкладки нет (долго не было запросов) — греем здесь.
      if (!warm && Date.now() - _homeWarmedAt > HOME_TTL_MS) await _warmHome(page);
      // 2а. Короткий путь: сразу на страницу режима ИИ с готовым запросом. Ждём только
      //     ответа сервера (commit), а не разбора всей страницы: ответ читаем опросом,
      //     он появляется в DOM раньше, чем страница «загрузится».
      const searchUrl = 'https://www.google.com/search?q=' + encodeURIComponent(text) + '&udm=50';
      await page.goto(searchUrl, { waitUntil: 'commit', timeout: 40000 });
      mark('страница ответа');
      // Капча здесь = куки протухли. Греем и повторяем ровно один раз.
      if (CONFIG.captchaRe.test(page.url())) {
        _homeWarmedAt = 0;
        await _warmHome(page);
        await page.goto(searchUrl, { waitUntil: 'commit', timeout: 40000 });
        if (CONFIG.captchaRe.test(page.url())) throw new Error('GoogleAI: капча на входе (серверный IP)');
      }
    } else {
      // 2б. Длинный путь и/или вложение: включаем интерактивный режим ИИ кнопкой
      //     и ждём, пока поднимется его поверхность. Признак готовности —
      //     появившийся в DOM файловый инпут: пока его нет, на странице только
      //     обычный поиск. Тёплая вкладка уже стоит на главной — второй раз не идём.
      const ready = aiMode && await page.evaluate(() => document.querySelectorAll('input[type="file"]').length > 0).catch(() => false);
      if (!ready) {
        if (!warm || !page.url().startsWith(HOME)) await _warmHome(page);
        mark('главная');
        await _openAiMode(page);
      }
      mark('режим ИИ открыт');

      if (pdfPromise) { pdfPath = await pdfPromise; mark('PDF готов'); }
      if (pdfPath) {
        await page.locator(CONFIG.fileInput).first().setInputFiles(pdfPath, { timeout: 20000 });
        mark('файл передан');
        // Ждём не «на всякий случай», а до появления чипа файла в интерфейсе —
        // обычно это ~0.5с. Не дождались за потолок — идём дальше: вложение чаще
        // всего уже прикрепилось, а лишний холостой сон здесь дороже риска.
        await _waitAttachmentReady(page, () =>
          /\.pdf/i.test(document.body.innerText) && !/загрузк|uploading/i.test(document.body.innerText));
        mark('PDF вложен');
      }

      if (images.length) {
        const files = images.map((img, i) => {
          const mimeMatch = img.match(/^data:(image\/[a-z+]+);base64,/i);
          const mimeType = mimeMatch ? mimeMatch[1] : 'image/png';
          const ext = mimeType === 'image/jpeg' ? 'jpg' : (mimeType.split('/')[1] || 'png');
          return { name: `screenshot-${i + 1}.${ext}`, mimeType, buffer: Buffer.from(img.replace(/^data:image\/[^;]+;base64,/i, ''), 'base64') };
        });
        await page.locator(CONFIG.imageFileInput).first().setInputFiles(files, { timeout: 20000 });
        // Готовность — появившееся превью картинки (blob:/data: изображение крупнее значка).
        // Прежняя проверка «на странице нет слова „загрузка“» проходила мгновенно: индикатора
        // загрузки у Google нет вовсе, и Enter уходил раньше картинки — Google его молча
        // игнорировал, ответ «не начинался за 25с», задача падала на Groq, который картинок
        // не видит (живой случай 14.10.2026).
        const want = files.length;
        await _waitAttachmentReady(page, (n) => [...document.querySelectorAll('img')]
          .filter((i) => /^(blob:|data:)/.test(i.src) && i.offsetParent && i.naturalWidth > 48 && i.naturalHeight > 48).length >= n, want);
        mark('картинка вложена');
      }

      // Что печатаем в строку: если текст ушёл вложением-PDF, инструкция фиксированная
      // (сам текст модель прочитает из файла); картинку в этом случае тоже упоминаем
      // явно — иначе модель отвечает только по PDF и не смотрит на приложенный скрин.
      // Если PDF не нужен (короткий текст + просто картинка) — печатаем сам текст.
      const say = pdfPath
        ? `В приложенном PDF — задание и весь контекст.${images.length ? ` Также ${images.length > 1 ? `приложены изображения (${images.length})` : 'приложено изображение'} — обязательно посмотри и на ${images.length > 1 ? 'них' : 'него'}.` : ''} Выполни задание и ответь текстом, без вступлений.`
        : text;
      asked = say;
      // fill() сам ставит фокус — отдельный click с ожиданием «кликабельности» стоил
      // около секунды на каждом таком запросе.
      const input = page.locator(CONFIG.input).first();
      try { await input.fill(say, { timeout: 15000 }); }
      catch { await input.click({ timeout: 15000 }); await page.keyboard.type(say, { delay: 8 }); }
      mark('текст введён');
      // Enter прямо с клавиатуры: фокус после fill() уже в поле. input.press() сначала
      // ждёт, пока поле «успокоится» после появления вложения, — это ещё 1.3-1.6с.
      // Отправку проверяем: пока вложение ещё уходит на сервер Google, Enter молча
      // игнорируется. Ушёл запрос — страница переходит на /search?q=… (или уже пишет
      // ответ). Нет — жмём ещё раз, до 4 попыток.
      const before = page.url();
      for (let attempt = 1; attempt <= 4; attempt += 1) {
        await page.waitForTimeout(attempt === 1 ? 100 : 0);
        await input.focus().catch(() => {});
        await page.keyboard.press('Enter');
        const sent = await page.waitForFunction(([was, sel]) => location.href !== was
          || !!(document.querySelector(sel)?.innerText || '').trim(), [before, CONFIG.answer], { timeout: 2500 })
          .then(() => true).catch(() => false);
        if (sent) break;
        mark(`Enter не принят (${attempt})`);
      }
      mark('отправлено');
    }

    // 4. Ждём ответ. Конец ответа — кнопки оценки/копирования под ним (появляются, когда
    //    Google дописал) при тексте, не изменившемся с прошлого опроса. Нет кнопок —
    //    запасной признак: текст неподвижен stableMs (короткий — вдвое дольше).
    let last = '';
    let stableSince = 0;
    let appeared = 0;
    const startedAt = Date.now();
    while (Date.now() < deadline) {
      await page.waitForTimeout(CONFIG.pollMs);
      if (CONFIG.captchaRe.test(page.url())) throw new Error('GoogleAI: капча во время ответа');

      const snap = await page.evaluate(([sel, doneRe]) => {
        const el = document.querySelector(sel);
        if (!el) return { t: '', done: false };
        const re = new RegExp(doneRe, 'i');
        const done = [...el.querySelectorAll('button,[role="button"]')]
          .some((b) => re.test(b.getAttribute('aria-label') || ''));
        return { t: el.innerText, done };
      }, [CONFIG.answer, CONFIG.doneLabelRe]).catch(() => ({ t: '', done: false }));
      const now = clean(snap.t);

      if (CONFIG.limitRe.test(now) && now.length < 300) {
        _limitedUntil = Date.now() + CONFIG.limitCooldownMs;
        throw new Error('GoogleAI: лимит режима ИИ на аккаунте');
      }

      if (now.length > 0) {
        if (!appeared) { appeared = Date.now(); mark('первые буквы'); }
        if (onPartial && now !== last && now.length >= CONFIG.minAnswer) {
          try { await onPartial(now); } catch { /* показ не критичен */ }
        }
        if (now === last) {
          if (snap.done) { mark('готово (кнопки)'); return now; }
          if (!stableSince) stableSince = Date.now();
          const need = now.length >= CONFIG.minAnswer ? CONFIG.stableMs : CONFIG.stableMs * 2;
          if (Date.now() - stableSince >= need) return now;
        } else {
          stableSince = 0;
        }
        last = now;
        if (Date.now() - appeared > CONFIG.appearTimeoutMs) break;
      } else if (!appeared && Date.now() - startedAt > CONFIG.firstTokenMs) {
        // Ответ не начался — не занимаем очередь, отдаём ход следующему тиру.
        throw new Error(`GoogleAI: ответ не начался за ${Math.round(CONFIG.firstTokenMs / 1000)}с`);
      }
    }
    if (last.length >= CONFIG.minAnswer) return last;   // время вышло — отдаём что есть
    throw new Error('GoogleAI: пустой ответ');
  } finally {
    if (marks.length) console.log(`⏱ GoogleAI: ${marks.join(' · ')} мс`);
    // Уборку НЕ ждём. Она стоит ~6с сплошных пауз (_deleteThread) и раньше
    // целиком лежала на пути пользователя: ответ уже готов и виден в DOM, а
    // задача всё ещё «в работе», пока мы возим мышкой по боковой панели. Теперь
    // отпускаем результат сразу, а цепочку и вкладку добираем в фоне — и при
    // удаче, и при сбое, иначе неудачные попытки копятся так же, как удачные.
    (async () => {
      await _deleteThread(page, asked).catch(() => {});
      // Вкладку всегда закрываем: иначе за сутки их накопятся сотни.
      await page.close().catch(() => {});
      const made = pdfPath || (pdfPromise ? await pdfPromise.catch(() => null) : null);
      if (made) fs.promises.unlink(made).catch(() => {});
    })();
  }
}

// Быстрая проверка живости: открывается ли главная и виден ли режим ИИ.
async function healthcheck() {
  const browser = await _browserRef();
  const page = await browser.contexts()[0].newPage();
  try {
    await page.goto(HOME, { waitUntil: 'domcontentloaded', timeout: 30000 });
    if (CONFIG.captchaRe.test(page.url())) return { ok: false, error: 'капча на серверном IP' };
    const visible = await page.locator(CONFIG.aiModeButton).first().isVisible({ timeout: 8000 }).catch(() => false);
    return visible ? { ok: true } : { ok: false, error: 'кнопка «Режим ИИ» не найдена' };
  } finally {
    await page.close().catch(() => {});
  }
}

module.exports = { generate, healthcheck, isAvailable, warmUp: _fillSpare, CONFIG };
