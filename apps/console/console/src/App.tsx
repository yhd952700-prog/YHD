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
  UserProfileCard,
} from './components/panels'

/** 真实遥测（后端 /v1/dashboard/* 聚合的真实运行数据）。 */
export interface Telemetry {
  ready: boolean
  uptime: number
  providerType: string
  providerModel: string
  sessions: number
  sessionDetail: { id: string; turns: number }[]
  auditTotal: number
  activity: { type: string; outcome: string; principal: string; timestamp: number }[]
}

function App() {
  // 真实遥测（/v1/dashboard/summary + /v1/dashboard/activity）—— NO-FAKE：只接真实接口
  const [gwReady, setGwReady] = useState<boolean | null>(null)
  const [gwUptime, setGwUptime] = useState<number>(-1)
  const [liveSessions, setLiveSessions] = useState(0)
  const [provider, setProvider] = useState<{ type: string; model: string }>({ type: '—', model: '—' })
  const [auditTotal, setAuditTotal] = useState(0)
  const [activity, setActivity] = useState<Telemetry['activity']>([])
  const [sessionDetail, setSessionDetail] = useState<Telemetry['sessionDetail']>([])
  const [activeNav, setActiveNav] = useState('dashboard')

  useEffect(() => {
    let alive = true
    async function poll() {
      try {
        const r = await fetch('/v1/ready', { cache: 'no-store' })
        const d = (await r.json()) as { status?: string }
        if (alive) setGwReady(d.status === 'ready')
      } catch {
        if (alive) setGwReady(false)
      }
      // 驾驶舱遥测（真实：provider / 会话 / 审计 / 动态）
      try {
        const s = await fetch('/v1/dashboard/summary', { cache: 'no-store' })
        const d = (await s.json()) as {
          uptime_seconds?: number
          provider?: { type?: string; model?: string }
          sessions?: { count?: number; detail?: { id: string; turns: number }[] }
          audit?: { total_events?: number }
        }
        if (alive) {
          if (typeof d.uptime_seconds === 'number') setGwUptime(d.uptime_seconds)
          if (d.provider) {
            setProvider({ type: d.provider.type ?? '—', model: d.provider.model ?? '—' })
          }
          setLiveSessions(d.sessions?.count ?? 0)
          setSessionDetail(d.sessions?.detail ?? [])
          setAuditTotal(d.audit?.total_events ?? 0)
        }
      } catch {
        // 保持上次值
      }
      try {
        const a = await fetch('/v1/dashboard/activity?limit=6', { cache: 'no-store' })
        const d = (await a.json()) as { activity?: Telemetry['activity'] }
        if (alive && d.activity) setActivity(d.activity)
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
              <TaskCenter sessions={sessionDetail} />
            </div>

            {/* 右列：核心指标 + 系统状态 + 动态 + 目标 */}
            <div className="col col-right">
              <KeyMetrics auditTotal={auditTotal} sessions={liveSessions} />
              <SystemHealth
                ready={gwReady}
                liveSessions={liveSessions}
                uptime={gwUptime}
                provider={provider}
                auditTotal={auditTotal}
              />
              <UserProfileCard defaultPrincipal="default" />
              <Activity items={activity} />
              <OurGoal />
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}

export default App
