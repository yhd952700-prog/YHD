import { useEffect, useState } from 'react'
import './App.css'
import { Sidebar, TopBar } from './components/chrome'
import { ChatPanel } from './components/ChatPanel'
import {
  Activity,
  BizReality,
  KeyMetrics,
  MarketFocus,
  OurGoal,
  Pipeline,
  SystemHealth,
  TaskCenter,
  TodayBrief,
} from './components/panels'

function App() {
  // 真实遥测（/v1/ready + /v1/chat/sessions）—— NO-FAKE：系统类数据只接真实接口
  const [gwReady, setGwReady] = useState<boolean | null>(null)
  const [gwUptime, setGwUptime] = useState<number>(-1)
  const [liveSessions, setLiveSessions] = useState(0)
  const [activeNav, setActiveNav] = useState('dashboard')

  useEffect(() => {
    let alive = true
    async function poll() {
      try {
        const r = await fetch('/v1/ready', { cache: 'no-store' })
        const d = (await r.json()) as { status?: string; timestamp?: number }
        if (alive) {
          setGwReady(d.status === 'ready')
          if (d.timestamp) setGwUptime((Date.now() / 1000 - d.timestamp) | 0)
        }
      } catch {
        if (alive) setGwReady(false)
      }
      try {
        const s = await fetch('/v1/chat/sessions', { cache: 'no-store' })
        const d = (await s.json()) as { count?: number }
        if (alive) setLiveSessions(d.count ?? 0)
      } catch {
        // 保持上次值
      }
    }
    poll()
    const t = setInterval(poll, 15000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

  return (
    <div className="app">
      <TopBar />
      <div className="shell">
        <Sidebar active={activeNav} onSelect={setActiveNav} />
        <main className="main">
          <div className="layout">
            {/* 左列：今日简报 + 市场 */}
            <div className="col col-left">
              <TodayBrief />
              <MarketFocus />
            </div>

            {/* 中列：JARVIS + Pipeline + 业务数据 + 任务 */}
            <div className="col col-center">
              <ChatPanel />
              <Pipeline />
              <BizReality />
              <TaskCenter />
            </div>

            {/* 右列：核心指标 + 系统状态 + 动态 + 目标 */}
            <div className="col col-right">
              <KeyMetrics />
              <SystemHealth ready={gwReady} liveSessions={liveSessions} uptime={gwUptime} />
              <Activity />
              <OurGoal />
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}

export default App
