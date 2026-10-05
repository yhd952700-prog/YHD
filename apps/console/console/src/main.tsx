import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
// os.css 定义三形态（data-mode）与主题（data-theme）的全部样式；必须晚于
// index.css 引入，否则 :root 上的默认令牌会被基础样式覆盖。
import './os.css'
import App from './App.tsx'
import { ErrorBoundary } from './components/ErrorBoundary'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/*
      最后一道边界：罩住整棵树。

      在此之前，任何渲染异常都会让 React **卸载整棵树**，`#root` 里什么都不剩 ——
      屏幕全白，而白屏里没有任何一句话能告诉人"出了什么事"。对一个把"人类主权"
      写在首要位置的系统来说，最需要观测的时刻反而没有界面可看，这是最坏的失败形态。

      `App` 内部另有分区级边界（见 components/ui.tsx 的 Card），所以这里兜住的
      只会是外壳级故障；它的职责是"绝不让整个界面消失成一块空白"。
    */}
    <ErrorBoundary label="控制台整体">
      <App />
    </ErrorBoundary>
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
