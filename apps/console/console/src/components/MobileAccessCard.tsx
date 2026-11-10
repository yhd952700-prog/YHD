/**
 * 「手机访问」—— 同一 Wi-Fi 下用手机浏览器接管驾驶舱。
 *
 * 与 `src/gateway/lan_access.py` 的端点一一对应：
 *   GET  /v1/lan-access/status                开关状态 / 配对码 / 设备列表
 *   POST /v1/lan-access/enable                {confirm_risk:true} 才开
 *   POST /v1/lan-access/disable               立即断开手机上的连接
 *   POST /v1/lan-access/pair/rotate           换一个 6 位配对码
 *   POST /v1/lan-access/devices/{id}/revoke   单踢（令牌立刻失效）
 *   POST /v1/lan-access/devices/revoke-all    全踢
 *
 * 几条不可让步的事实，界面上要说清楚：
 * * 默认关闭，且只有根账号能开；开启必须勾选风险确认（明文 HTTP）。
 * * 手机只持有设备 Cookie，业务令牌由电脑端代持；手机不能密码登录，也不能改这个开关。
 * * 手机能拒绝高危动作，但不能批准 —— 批准必须回电脑本机。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiFetch, useApi } from '../lib/api'
import { relativeTime } from '../lib/format'
import { qrMatrix } from '../lib/qr'

interface LanDevice {
  id: string
  name: string | null
  ip: string | null
  /** Unix 秒 */
  paired_at: number | null
  last_seen_at: number | null
  tokens: number
  online: boolean
}

interface LanStatus {
  enabled: boolean
  lan_ip: string | null
  port: number
  urls: string[]
  pair_code: string | null
  pair_expires_in: number
  pair_url: string | null
  devices: LanDevice[]
  online_count: number
  subnet: string | null
  /** 网络变化自动关闭时的说明；其它情况为 null */
  notice: string | null
  risk_notice: string
}

type Busy = 'enable' | 'disable' | 'rotate' | 'revoke' | 'revoke-all' | null

interface Message {
  tone: 'ok' | 'danger'
  text: string
}

/** 静区固定 2 格：扫码需要留白，界面上的白边不能省。 */
const QUIET = 2

function QrCode({ text, size = 168 }: { text: string; size?: number }) {
  const matrix = useMemo(() => {
    try {
      return qrMatrix(text)
    } catch {
      return null
    }
  }, [text])

  if (!matrix) return null
  const count = matrix.length
  // 每行把连续的黑格并成一条命令，29×29 的图也只有几十条，SVG 不至于臃肿。
  const path: string[] = []
  for (let row = 0; row < count; row += 1) {
    let start = -1
    for (let col = 0; col <= count; col += 1) {
      const dark = col < count && matrix[row][col]
      if (dark && start < 0) start = col
      if (!dark && start >= 0) {
        path.push(`M${start} ${row}h${col - start}v1h-${col - start}z`)
        start = -1
      }
    }
  }

  return (
    <svg
      className="os-lan-qr"
      width={size}
      height={size}
      viewBox={`${-QUIET} ${-QUIET} ${count + QUIET * 2} ${count + QUIET * 2}`}
      role="img"
      aria-label="手机配对二维码"
      shapeRendering="crispEdges"
    >
      <rect x={-QUIET} y={-QUIET} width={count + QUIET * 2} height={count + QUIET * 2} fill="#ffffff" />
      <path d={path.join('')} fill="#000000" />
    </svg>
  )
}

function groupedCode(code: string): string {
  return code.replace(/(\d{3})(\d{3})/, '$1 $2')
}

function clock(seconds: number): string {
  const value = Math.max(0, Math.floor(seconds))
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`
}

export interface MobileAccessCardProps {
  /** 只有根账号能开关；成员只读。 */
  canManage: boolean
}

export function MobileAccessCard({ canManage }: MobileAccessCardProps) {
  // 开着的时候轮询：设备上下线、配对码倒计时都要跟得上；关着就不再打扰后端。
  const initial = useApi<LanStatus>('/v1/lan-access/status', 0)
  const enabled = Boolean(initial.data?.enabled)
  const status = useApi<LanStatus>('/v1/lan-access/status', enabled ? 4000 : 0)

  const [busy, setBusy] = useState<Busy>(null)
  const [message, setMessage] = useState<Message | null>(null)
  const [riskAck, setRiskAck] = useState(false)
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null)

  const data = status.data ?? initial.data
  const reloadInitial = initial.reload
  const reloadStatus = status.reload
  const reload = useCallback(() => {
    reloadInitial()
    reloadStatus()
  }, [reloadInitial, reloadStatus])

  // 倒计时以服务端给的剩余秒数为准；每次轮询回来对齐一次，中间本地每秒递减。
  const serverRemaining = data?.pair_expires_in ?? 0
  const [remaining, setRemaining] = useState(0)
  const lastServerRemaining = useRef(serverRemaining)
  if (lastServerRemaining.current !== serverRemaining) {
    lastServerRemaining.current = serverRemaining
    setRemaining(serverRemaining)
  }
  useEffect(() => {
    if (remaining <= 0) return
    const timer = window.setTimeout(() => setRemaining((value) => Math.max(0, value - 1)), 1000)
    return () => window.clearTimeout(timer)
  }, [remaining])

  const post = useCallback(
    async (kind: Busy, path: string, body?: unknown, okText?: string) => {
      setBusy(kind)
      setMessage(null)
      try {
        await apiFetch<unknown>(path, {
          method: 'POST',
          body: body === undefined ? undefined : JSON.stringify(body),
        })
        reload()
        if (okText) setMessage({ tone: 'ok', text: okText })
      } catch (err) {
        setMessage({
          tone: 'danger',
          text: err instanceof Error ? err.message : '操作没有完成，请稍后重试。',
        })
      } finally {
        setBusy(null)
      }
    },
    [reload],
  )

  if (status.error && !data) {
    return (
      <p className="os-alert" role="alert">
        <span>读取手机访问状态失败：{status.error}</span>
        <button className="os-link" type="button" style={{ flex: 'none' }} onClick={reload}>
          重新读取
        </button>
      </p>
    )
  }

  if (!data) {
    return (
      <p className="os-note" role="status">
        <span>正在读取手机访问状态…</span>
      </p>
    )
  }

  const devices = data.devices ?? []
  const pairUrl = data.pair_url ?? ''
  const expired = enabled && remaining <= 0

  return (
    <div className="os-lan">
      {data.notice && (
        <p className="os-alert" role="alert">
          <span>{data.notice}</span>
        </p>
      )}

      {!canManage && (
        <p className="os-note" role="note">
          <span>手机访问的开关与设备管理只能由根账号在电脑上操作。</span>
        </p>
      )}

      <label className="os-check" htmlFor="lan-access-switch">
        <input
          id="lan-access-switch"
          type="checkbox"
          checked={enabled}
          disabled={!canManage || busy !== null}
          onChange={(event) => {
            if (!canManage) return
            if (event.target.checked) {
              if (!riskAck) {
                setRiskAck(false)
                setMessage({ tone: 'danger', text: '请先勾选下面的风险确认，再打开开关。' })
                return
              }
              void post(
                'enable',
                '/v1/lan-access/enable',
                { confirm_risk: true },
                '已开启。用手机连同一个 Wi-Fi，扫下面的二维码即可。',
              )
              return
            }
            void post('disable', '/v1/lan-access/disable', undefined, '已关闭，手机上的页面会立刻断开。')
          }}
        />
        允许同一 Wi-Fi 下的手机访问（默认关闭）
      </label>

      <p className="os-note os-lan-risk" role="note">
        <span>
          <strong>明文 HTTP 风险：</strong>
          {data.risk_notice}
        </span>
      </p>

      {!enabled && (
        <label className="os-check" htmlFor="lan-access-risk">
          <input
            id="lan-access-risk"
            type="checkbox"
            checked={riskAck}
            disabled={!canManage}
            onChange={(event) => setRiskAck(event.target.checked)}
          />
          我确认这在家里的可信任 Wi-Fi 下使用，并接受明文传输的风险
        </label>
      )}

      {enabled && (
        <>
          <div className="os-lan-pair">
            {pairUrl && !expired ? (
              <QrCode text={pairUrl} />
            ) : (
              <div className="os-lan-qr os-lan-qr-empty" role="status">
                {expired ? '配对码已过期' : '正在生成二维码…'}
              </div>
            )}
            <div className="os-lan-pair-info">
              <p className="os-lan-address">
                手机打开：<code>{data.urls[0] ?? `${data.lan_ip}:${data.port}`}</code>
              </p>
              <p>
                配对码{' '}
                <strong className="os-lan-code">
                  {data.pair_code ? groupedCode(data.pair_code) : '——'}
                </strong>{' '}
                <span className="os-hint">
                  {expired ? '已过期，请换一个。' : `还有 ${clock(remaining)}，一次性有效。`}
                </span>
              </p>
              <div className="os-kb-actions">
                <button
                  className="os-btn os-btn-ghost"
                  type="button"
                  disabled={busy !== null || !canManage}
                  onClick={() =>
                    void post('rotate', '/v1/lan-access/pair/rotate', undefined, '已换新配对码，旧的立刻作废。')
                  }
                >
                  {busy === 'rotate' ? '换码中…' : '换一个配对码'}
                </button>
              </div>
              <p className="os-hint">
                手机只持有一次性配对换来的设备凭证，不需要也不能输入电脑的管理员密码。
                手机可以看进度、审批和下达指令，但高危动作只能拒绝 —— 批准必须回到这台电脑。
              </p>
            </div>
          </div>

          <div className="os-lan-devices">
            <header className="os-lan-devices-head">
              <strong>已配对设备（{devices.length}）</strong>
              <span className="os-hint">
                {data.online_count > 0 ? `${data.online_count} 台在线` : '当前没有在线设备'}
              </span>
              {devices.length > 0 && (
                <button
                  className="os-link"
                  type="button"
                  style={{ flex: 'none', marginLeft: 'auto' }}
                  disabled={busy !== null || !canManage}
                  onClick={() => {
                    if (confirmRevoke !== 'all') {
                      setConfirmRevoke('all')
                      return
                    }
                    setConfirmRevoke(null)
                    void post('revoke-all', '/v1/lan-access/devices/revoke-all', undefined, '已全部断开，令牌同时作废。')
                  }}
                >
                  {confirmRevoke === 'all' ? '确认全部断开' : '全部断开'}
                </button>
              )}
            </header>

            {devices.length === 0 ? (
              <p className="os-note" role="status">
                <span>还没有手机完成配对。配对成功后，这里会显示设备名、首次配对时间和最后访问时间。</span>
              </p>
            ) : (
              <ul className="os-lan-device-list">
                {devices.map((device) => (
                  <li key={device.id} className="os-lan-device">
                    <span className="os-lan-dot" data-online={device.online ? 'true' : 'false'} />
                    <div className="os-lan-device-main">
                      <strong>{device.name || '未命名设备'}</strong>
                      <span className="os-hint">
                        {device.ip ? `${device.ip} · ` : ''}
                        {device.paired_at ? `首次配对 ${relativeTime(device.paired_at)}` : '首次配对时间未知'}
                        {device.last_seen_at
                          ? ` · 最后访问 ${relativeTime(device.last_seen_at)}`
                          : ''}
                      </span>
                    </div>
                    <button
                      className="os-btn os-btn-ghost"
                      type="button"
                      disabled={busy !== null || !canManage}
                      onClick={() => {
                        if (confirmRevoke !== device.id) {
                          setConfirmRevoke(device.id)
                          return
                        }
                        setConfirmRevoke(null)
                        void post(
                          'revoke',
                          `/v1/lan-access/devices/${encodeURIComponent(device.id)}/revoke`,
                          undefined,
                          '已断开这台设备，它的令牌已立刻作废。',
                        )
                      }}
                    >
                      {confirmRevoke === device.id ? '确认断开' : '断开'}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}

      {message && (
        <p className={message.tone === 'ok' ? 'os-note' : 'os-alert'} role={message.tone === 'ok' ? 'status' : 'alert'}>
          <span>{message.text}</span>
        </p>
      )}
    </div>
  )
}
