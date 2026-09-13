/* 鎏灏 AI OS 驾驶舱 —— Service Worker
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
 * 版本号变更即触发旧缓存清理（见 activate）。
 */

const VERSION = 'v1';
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

/** 鉴权数据与接口文档：永不缓存、永不拦截。 */
function isLiveApi(pathname) {
  return (
    pathname.startsWith('/v1/') ||
    pathname === '/openapi.json' ||
    pathname.startsWith('/docs') ||
    pathname.startsWith('/redoc')
  );
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL_CACHE);
      // 逐个添加：某个图标 404 不应该让整个安装失败。
      await Promise.all(
        SHELL_URLS.map(async (url) => {
          try {
            await cache.add(new Request(url, { cache: 'reload' }));
          } catch {
            /* 单个资源缺失不阻塞安装 */
          }
        }),
      );
      await self.skipWaiting();
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
          const cached = (await caches.match('/')) || (await caches.match('/index.html'));
          if (cached) return cached;
          return new Response('离线，且没有可用的缓存外壳。', {
            status: 503,
            headers: { 'Content-Type': 'text/plain; charset=utf-8' },
          });
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
