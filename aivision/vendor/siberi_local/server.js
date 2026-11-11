'use strict';
/*
  Siberi Local — «мозг» Siberi на этом компьютере, без хостинга.

  Тот же драйвер Google «Режима ИИ», что в сервере Siberi (googleai.js), но без MongoDB,
  платежей, ботов и кабинета: только то, чем пользуется AI Vision. API совместим с
  API сервера Siberi (/api/v1/ai) — клиенту достаточно сменить адрес:
    POST /api/v1/ai/generate  {prompt, system?, image? | images?, wait?:false} → {taskId}
    GET  /api/v1/ai/result/:taskId → {status: pending|completed|error, result, partialResult, error}
    GET  /api/v1/ai/ping → {ok, browser, google}
  Слушает только 127.0.0.1. Ключ — SIBERI_LOCAL_KEY (тот же SIBERI_API_KEY, что у клиента).

    node server.js            — сервер (AI Vision запускает его сам)
    node server.js --login    — окно Edge с профилем: один раз войти в Google (закроется само)
*/
const http = require('http');
const crypto = require('crypto');
const browser = require('./browser');

const PORT = Number(process.env.SIBERI_LOCAL_PORT || 8770);
const KEY = process.env.SIBERI_LOCAL_KEY || '';
const PARALLEL = Math.max(1, Number(process.env.SIBERI_LOCAL_PARALLEL || 2));   // вкладок одновременно
const KEEP_MS = 10 * 60 * 1000;                                                   // готовые задачи храним столько
const MAX_BODY = 15 * 1024 * 1024;
process.env.GOOGLEAI_CDP = process.env.GOOGLEAI_CDP || `http://127.0.0.1:${browser.PORT}`;
// SIBERI_LOCAL_ENGINE=echo — для тестов AI Vision: без Edge и Google, ответ — эхо запроса.
const ECHO = process.env.SIBERI_LOCAL_ENGINE === 'echo';
const googleai = ECHO ? {
  async generate(prompt, system, onPartial, opts) {
    if (/^REFUSE/.test(prompt)) return 'Не удается дать ответ на этот поисковый запрос. Попробуйте спросить что-нибудь другое.';
    if (onPartial) await onPartial('эхо…');
    return `echo[${(opts.images || []).length}]: ${system ? `${system} | ` : ''}${prompt}`;
  },
  isAvailable: () => true,
  warmUp: () => {},
} : require('./googleai');

// Без входа в аккаунт «Режим ИИ» на любую картинку отвечает этой фразой (проверено 26.10.2026),
// а текстовые вопросы — работают. Отличаем, чтобы сказать человеку, что делать.
const REFUSAL = /Не удается дать ответ на этот поисковый запрос|can't help with that search/i;
const LOGIN_NEEDED = 'google_login_required: Google отвечает на вопросы с картинками только после входа в аккаунт — '
  + 'войдите один раз: node server.js --login (в AI Vision — «Войти в Google»)';

let cdp = null;
async function signedIn() {
  if (ECHO) return process.env.SIBERI_LOCAL_SIGNED === '1';   // тесты: без настоящего браузера
  try {
    if (!cdp || !cdp.isConnected()) {
      const { chromium } = require('playwright-core');
      cdp = await chromium.connectOverCDP(process.env.GOOGLEAI_CDP, { timeout: 5000 });
    }
    const cookies = await cdp.contexts()[0].cookies('https://www.google.com');
    return cookies.some((c) => c.name === 'SID' || c.name === '__Secure-1PSID');
  } catch {
    return false;
  }
}

const tasks = new Map();         // taskId → {status, result, partialResult, error, done}
const queue = [];
let running = 0;

function authorized(req) {
  if (!KEY) return true;
  const hdr = req.headers.authorization || '';
  const got = Buffer.from(hdr.startsWith('Bearer ') ? hdr.slice(7).trim() : String(req.headers['x-api-key'] || ''));
  const want = Buffer.from(KEY);
  return got.length === want.length && crypto.timingSafeEqual(got, want);
}

function send(res, code, body) {
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(body));
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > MAX_BODY) { reject(new Error('запрос больше 15 МБ')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => {
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')); } catch (e) { reject(e); }
    });
    req.on('error', reject);
  });
}

function pump() {
  while (running < PARALLEL && queue.length) {
    const job = queue.shift();
    running += 1;
    run(job).finally(() => { running -= 1; pump(); });
  }
}

async function run({ id, prompt, system, images, timeoutMs }) {
  const t = tasks.get(id);
  const t0 = Date.now();
  try {
    if (!ECHO && !(await browser.cdpAlive())) await browser.launchBrowser();
    const result = await googleai.generate(prompt, system, (partial) => { t.partialResult = partial; },
      { images, timeoutMs });
    if (images.length && REFUSAL.test(result) && result.length < 200 && !(await signedIn())) {
      throw new Error(LOGIN_NEEDED);                 // без входа Google картинки не принимает
    }
    Object.assign(t, { status: 'completed', result, done: Date.now() });
    console.log(`✅ ${id.slice(0, 8)} · ${((Date.now() - t0) / 1000).toFixed(1)} с · картинок ${images.length} · ${result.length} симв.`);
  } catch (e) {
    Object.assign(t, { status: 'error', error: e.message, done: Date.now() });
    console.log(`❌ ${id.slice(0, 8)} · ${e.message}`);
  }
}

function images(body) {
  const list = Array.isArray(body.images) ? body.images : [body.image ?? body.imageBase64];
  return list.filter((s) => typeof s === 'string' && /^data:image\/[a-z+]+;base64,/i.test(s)).slice(0, 4);
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const p = url.pathname.replace(/\/+$/, '');
  if (!p.startsWith('/api/v1/ai')) return send(res, 404, { error: 'not found' });
  if (!authorized(req)) return send(res, 401, { error: 'Unauthorized: invalid or missing API key' });
  try {
    if (req.method === 'GET' && p === '/api/v1/ai/ping') {
      const alive = !ECHO && await browser.cdpAlive();
      return send(res, 200, { success: true, ok: true, service: 'Siberi Local', browser: alive,
        google: googleai.isAvailable(), signedIn: alive ? await signedIn() : false, queue: queue.length, running });
    }
    if (req.method === 'POST' && p === '/api/v1/ai/generate') {
      const body = await readJson(req);
      const prompt = String(body.prompt ?? body.message ?? '').trim();
      if (!prompt) return send(res, 400, { error: 'prompt (or message) is required' });
      const id = crypto.randomUUID();
      tasks.set(id, { status: 'pending', result: null, partialResult: null, error: null, done: 0 });
      queue.push({ id, prompt, system: String(body.system || ''), images: images(body),
        timeoutMs: Math.min(Math.max(Number(body.timeoutMs) || 120000, 5000), 300000) });
      pump();
      if (body.wait === false) return send(res, 202, { success: true, taskId: id });
      while (tasks.get(id).status === 'pending') await new Promise((r) => setTimeout(r, 200));
      const t = tasks.get(id);
      return send(res, t.status === 'completed' ? 200 : 502,
        { success: t.status === 'completed', taskId: id, result: t.result, error: t.error });
    }
    const m = p.match(/^\/api\/v1\/ai\/result\/([\w-]+)$/);
    if (req.method === 'GET' && m) {
      const t = tasks.get(m[1]);
      if (!t) return send(res, 404, { error: 'Task not found' });
      return send(res, 200, { success: true, status: t.status, result: t.result, partialResult: t.partialResult, error: t.error });
    }
    return send(res, 404, { error: 'not found' });
  } catch (e) {
    return send(res, 500, { error: e.message });
  }
});

setInterval(() => {                                   // забытые результаты не копим
  const old = Date.now() - KEEP_MS;
  for (const [id, t] of tasks) if (t.done && t.done < old) tasks.delete(id);
}, 60 * 1000).unref();

async function main() {
  if (process.argv.includes('--login')) {
    console.log('Окно Edge с профилем Siberi Local: войдите в Google (или пройдите проверку) — окно закроется само.');
    await browser.launchBrowser({ login: true });
    return;
  }
  if (!ECHO) await browser.launchBrowser();
  server.listen(PORT, '127.0.0.1', () => console.log(`🧠 Siberi Local: http://127.0.0.1:${PORT}/api/v1/ai · CDP ${browser.PORT}`));
  googleai.warmUp();                                  // тёплые вкладки на google.com — первый ответ быстрее
}

process.on('SIGINT', () => { browser.stopBrowser(); process.exit(0); });
process.on('SIGTERM', () => { browser.stopBrowser(); process.exit(0); });
main().catch((e) => { console.error(`❌ ${e.message}`); browser.stopBrowser(); process.exit(1); });
