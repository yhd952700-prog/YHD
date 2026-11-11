/**
 * 系统设置 —— 形态覆盖 / 主题 / 会话 / 通知。
 *
 * 这一页存在的意义是让"三端"从一句口号变成一个可验证的事实：同一个账号、同一个
 * 会话，在这里可以立刻把界面切到桌面端 / 网页版 / 手机端，直接看到三种布局。
 *
 * 形态覆盖是**显示偏好**，不是权限开关 —— 切成手机端不会少任何一个可执行动作，
 * 只是把侧边栏换成底部导航、把多列网格收成单列。任何"某端不能做某事"的规则都
 * 必须落在服务端，不在这里。
 */

import { useCallback, useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type { DeviceMode, DeviceModeState, Theme } from '../lib/mode'
import { CLIENT_LABELS, LAYOUT_LABELS, MODE_HINTS, isDesktopShell, layoutChoicesFor } from '../lib/mode'
import type { Session } from '../lib/auth'
import { apiFetch, useApi } from '../lib/api'
import { humanDuration, permissionLabel, toneVar, useNow } from '../lib/format'
import type { OsNotification } from '../components/Shell'
import { Card, Dot, Guard } from '../components/ui'
import { ModelManager, providerLabel } from '../components/ModelManager'
import { knowledgeStatusView } from '../lib/knowledge'
import type { KnowledgeStatus } from '../lib/knowledge'
import { DiagnosticsExport } from '../components/DiagnosticsExport'
import { LogRetentionPanel } from '../components/LogRetentionPanel'
import { MobileAccessCard } from '../components/MobileAccessCard'
import { UpgradePanel } from '../components/UpgradePanel'
import { getReducedMotion, setReducedMotion } from '../lib/reducedMotion'
import {
  IconCheck,
  IconDesktop,
  IconGlobe,
  IconMobile,
  IconRefresh,
  IconServer,
} from '../components/icons'

export interface SettingsProps {
  device: DeviceModeState
  session: Session
  notifications: OsNotification[]
  onLogout: () => void
  /** 深链 `?p=settings&model=<目标>` 带来的模型；见 ModelManager 的同名参数。 */
  focusModelId?: string | null
}

const MODE_ICON: Record<DeviceMode, (props: { size?: number }) => ReactElement> = {
  desktop: IconDesktop,
  web: IconGlobe,
  mobile: IconMobile,
}

/** `beforeinstallprompt` 事件（Chromium 专有，TS 的 DOM 类型里没有）。 */
interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice?: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

/**
 * 知识页「去系统设置」带来的落点：`?focus=embedding`。
 *
 * 只负责定位，不改变任何配置 —— 向量化由部署环境决定，这里是只读视图。
 */
function readEmbeddingFocus(): boolean {
  try {
    return (new URLSearchParams(window.location.search).get('focus') ?? '') === 'embedding'
  } catch {
    return false
  }
}

/** 只读向量化状态卡：数据只来自 `GET /v1/knowledge/status`。 */
function EmbeddingStatus({ canManage }: { canManage: boolean }) {
  const status = useApi<KnowledgeStatus>('/v1/knowledge/status', 0)

  return (
    <>
      <Guard
        loading={status.loading}
        error={status.error}
        data={status.data}
        onRetry={status.reload}
      >
        {(data) => {
          const view = knowledgeStatusView(data)
          return (
            <>
              <dl className="os-kv">
                <dt>可用性</dt>
                <dd>
                  <span className="os-badge" data-tone={data.enabled ? 'ok' : 'danger'}>
                    {data.enabled ? '已启用' : '未启用'}
                  </span>
                </dd>
                <dt>向量模型</dt>
                <dd>
                  {data.provider ? providerLabel(data.provider) : '—'}
                  {data.model ? ` · ${data.model}` : ''}
                </dd>
                {/* 未启用时 vectors 可能是 0 回退值：不渲染成「0 条向量」。 */}
                {data.enabled && (
                  <>
                    <dt>向量总数</dt>
                    <dd>{data.vectors}</dd>
                  </>
                )}
                {!data.enabled && view.title && (
                  <>
                    <dt>不可用原因</dt>
                    <dd className="os-model-error">
                      <strong>{view.title}</strong>
                      <br />
                      {view.detail}
                      {view.command && (
                        <>
                          <br />
                          <code>{view.command}</code>
                        </>
                      )}
                    </dd>
                  </>
                )}
              </dl>
              <div className="os-kb-actions" style={{ marginTop: 12 }}>
                <button className="os-btn os-btn-ghost" onClick={status.reload}>
                  重新检测
                </button>
              </div>
            </>
          )
        }}
      </Guard>
      <p className="os-hint" style={{ marginTop: 12 }}>
        这张卡只读：向量化默认只走本机 Ollama；云端向量化需要在部署时设置环境变量，界面不提供选择。
      </p>
      {!canManage && <p className="os-hint">该设置由系统统一管理，当前账号只读。</p>}
    </>
  )
}

/** `GET/PUT /v1/network/research`：联网检索的开关状态与告知文案。 */
interface ResearchState {
  enabled: boolean
  stored_enabled: boolean
  /** 环境变量在强制覆盖时，界面开关改了也不生效 —— 必须如实说明。 */
  env_override: boolean
  providers: string[]
  provider_hosts: string[]
  disclosure: string
  max_phrase_chars: number
}

interface AuthorityModeState {
  /** 本进程启动时快照到的生效模式。 */
  mode: 'standard' | 'full'
  mode_label: string
  /** 重启后会生效的值；与 mode 不同即说明改了还没重启。 */
  next_mode: 'standard' | 'full'
  restart_required: boolean
  env_var: string
  env_value: string
}

/** `GET/PUT /v1/policy/host-command-confirmation`：危险命令的一次确认。 */
interface HostCommandConfirmState {
  confirmation_enabled: boolean
  stored_enabled: boolean
  env_override: boolean
  env_var: string
  env_value: string
  note: string
}

export function Settings({
  device,
  session,
  notifications,
  onLogout,
  focusModelId,
}: SettingsProps) {
  const { mode, detected, override, theme, setOverride, setTheme } = device
  // 手机端不渲染宽屏 / 紧凑选项：360px 下切过去会横向溢出且底栏消失。
  const layoutChoices = layoutChoicesFor(detected)

  // 安装提示：桌面端会触发 beforeinstallprompt，手机端（iOS Safari）不会 —— 后者
  // 只能手动"添加到主屏幕"。拿不到事件时如实提示手动方式，而不是放一个按了没反应的按钮。
  const [installEvent, setInstallEvent] = useState<InstallPromptEvent | null>(null)
  // 减弱动效：系统偏好默认生效；这里的手动开关会持久化并覆盖它。
  const [reducedMotion, setReducedMotionState] = useState(() => getReducedMotion())
  // 初始值用惰性初始化器取：在 effect 里 setState 会多跑一轮渲染，而这里根本不需要
  // 订阅变化 —— `appinstalled` 事件已经覆盖了"装完之后变成 standalone"这一种变化。
  const [standalone, setStandalone] = useState(() => {
    try {
      return window.matchMedia('(display-mode: standalone)').matches
    } catch {
      return false
    }
  })

  useEffect(() => {
    const onPrompt = (event: Event) => {
      event.preventDefault()
      setInstallEvent(event as InstallPromptEvent)
    }
    const onInstalled = () => {
      setInstallEvent(null)
      setStandalone(true)
    }
    window.addEventListener('beforeinstallprompt', onPrompt)
    window.addEventListener('appinstalled', onInstalled)
    return () => {
      window.removeEventListener('beforeinstallprompt', onPrompt)
      window.removeEventListener('appinstalled', onInstalled)
    }
  }, [])

  const install = useCallback(async () => {
    if (!installEvent) return
    await installEvent.prompt()
    const choice = await installEvent.userChoice
    if (choice?.outcome === 'accepted') setStandalone(true)
    setInstallEvent(null)
  }, [installEvent])

  // 知识页「去系统设置查看向量化」的落点：滚动 + 聚焦 + 高亮真卡片。
  // 落点写进 URL（`?focus=embedding`）而不是只做页内跳转，刷新后仍能复查同一张卡。
  // 高亮状态在首帧就定下来（惰性初始化），不在 effect 里 setState 再触发一轮渲染。
  const [embeddingHighlighted] = useState(readEmbeddingFocus)
  useEffect(() => {
    if (!readEmbeddingFocus()) return
    const target = document.getElementById('settings-embedding')
    if (!target) return
    target.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
    // Card 是 section，默认不可聚焦；这里临时给它一个程序化焦点位置。
    target.tabIndex = -1
    target.focus?.()
    try {
      const url = new URL(window.location.href)
      url.searchParams.delete('focus')
      window.history.replaceState(null, '', url.toString())
    } catch {
      // 受限环境忽略。
    }
  }, [])

  // 联网检索：默认关闭。开启前必须把「检索词会发到哪几个站点」说清楚。
  const research = useApi<ResearchState>('/v1/network/research', 0)
  const [researchBusy, setResearchBusy] = useState(false)
  const [researchError, setResearchError] = useState<string | null>(null)
  const researchOn = research.data?.enabled ?? false
  // 写策略只归根账号：成员保留只读视角，避免点一下就是 403。
  const canManageSettings = session.isRoot

  const setResearch = useCallback(
    async (next: boolean) => {
      setResearchBusy(true)
      setResearchError(null)
      try {
        await apiFetch<ResearchState>('/v1/network/research', {
          method: 'PUT',
          body: JSON.stringify({ enabled: next }),
        })
        research.reload()
      } catch (err) {
        setResearchError(err instanceof Error ? err.message : '保存失败')
      } finally {
        setResearchBusy(false)
      }
    },
    // research.reload 是稳定引用（useApi 内部 useCallback）；依赖只列它即可。
    [research.reload],
  )

  // 授权模式：展示"当前生效的是什么模式"与"怎么切回标准模式"。
  // 切换只写盘，**重启后生效**（不做热切，避免任务中途出现半套权限窗口）。
  const authorityMode = useApi<AuthorityModeState>('/v1/policy/authority-mode', 0)
  const [modeBusy, setModeBusy] = useState(false)
  const [modeError, setModeError] = useState<string | null>(null)
  const authorityFull = authorityMode.data?.mode === 'full'

  const changeAuthorityMode = useCallback(
    async (mode: 'standard' | 'full') => {
      setModeBusy(true)
      setModeError(null)
      try {
        await apiFetch<AuthorityModeState>('/v1/policy/authority-mode', {
          method: 'PUT',
          body: JSON.stringify({ mode, reason: '设置页切换授权模式' }),
        })
        authorityMode.reload()
      } catch (err) {
        setModeError(err instanceof Error ? err.message : '保存失败')
      } finally {
        setModeBusy(false)
      }
    },
    [authorityMode.reload],
  )

  // 危险命令「一次确认」：默认开启；关闭立即生效，但关闭动作会记入审计。
  const hostConfirm = useApi<HostCommandConfirmState>(
    '/v1/policy/host-command-confirmation',
    0,
  )
  const [confirmBusy, setConfirmBusy] = useState(false)
  const [confirmError, setConfirmError] = useState<string | null>(null)

  const setHostConfirm = useCallback(
    async (enabled: boolean) => {
      setConfirmBusy(true)
      setConfirmError(null)
      try {
        await apiFetch<HostCommandConfirmState>('/v1/policy/host-command-confirmation', {
          method: 'PUT',
          body: JSON.stringify({
            enabled,
            reason: enabled ? '设置页恢复危险命令确认' : '设置页关闭危险命令确认',
          }),
        })
        hostConfirm.reload()
      } catch (err) {
        setConfirmError(err instanceof Error ? err.message : '保存失败')
      } finally {
        setConfirmBusy(false)
      }
    },
    [hostConfirm.reload],
  )

  // 剩余时长必须来自"状态化的时间"：在渲染里直接调 `Date.now()` 是不纯的
  // （同样的 props 会渲染出不同结果），React 的纯度检查会拦下来。
  const now = useNow()
  const remaining = session.expiresAt ? Math.max(0, session.expiresAt - now / 1000) : 0

  return (
    <div className="os-grid">
      {/* ---------- 首次使用提示 ----------
          这两件事如果留在各自卡片底部，900px 视口下分别位于距顶 1200px / 1800px
          附近，等同于不存在。提到网格最前面后，页面不滚动就能看到；按钮只是把
          视线带到对应卡片，不改变任何配置。 */}
      <div className="os-span-12">
        <div className="os-attention" role="status" aria-label="首次使用提示">
          <span className="os-attention-lead">第一次用</span>
          <span className="os-attention-item" data-tone="ok">
            <strong>模型可以最后再配</strong>
            先跳过不影响浏览，之后回到「模型管理」补上 Key 即可。
            <button
              className="os-link"
              onClick={() =>
                document.getElementById('settings-models')?.scrollIntoView?.({
                  behavior: 'smooth',
                  block: 'start',
                })
              }
            >
              去配置模型
            </button>
          </span>
          {/* 「关闭窗口退托盘」只对桌面壳成立。浏览器里没有托盘、也没有
              "关窗口仍在跑"这回事，网页版读到这句话就是自相矛盾的自述。 */}
          {isDesktopShell() && (
            <span className="os-attention-item" data-tone="warn">
              <strong>关闭窗口不会退出</strong>
              桌面端只有一个鎏灏窗口；关闭后会退到系统托盘，本地服务继续运行，需要时从托盘重新打开即可。
              <button
                className="os-link"
                onClick={() =>
                  document.getElementById('settings-runtime')?.scrollIntoView?.({
                    behavior: 'smooth',
                    block: 'start',
                  })
                }
              >
                查看运行说明
              </button>
            </span>
          )}
        </div>
      </div>

      <Card
        title="布局与主题预览"
        sub="只影响排版与配色，不影响权限；身份（桌面端 / 网页版 / 手机端）由运行载体决定"
        span={12}
      >
        <div className="os-choice">
          <button
            className="os-choice-option"
            data-selected={override === null ? 'true' : 'false'}
            onClick={() => setOverride(null)}
          >
            <span className="os-choice-title">
              <IconRefresh size={15} /> 跟随窗口
            </span>
            <span className="os-choice-desc">
              桌面客户端固定为宽屏；浏览器按视口宽度判定（当前 {LAYOUT_LABELS[detected]}）
            </span>
          </button>

          {layoutChoices.map((candidate) => {
            const Icon = MODE_ICON[candidate]
            return (
              <button
                key={candidate}
                className="os-choice-option"
                data-selected={override === candidate ? 'true' : 'false'}
                onClick={() => setOverride(candidate)}
              >
                <span className="os-choice-title">
                  <Icon size={15} /> {LAYOUT_LABELS[candidate]}
                </span>
                <span className="os-choice-desc">{MODE_HINTS[candidate]}</span>
              </button>
            )
          })}
        </div>
        <div className="os-note" style={{ marginTop: 12 }}>
          <span>
            当前生效布局：<strong>{LAYOUT_LABELS[mode]}</strong>
            {override ? '（已手动覆盖）' : '（跟随窗口）'}。
            三端共用同一批端点与同一套鉴权 —— 断点里没有、也不该有权限边界。
            {detected === 'mobile' && ' 手机端固定使用窄屏布局，不提供宽屏选项。'}
          </span>
        </div>
      </Card>

      <Card
        id="settings-models"
        title="模型管理"
        sub="新增 / 编辑 / 删除模型配置；测试连通并保存后，下一条消息即生效。密钥只显示首尾几位，不显示完整值。"
        span={12}
      >
        <ModelManager session={session} focusModelId={focusModelId} />
      </Card>

      <Card
        id="settings-embedding"
        title="知识库向量化"
        sub="只读：向量模型与向量库状态都来自服务端，这里不提供修改"
        span={12}
        className={embeddingHighlighted ? 'sc-panel-active' : undefined}
      >
        <EmbeddingStatus canManage={canManageSettings} />
      </Card>

      <Card
        id="settings-privacy"
        title="隐私与联网"
        sub="这里决定「什么内容可以离开本机」。默认状态：除了你自己配置的模型，没有任何外发。"
        span={12}
      >
        <div className="os-note" role="note" style={{ marginBottom: 12 }}>
          <span>
            <strong>知识库向量化：</strong>
            向量化默认只走本机 Ollama，文档分片不离开本机。云端向量化目前不提供界面配置：
            要走云端需要在部署时设置 <code>LIUHAO_EMBEDDING_PROVIDER</code> 与对应凭据/地址。
            环境里存在 <code>OPENAI_API_KEY</code> 不会自动改走云端。
          </span>
        </div>

        {!canManageSettings && (
          <div className="os-note" role="note" style={{ marginBottom: 12 }}>
            <span>该设置由系统统一管理，当前账号只读。</span>
          </div>
        )}

        <label className="os-check" htmlFor="settings-web-research">
          <input
            id="settings-web-research"
            type="checkbox"
            checked={researchOn}
            disabled={!canManageSettings || researchBusy || research.loading || !research.data}
            onChange={(event) => {
              if (!canManageSettings) return
              void setResearch(event.target.checked)
            }}
          />
          允许「联网检索」：把检索词发到 hn.algolia.com / api.github.com / export.arxiv.org 三个公开接口
        </label>

        {research.loading && (
          <p className="os-note" role="status">
            <span>正在读取联网检索设置…开关暂时是灰的，读完就能改。</span>
          </p>
        )}

        {research.error && (
          <p className="os-alert" role="alert">
            <span>
              联网检索设置没读到：{research.error}。开关先灰着，读回来再改。
            </span>
            <button
              className="os-link"
              type="button"
              style={{ flex: 'none' }}
              onClick={research.reload}
            >
              重试
            </button>
          </p>
        )}

        {researchError && (
          <div className="os-alert" role="alert">
            <span>{researchError}</span>
          </div>
        )}

        {research.data && (
          <>
            {researchOn ? (
              <p className="os-note" role="status">
                <span>{research.data.disclosure}</span>
              </p>
            ) : (
              <p className="os-note" role="status">
                <span>
                  当前关闭：助手不会向任何外部站点发送检索词，只会用本机模型与你配置的模型服务商。
                </span>
              </p>
            )}
            <p className="os-note">
              <span>
                开启后只发送<strong>提炼后的关键词</strong>（最多 {research.data.max_phrase_chars} 个字符）：
                会先去掉邮箱、电话、密钥和链接。只发送提炼后的关键词；不发送你的对话内容、对话历史、个人画像、账号信息或 API Key。
                关闭后立即生效，无需重启。
              </span>
            </p>
            {research.data.env_override && (
              <p className="os-note" role="alert">
                <span>
                  当前进程的环境变量 <code>LIUHAO_WEB_RESEARCH</code> 正在强制覆盖这个开关，
                  界面上的修改不会改变实际行为。要由界面控制，请先移除该环境变量。
                </span>
              </p>
            )}
          </>
        )}
      </Card>

      <Card
        id="settings-authority"
        title="授权模式"
        sub="决定「高风险操作还要不要你逐次批准」。默认是标准模式；收回立即生效，开启需重启。"
        span={12}
      >
        {authorityMode.loading && (
          <p className="os-note" role="status">
            <span>正在读取当前授权模式…</span>
          </p>
        )}

        {authorityMode.error && (
          <p className="os-alert" role="alert">
            <span>
              授权模式没读到：{authorityMode.error}。先按标准模式理解：高风险操作仍需批准。
            </span>
            <button
              className="os-link"
              type="button"
              style={{ flex: 'none' }}
              onClick={authorityMode.reload}
            >
              重试
            </button>
          </p>
        )}

        {authorityMode.data && (
          <>
            <p className="os-note" role="status">
              <span>
                当前模式：
                <strong>{authorityMode.data.mode_label}（{authorityMode.data.mode}）</strong>
                。
                {authorityFull
                  ? '你手动派出的任务、AI 员工、定时任务和自主接活都不再逐次等批准，但仍会留下完整审计（含哪个员工、哪个任务）。'
                  : '高风险操作按审批流程执行，审批结果会留在审计里。'}
              </span>
            </p>
            <p className="os-note">
              <span>
                <strong>怎么切回标准模式：</strong>
                选下面的「标准」并保存，<strong>立即生效，不用重启</strong>，同时会作废全权期间
                发出的临时授权。反过来开启「完全授权」需要<strong>重启程序</strong>才生效
                （放宽权限不做热切，避免一次任务中途出现半套权限）。两种切换都会记入审计
                （谁、何时、从什么切到什么）。
              </span>
            </p>

            {!canManageSettings && (
              <div className="os-note" role="note">
                <span>授权模式只有根账号可以切换，当前账号只读。</span>
              </div>
            )}

            <div className="os-choice">
              {(
                [
                  ['standard', '标准'],
                  ['full', '完全授权'],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  className="os-choice-option"
                  data-selected={authorityMode.data?.next_mode === value ? 'true' : 'false'}
                  disabled={!canManageSettings || modeBusy || authorityMode.data?.next_mode === value}
                  onClick={() => {
                    if (!canManageSettings) return
                    void changeAuthorityMode(value)
                  }}
                >
                  <span className="os-choice-title">
                    {authorityMode.data?.next_mode === value && <IconCheck size={15} />}
                    {label}
                  </span>
                </button>
              ))}
            </div>

            {authorityMode.data.restart_required && (
              <p className="os-note" role="alert">
                <span>
                  已保存为「{authorityMode.data.next_mode === 'full' ? '完全授权' : '标准'}」，
                  <strong>重启后生效</strong>；当前进程仍是「{authorityMode.data.mode_label}」。
                </span>
              </p>
            )}

            {authorityMode.data.env_value && (
              <p className="os-note" role="alert">
                <span>
                  环境变量 <code>{authorityMode.data.env_var}</code> 正在强制指定模式
                  （当前值 <code>{authorityMode.data.env_value}</code>），界面选择不会改变实际行为。
                  要由界面控制，请先移除该环境变量。
                </span>
              </p>
            )}

            {modeError && (
              <div className="os-alert" role="alert">
                <span>{modeError}</span>
              </div>
            )}
          </>
        )}
      </Card>

      <Card
        id="settings-host-command-confirm"
        title="危险命令确认"
        sub="全权模式下，格式化、分区、关机重启、递归强删执行前问一次。默认开启；关闭立即生效，但会记入审计。"
        span={12}
      >
        {!canManageSettings && (
          <div className="os-note" role="note" style={{ marginBottom: 12 }}>
            <span>该设置由系统统一管理，当前账号只读。</span>
          </div>
        )}

        <label className="os-check" htmlFor="settings-host-command-confirm-toggle">
          <input
            id="settings-host-command-confirm-toggle"
            type="checkbox"
            checked={hostConfirm.data?.confirmation_enabled ?? true}
            disabled={
              !canManageSettings || confirmBusy || hostConfirm.loading || !hostConfirm.data
            }
            onChange={(event) => {
              if (!canManageSettings) return
              void setHostConfirm(event.target.checked)
            }}
          />
          危险命令执行前先问我一次（关闭后不再询问）
        </label>

        {hostConfirm.loading && (
          <p className="os-note" role="status">
            <span>正在读取危险命令确认设置…开关暂时是灰的，读完就能改。</span>
          </p>
        )}

        {hostConfirm.error && (
          <p className="os-alert" role="alert">
            <span>危险命令确认设置没读到：{hostConfirm.error}。先按「开启」理解。</span>
            <button
              className="os-link"
              type="button"
              style={{ flex: 'none' }}
              onClick={hostConfirm.reload}
            >
              重试
            </button>
          </p>
        )}

        {hostConfirm.data && (
          <>
            <p className="os-note" role="status">
              <span>
                当前：
                <strong>
                  {hostConfirm.data.confirmation_enabled ? '开启（执行前询问）' : '已关闭（不再询问）'}
                </strong>
                。{hostConfirm.data.note}
              </span>
            </p>
            <p className="os-note">
              <span>
                这不是安全边界：它只是危险命令这一层的减速带。格式化、分区、关机重启属于
                硬件/系统级操作，本程序没有文件系统级拦截能力；命令被拆开拼接、或由子进程
                自己生成时，这一层可能看不到。真正的硬边界是「不提权」——始终以当前 Windows
                用户身份运行。
              </span>
            </p>
            {hostConfirm.data.env_override && (
              <p className="os-note" role="alert">
                <span>
                  环境变量 <code>{hostConfirm.data.env_var}</code> 正在强制覆盖这个开关
                  （当前值 <code>{hostConfirm.data.env_value}</code>），界面上的修改不会改变实际行为。
                </span>
              </p>
            )}
          </>
        )}

        {confirmError && (
          <div className="os-alert" role="alert">
            <span>{confirmError}</span>
          </div>
        )}
      </Card>

      <Card
        id="settings-mobile-access"
        title="手机访问"
        sub="同一 Wi-Fi 下用手机浏览器看进度、审批、下达指令。默认关闭；离开这个 Wi-Fi 就打不开。"
        span={12}
      >
        <MobileAccessCard canManage={canManageSettings} />
      </Card>

      <Card
        id="settings-upgrade"
        title="软件更新"
        sub="检查新版本、下载并安装；安装失败或新版本启动不通过会自动回滚到升级前的版本。"
        span={12}
      >
        {canManageSettings ? (
          <UpgradePanel />
        ) : (
          <p className="os-note" role="note">
            软件更新由系统统一管理；当前账号不提供检查、安装与回滚操作。
          </p>
        )}
      </Card>

      <Card title="外观主题" sub="可跟随形态，也可手动指定" span={6}>
        <div className="os-choice">
          {(['dark', 'light'] as Theme[]).map((candidate) => (
            <button
              key={candidate}
              className="os-choice-option"
              data-selected={theme === candidate ? 'true' : 'false'}
              onClick={() => setTheme(candidate)}
            >
              <span className="os-choice-title">
                {theme === candidate && <IconCheck size={15} />}
                {candidate === 'dark' ? '深色' : '浅色'}
              </span>
              <span className="os-choice-desc">
                {candidate === 'dark' ? '宽屏 / 窄屏布局默认' : '紧凑布局默认'}
              </span>
            </button>
          ))}
          <button className="os-choice-option" data-selected="false" onClick={() => setTheme(null)}>
            <span className="os-choice-title">
              <IconRefresh size={15} /> 跟随形态
            </span>
            <span className="os-choice-desc">清除手动覆盖，回到形态默认</span>
          </button>
          <button
            className="os-choice-option"
            data-selected={reducedMotion ? 'true' : 'false'}
            aria-pressed={reducedMotion}
            onClick={() => {
              const next = !reducedMotion
              setReducedMotionState(next)
              setReducedMotion(next)
            }}
          >
            <span className="os-choice-title">
              {reducedMotion && <IconCheck size={15} />} 减弱动效
            </span>
            <span className="os-choice-desc">
              停掉脉冲、闪烁与旋转；系统「减少动态效果」开启时默认启用
            </span>
          </button>
        </div>
      </Card>

      <Card title="当前会话" sub="令牌由服务端签发与校验，前端只保存不决策" span={6}>
        <dl className="os-kv">
          <dt>账号</dt>
          <dd>{session.principal || '—'}</dd>
          <dt>显示名</dt>
          <dd>{session.displayName || '—'}</dd>
          <dt>身份范围</dt>
          <dd>{session.scope ? '全部权限' : '—'}</dd>
          <dt>登录来源端</dt>
          <dd>{CLIENT_LABELS[session.client] ?? session.client}</dd>
          <dt>会话剩余</dt>
          <dd>{session.expiresAt ? humanDuration(remaining) : '由服务端裁决'}</dd>
          <dt>过期时刻</dt>
          <dd>
            {session.expiresAt ? new Date(session.expiresAt * 1000).toLocaleString('zh-CN') : '—'}
          </dd>
          <dt>凭据权限</dt>
          <dd>
            {session.permissions.length > 0
              ? session.permissions.map(permissionLabel).join(' / ')
              : '（无额外权限）'}
          </dd>
        </dl>
        <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
          <button className="os-btn os-btn-ghost" onClick={onLogout}>
            退出登录
          </button>
        </div>
      </Card>

      <Card
        id="settings-runtime"
        title="安装到设备"
        sub="PWA —— 桌面浏览器独立窗口 / 手机主屏图标"
        span={6}
      >
        {standalone ? (
          <div className="os-list">
            <div className="os-row">
              <span className="os-dot" style={{ background: 'var(--os-ok)' }} />
              <div className="os-row-main">
                <div className="os-row-title">已在独立窗口中运行</div>
                <div className="os-row-sub">当前就是从已安装的应用打开的</div>
              </div>
              <span className="os-badge" data-tone="ok">
                已安装
              </span>
            </div>
          </div>
        ) : (
          <>
            <div className="os-list">
              <div className="os-row">
                <span className="os-dot" style={{ background: 'var(--os-accent)' }} />
                <div className="os-row-main">
                  <div className="os-row-title">桌面浏览器（Chrome / Edge）</div>
                  <div className="os-row-sub">
                    地址栏右侧的「安装」图标，或浏览器菜单 → 安装应用
                  </div>
                </div>
              </div>
              <div className="os-row">
                <span className="os-dot" style={{ background: 'var(--os-accent)' }} />
                <div className="os-row-main">
                  <div className="os-row-title">手机浏览器（iOS Safari / Android Chrome）</div>
                  <div className="os-row-sub">
                    分享 → 添加到主屏幕。iOS 不派发安装事件，只能用这个手动方式。
                  </div>
                </div>
              </div>
            </div>
            {installEvent && (
              <div style={{ marginTop: 14 }}>
                <button className="os-btn" onClick={() => void install()}>
                  立即安装
                </button>
              </div>
            )}
          </>
        )}
      </Card>

      <Card
        title="日志与磁盘"
        sub="看占用、改保留策略、立即清理 —— 只删旧备份，不动正在写的日志"
        span={12}
      >
        <LogRetentionPanel canManage={canManageSettings} />
      </Card>

      <Card title="遇到问题" sub="导出排障信息 —— 先看清包含什么，再决定发给谁" span={12}>
        <DiagnosticsExport />
      </Card>

      <Card title="系统通知" sub="与顶栏铃铛同一份数据" span={12}>
        {notifications.length === 0 ? (
          <div className="os-empty">
            <strong>暂无通知</strong>
            <span>所有已接入项状态正常</span>
          </div>
        ) : (
          <div className="os-list">
            {notifications.map((item) => (
              <div className="os-row" key={item.id}>
                <span
                  className="os-dot"
                  style={{ background: `var(--os-${toneVar(item.tone)})` }}
                />
                <div className="os-row-main">
                  <div className="os-row-title">{item.title}</div>
                  <div className="os-row-sub">{item.detail}</div>
                </div>
                <span className="os-badge" data-tone={item.tone === 'muted' ? 'muted' : item.tone}>
                  {item.tone === 'danger' ? '严重' : item.tone === 'warn' ? '注意' : '正常'}
                </span>
              </div>
            ))}
          </div>
        )}
        <div className="os-legend" style={{ marginTop: 12 }}>
          <Dot tone="muted" label={<>共 {notifications.length} 条</>} />
          <Dot tone="accent" label={<>当前布局 {LAYOUT_LABELS[mode]}</>} />
          <span className="os-legend-item">
            <IconServer size={13} /> 数据来自真实端点
          </span>
        </div>
      </Card>
    </div>
  )
}
