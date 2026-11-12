/* Jarvis · 贾维斯 驾驶舱 —— Service Worker
 *
 * 职责边界（刻意收得很窄）
 * ----------------------
 * 1. **让应用可安装**。没有 SW 就没有 `beforeinstallprompt`，"桌面端装成独立窗口 /
 *    手机端加到主屏"就只能靠用户手输地址栏，那不是三端交付。
 * 2. **让外壳在弱网下能开出来**。导航请求走 network-first，失败时回落到上次缓存的
 *    外壳 —— 但绝不冒充数据。
 *
 * 三条硬约束：
 * - `/v1/*`、`/openapi.json`、`/docs`、`/redoc` **一律直连网络，永不缓存**。这些都是
 *   带鉴权的实时数据；缓存它们等于让"审批态势"或"依赖健康度"停留在几分钟前，
 *   在安全控制台上这是比不可用更糟的错误。
 * - 导航请求 network-first（不是 cache-first）：重新部署后不能出现"用户拿到旧外壳、
 *   JS 哈希对不上"的白屏。
 * - 只有 2xx 同源响应才会写缓存。
 *
 * 版本号 = 构建产物哈希（见 vite.config.ts 的 sw 版本注入）
 * ---------------------------------------------------------
 * 这里曾经写死 `'v1'`，代价是**永不失效**：后端进程被杀掉之后，08:22 的旧外壳被
 * 原样回放，08:24 页面看起来一切正常，只有卡片各自报"数据不可用"。用户无法从
 * 界面上判断"服务挂了"和"数据有问题"。
 *
 * 现在 VERSION 由构建注入（与 `assets/index-<hash>.js` 同源），每次发布都会换
 * cache 名，activate 里按"不是当前版本"清理旧缓存；旧标签页刷新即拿到新外壳。
 *
 * 回退页面必须自曝其短
 * --------------------
 * 导航失败回退缓存外壳时，注入顶部「本地服务未运行」横幅（含重新加载入口），
 * 不能让用户把"缓存回放"当成"服务正常"。
 */

const VERSION = '__BUILD_VERSION__';
const SHELL_CACHE = `liuhao-shell-${VERSION}`;
const ASSET_CACHE = `liuhao-asset-${VERSION}`;

const SHELL_URLS = [
  '/',
  '/manifest.webmanifest',
  '/favicon.svg',
  '/icon-192.png',
  '/icon-512.png',
  '/icon-512-maskable.png',
  '/apple-touch-icon.png',
];

/**
 * 本次构建的 JS/CSS 产物（构建时注入）。
 *
 * 没有这一步，回退页面就是"有壳没肉"：导航能回放缓存 HTML，但
 * `/assets/index-<hash>.js` 不在缓存里，页面只剩一块空白 —— 比报错更糟。
 * 不能在运行时扫描，因为需要知道带哈希的文件名；由 vite.config.ts 把本次
 * bundle 的文件名直接写进来。
 */
const PRECACHE_ASSETS = __PRECACHE_ASSETS__;

/** 鉴权数据与接口文档：永不缓存、永不拦截。 */
function isLiveApi(pathname) {
  return (
    pathname.startsWith('/v1/') ||
    pathname === '/openapi.json' ||
    pathname.startsWith('/docs') ||
    pathname.startsWith('/redoc')
  );
}

/** 回退横幅：纯内联样式，不依赖任何外部资源（此时网络本来就不可用）。 */
const OFFLINE_BANNER_ID = 'liuhao-offline-banner';
const OFFLINE_BANNER = `
<div id="${OFFLINE_BANNER_ID}" role="alert" style="position:fixed;left:0;right:0;top:0;z-index:2147483647;display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center;justify-content:center;padding:12px 18px;background:#3b2020;color:#ffe9e6;border-bottom:1px solid #8a4444;box-shadow:0 2px 12px rgba(0,0,0,.45);font:14px/1.5 system-ui,-apple-system,'Segoe UI','Microsoft YaHei',sans-serif">
  <strong style="font-weight:600;color:#ffb4a8">本地服务未运行</strong>
  <span>贾维斯的本地服务已断开，这一页是上次缓存的内容，数据不会更新。</span>
  <a href="/" style="display:inline-block;padding:4px 12px;border-radius:999px;background:#ffb4a8;color:#3b2020;font-weight:600;text-decoration:none">重新加载</a>
  <span style="opacity:.85">请打开贾维斯后再试。</span>
</div>`;

/** 把横幅塞进缓存的 HTML；重复回退不会叠加多个横幅。 */
function withOfflineBanner(html) {
  if (html.includes(OFFLINE_BANNER_ID)) return html;
  if (/<\/body>/i.test(html)) return html.replace(/<\/body>/i, `${OFFLINE_BANNER}</body>`);
  return html + OFFLINE_BANNER;
}

async function offlineShell() {
  const cached = (await caches.match('/')) || (await caches.match('/index.html'));
  if (!cached) {
    return new Response('本地服务未运行，且没有可用的缓存页面。请打开贾维斯后再试。', {
      status: 503,
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    });
  }
  const html = await cached.text();
  return new Response(withOfflineBanner(html), {
    status: 200,
    headers: { 'Content-Type': 'text/html; charset=utf-8' },
  });
}

self.addEventListener('install', (event) => {
  // 顶层的 `skipWaiting()` 只在"确实有旧版本在跑"时才有意义，所以放在 install
  // 开头先发一次：等待中的旧外壳没有理由继续留着，否则用户刷新拿到的仍是旧缓存
  // 对应的旧版本，直到关掉所有标签页为止。
  //
  // 这里**必须**是 fire-and-forget：`skipWaiting()` 的 promise 要等到接管完成后
  // 才 resolve，把它 await 进 `waitUntil` 会让 install 永远等不到结束，新版本就
  // 一直停在 waiting —— 表面"已安装"，实际永远不生效。
  void self.skipWaiting();
  event.waitUntil(
    (async () => {
      const shell = await caches.open(SHELL_CACHE);
      // 逐个添加：某个图标 404 不应该让整个安装失败。
      await Promise.all(
        SHELL_URLS.map(async (url) => {
          try {
            await shell.add(new Request(url, { cache: 'reload' }));
          } catch {
            /* 单个资源缺失不阻塞安装 */
          }
        }),
      );
      // 本次构建的 JS/CSS 必须预缓存：回退页面没有产物就是白屏。
      const assets = await caches.open(ASSET_CACHE);
      await Promise.all(
        PRECACHE_ASSETS.map(async (url) => {
          try {
            await assets.add(new Request(url, { cache: 'reload' }));
          } catch {
            /* 同上 */
          }
        }),
      );
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((key) => key !== SHELL_CACHE && key !== ASSET_CACHE)
          .map((key) => caches.delete(key)),
      );
      await self.clients.claim();
    })(),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }
  if (url.origin !== self.location.origin) return;
  if (isLiveApi(url.pathname)) return; // 交给浏览器直连

  // --- 导航：network-first ---
  if (request.mode === 'navigate') {
    event.respondWith(
      (async () => {
        try {
          const fresh = await fetch(request);
          if (fresh && fresh.ok) {
            const cache = await caches.open(SHELL_CACHE);
            cache.put('/', fresh.clone()).catch(() => {});
          }
          return fresh;
        } catch {
          return offlineShell();
        }
      })(),
    );
    return;
  }

  // --- 静态资源：stale-while-revalidate（文件名带内容哈希，回退安全） ---
  event.respondWith(
    (async () => {
      const cache = await caches.open(ASSET_CACHE);
      const cached = await cache.match(request);
      const revalidate = fetch(request)
        .then((response) => {
          if (response && response.ok && response.type === 'basic') {
            cache.put(request, response.clone()).catch(() => {});
          }
          return response;
        })
        .catch(() => null);

      if (cached) {
        event.waitUntil(revalidate);
        return cached;
      }
      const fresh = await revalidate;
      if (fresh) return fresh;
      return new Response('', { status: 504, statusText: 'Gateway Timeout' });
    })(),
  );
});
