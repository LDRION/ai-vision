'use strict';
/*
  Edge для Siberi Local: свой профиль (куки Google живут в нём), порт CDP, без окон.

  Без окон — важно: AI Vision управляет экраном, и всплывающий браузер «мозга» мешал бы
  ему (перехватывал фокус, попадал в список окон). Поэтому headless. Чтобы Google не
  отдавал «урезанную» страницу headless-браузеру, представляемся обычным Edge этой версии.

  Edge — ребёнок этого процесса: AI Vision держит Siberi Local в Job Object «убить при
  закрытии», и Edge гаснет вместе с ним, без висящих фоновых браузеров.

  login: true — окно с профилем, чтобы один раз войти в Google (или пройти проверку);
  вошли — окно закрывается само (waitForLogin).
*/
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const PORT = Number(process.env.SIBERI_CDP_PORT || 9224);
const PROFILE = process.env.SIBERI_PROFILE || path.join(__dirname, 'edge-profile');
const EDGE_DIRS = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application',
  'C:\\Program Files\\Microsoft\\Edge\\Application',
];

function findEdge() {
  for (const dir of EDGE_DIRS) {
    const exe = path.join(dir, 'msedge.exe');
    if (fs.existsSync(exe)) {
      const ver = fs.readdirSync(dir).filter((d) => /^\d+\.\d+\.\d+\.\d+$/.test(d)).sort().pop() || '150.0.0.0';
      return { exe, ver };
    }
  }
  return null;
}

async function cdpAlive() {
  try {
    const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
    return r.ok;
  } catch {
    return false;
  }
}

let child = null;

async function launchBrowser({ login = false } = {}) {
  if (!login && await cdpAlive()) return true;
  const edge = findEdge();
  if (!edge) throw new Error('Microsoft Edge не найден — он нужен Siberi Local');
  fs.mkdirSync(PROFILE, { recursive: true });
  const major = edge.ver.split('.')[0];
  const args = [
    `--user-data-dir=${PROFILE}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-blink-features=AutomationControlled',
    '--disable-features=IsolateOrigins,site-per-process,PlzServiceWorker,BackForwardCache',
    '--lang=ru-RU',
  ];
  if (login) {
    args.push(`--remote-debugging-port=${PORT}`,
      'https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fwww.google.com%2F');
  } else {
    args.push(`--remote-debugging-port=${PORT}`, '--headless=new', '--window-size=1366,900',
      `--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) `
      + `Chrome/${major}.0.0.0 Safari/537.36 Edg/${edge.ver}`, 'about:blank');
  }
  child = spawn(edge.exe, args, { stdio: 'ignore', windowsHide: !login });
  if (login) return waitForLogin();
  for (let i = 0; i < 60; i += 1) {
    if (await cdpAlive()) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error('Edge не поднял CDP за 30 с');
}

const SIGNED_GRACE_MS = 4000;    // вошёл — даём странице догрузиться и куки записаться, потом закрываем

/*
  Окно входа закрываем сами: человек вошёл в аккаунт (появилась кука SID) — через пару секунд
  закрываем браузер штатно (Browser.close — Edge сохраняет куки на диск). Ждать, пока окно
  закроют руками, нельзя: это легко пропустить (живой случай 26.10.2026), а Edge и после
  закрытия последнего окна может остаться жить в фоне. Закрыли окно сами — тоже конец.
*/
async function waitForLogin() {
  for (let i = 0; i < 60 && !(await cdpAlive()); i += 1) await new Promise((r) => setTimeout(r, 500));
  const { chromium } = require('playwright-core');
  let b = null;
  let signedAt = 0;
  try {
    b = await chromium.connectOverCDP(`http://127.0.0.1:${PORT}`, { timeout: 30000 });
    const ctx = b.contexts()[0];
    while (child.exitCode === null && b.isConnected()) {
      await new Promise((r) => setTimeout(r, 1000));
      const cookies = await ctx.cookies('https://www.google.com').catch(() => []);
      if (!signedAt && cookies.some((c) => c.name === 'SID' || c.name === '__Secure-1PSID')) {
        signedAt = Date.now();
        console.log('✅ вход в Google есть — закрываю окно');
      }
      if (signedAt && Date.now() - signedAt > SIGNED_GRACE_MS) break;
      if (!ctx.pages().length) break;                  // окно закрыли, а Edge остался в фоне
    }
    if (b.isConnected()) {
      const s = await b.newBrowserCDPSession();
      await s.send('Browser.close').catch(() => {});
    }
  } catch (e) {
    console.log(`⚠️ окно входа: ${e.message}`);
  }
  for (let i = 0; i < 20 && child.exitCode === null; i += 1) await new Promise((r) => setTimeout(r, 500));
  if (child.exitCode === null) child.kill();
  return !!signedAt;
}

function stopBrowser() {
  if (child && child.exitCode === null) child.kill();
}

module.exports = { launchBrowser, stopBrowser, cdpAlive, PORT, PROFILE };
