import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
// os.css 定义三形态（data-mode）与主题（data-theme）的全部样式；必须晚于
// index.css 引入，否则 :root 上的默认令牌会被基础样式覆盖。
import './os.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// PWA：只在生产构建里注册 Service Worker。
//
// 开发时不注册 —— 否则 dev server 的模块会被缓存住，出现"代码改了页面不变"这类
// 极难排查的问题，得到的便利远不及代价。
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch((error) => {
      // 注册失败不影响应用本身；把真实原因留在控制台，不静默吞掉。
      console.warn('Service Worker 注册失败：', error)
    })
  })
}
