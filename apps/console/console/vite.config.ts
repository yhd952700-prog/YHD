import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import type { Plugin } from 'vite'

/**
 * 把 Service Worker 的版本号钉到**本次构建的入口哈希**上。
 *
 * 为什么必须随构建变：SW 里写死 `VERSION = 'v1'` 时，缓存永不失效。后端被关掉
 * 之后，浏览器仍然会把旧外壳原样回放，页面看起来正常、只有数据卡片各自报错，
 * 用户无法区分"服务挂了"和"数据有问题"。
 *
 * 版本值取自 `assets/index-<hash>.js` 的 `<hash>`：它与真实产物同源，因此
 * "换了产物"必然"换了缓存名"，activate 会清掉旧缓存。
 *
 * 实现放在 `generateBundle`：此时入口 chunk 的最终文件名（含哈希）已经确定，
 * 直接产出 `dist/sw.js`。模板放在 `sw.template.js`（不在 `public/` 里），
 * 避免 Vite 把带占位符的版本原样拷进产物。
 */
function serviceWorkerVersion(): Plugin {
  const templatePath = fileURLToPath(new URL('./sw.template.js', import.meta.url))
  return {
    name: 'liuhao-sw-version',
    apply: 'build',
    generateBundle(_options, bundle) {
      const items = Object.values(bundle)
      const entry = items.find((item) => item.type === 'chunk' && item.isEntry)
      const fileName = entry && entry.type === 'chunk' ? entry.fileName : ''
      const version = /index-([A-Za-z0-9_-]+)\.js$/.exec(fileName)?.[1] ?? 'dev'
      // 预缓存清单必须来自**本次**产物：回退页面没有 JS/CSS 就是白屏，
      // 而运行时无法得知带哈希的文件名。
      const precache = items
        .filter(
          (item) =>
            (item.type === 'chunk' && item.isEntry) ||
            (item.type === 'asset' && item.fileName.endsWith('.css')),
        )
        .map((item) => `/${item.fileName}`)
      const source = readFileSync(templatePath, 'utf8')
        .replaceAll('__BUILD_VERSION__', version)
        .replaceAll('__PRECACHE_ASSETS__', JSON.stringify(precache))
      this.emitFile({ type: 'asset', fileName: 'sw.js', source })
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), serviceWorkerVersion()],
  server: {
    port: 5173,
    proxy: {
      // 把对话 API 代理到 FastAPI 网关，前端无需关心跨域。
      '/v1': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
      },
    },
  },
})
