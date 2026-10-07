/**
 * 生产预检（`GET /v1/production/preflight`）—— 只读产品面。
 *
 * 为什么这个页面存在
 * ------------------
 * 该端点此前**真实存在且返回真实数据，但界面上没有任何入口**：一份已经算好的
 * "这份部署能不能上生产"从来递不到人眼前。本页把它渲染出来。
 *
 * 诚实优先（三条，都是刻意的）
 * ----------------------------
 * 1. **不美化**。`ok: false` 是"现在不该上生产"，不是"有两条提示"。因此 critical
 *    用 danger 色并置顶，页面顶部给出明确结论，绝不把它渲染成一张平静的清单。
 * 2. **不推测环境**。后端 `app_env` 为空表示 `APP_ENV` 未声明，而"是否 production"
 *    由 `src.security.posture` 独立裁决（它认 `LIUHAO_ENV > ENVIRONMENT > APP_ENV`）。
 *    所以本页**只如实显示 `app_env` 原值**，不据此推断"当前是生产/开发"——
 *    那种推断正是 preflight 自己注释里点名过的 fail-open 来源。
 * 3. **只读、无副作用**。本页只发一个 GET。签发凭据/改配置**不在这里**：
 *    预检是"体检报告"，不是"修复按钮"。
 *
 * 边界
 * ----
 * 端点受 `require_human_principal` 保护（令牌必须带 `sub`），无令牌返回 401 ——
 * 由 `apiFetch` 统一处理（清会话 + 广播未认证），本页不重复处理鉴权。
 *
 * 报告只含键名 / 代码 / 严重级别，**不含任何配置值**，因此本页无处可显示密钥，
 * 也不应该新增这样的显示。
 */

import type { PreflightFinding, PreflightPayload } from '../lib/contracts'
import { useApi } from '../lib/api'
import { preflightVerdict, severityTone } from '../lib/preflightLogic'
import { Card, Dot, Guard } from '../components/ui'

/** 预检结论的刷新间隔：配置不会秒级变化，60s 足够，且不刷爆网关。 */
const REFRESH = 60_000

function FindingRow({ finding }: { finding: PreflightFinding }) {
  return (
    <div className="os-row">
      <span
        className="os-dot"
        style={{ background: `var(--os-${severityTone(finding.severity)})` }}
      />
      <div className="os-row-main">
        <div className="os-row-title">
          {/* code 是机器可读判据，key 是被检查的变量 —— 两个都显示，
              因为运维拿到其中任何一个都要能对照后端日志。 */}
          <code>{finding.code}</code>
          <span className="os-row-tail"> · {finding.key}</span>
        </div>
        <div className="os-row-sub">{finding.message}</div>
      </div>
      <span className="os-badge" data-tone={severityTone(finding.severity)}>
        {finding.severity === 'warning' ? '警告' : '严重'}
      </span>
    </div>
  )
}

export function Preflight() {
  const preflight = useApi<PreflightPayload>('/v1/production/preflight', REFRESH)
  const data = preflight.data

  // 结论先于明细：这是"能不能上生产"的答案，不该让人翻到列表底部自己总结。
  // 判定逻辑在 lib/preflightLogic（可被真实执行的纯函数，有单测），
  // 本页只负责把后端给的计数喂进去 —— 不自己重新数一遍。
  const verdict = data
    ? preflightVerdict(data.ok, data.critical_count, data.warning_count)
    : null

  return (
    <div className="os-grid">
      <div className="os-span-12">
        <div className="os-kpis">
          <div className="os-kpi">
            <div className="os-kpi-label">预检结论</div>
            {verdict ? (
              <div
                className="os-kpi-value"
                style={{
                  // blocked 是唯一决定"红还是绿"的事实：ok=true 但有警告时
                  // 仍是绿（警告不阻断），ok=false 一律红。
                  color: verdict.blocked ? 'var(--os-danger)' : 'var(--os-ok)',
                  fontSize: 20,
                }}
              >
                {data?.ok ? '通过' : '未通过'}
              </div>
            ) : (
              <div className="os-skeleton" style={{ height: 30, margin: '6px 0 4px' }} />
            )}
            <div className="os-kpi-foot">
              {verdict ? `${verdict.title} · ${verdict.detail}` : '来自生产配置预检'}
            </div>
          </div>
          <div className="os-kpi">
            <div className="os-kpi-label">严重问题</div>
            <div
              className="os-kpi-value"
              style={{ color: (data?.critical_count ?? 0) > 0 ? 'var(--os-danger)' : undefined }}
            >
              {data ? String(data.critical_count) : '—'}
            </div>
            <div className="os-kpi-foot">critical：判定 ok 的唯一依据</div>
          </div>
          <div className="os-kpi">
            <div className="os-kpi-label">警告</div>
            <div
              className="os-kpi-value"
              style={{ color: (data?.warning_count ?? 0) > 0 ? 'var(--os-warn)' : undefined }}
            >
              {data ? String(data.warning_count) : '—'}
            </div>
            <div className="os-kpi-foot">warning：不影响 ok，但需知悉</div>
          </div>
          <div className="os-kpi">
            <div className="os-kpi-label">APP_ENV</div>
            <div className="os-kpi-value" style={{ fontSize: 20 }}>
              {data ? data.app_env || '未声明' : '—'}
            </div>
            {/* 刻意不据此推断"当前是生产/开发"：真实姿态由 src.security.posture
                裁决（认 LIUHAO_ENV > ENVIRONMENT > APP_ENV），只看 APP_ENV 会漏判。 */}
            <div className="os-kpi-foot">原样显示；是否生产由 posture 独立裁决</div>
          </div>
        </div>
      </div>

      <Card
        title="配置发现"
        sub="真实预检结果 —— 只含键名、代码与严重级别，不含任何配置值"
        span={7}
        action={
          <button className="os-link" onClick={preflight.reload}>
            刷新
          </button>
        }
      >
        <Guard
          loading={preflight.loading}
          error={preflight.error}
          data={data}
          isEmpty={(payload) => payload.findings.length === 0}
          emptyTitle="没有任何配置发现"
          emptyHint="既无 critical 也无 warning 项。"
          onRetry={preflight.reload}
        >
          {(payload) => (
            <>
              <div className="os-list">
                {payload.findings.map((finding) => (
                  <FindingRow
                    key={`${finding.key}:${finding.code}`}
                    finding={finding}
                  />
                ))}
              </div>
              <div className="os-legend" style={{ marginTop: 10 }}>
                <Dot tone="danger" label={<>严重 critical · {payload.critical_count}</>} />
                <Dot tone="warn" label={<>警告 warning · {payload.warning_count}</>} />
              </div>
              {/* 一句话说清 ok 的判据，避免读者把 warning 也当成阻塞。 */}
              <p className="os-note" style={{ marginTop: 8 }}>
                <span>
                  ok = 没有 critical 项（warning 不影响 ok）。
                </span>
              </p>
            </>
          )}
        </Guard>
      </Card>

      <Card title="这一页不做什么" sub="边界声明 —— 预检是体检报告，不是修复入口" span={5}>
        <div className="os-list">
          <div className="os-row">
            <span className="os-dot" style={{ background: 'var(--os-accent)' }} />
            <div className="os-row-main">
              <div className="os-row-title">只读</div>
              <div className="os-row-sub">
                本页只发一个 GET，没有任何写操作。
              </div>
            </div>
          </div>
          <div className="os-row">
            <span className="os-dot" style={{ background: 'var(--os-accent)' }} />
            <div className="os-row-main">
              <div className="os-row-title">不显示配置值</div>
              <div className="os-row-sub">
                后端只下发键名与代码；密钥内容永远不会出现在这个响应里。
              </div>
            </div>
          </div>
          <div className="os-row">
            <span className="os-dot" style={{ background: 'var(--os-accent)' }} />
            <div className="os-row-main">
              <div className="os-row-title">不推断运行姿态</div>
              <div className="os-row-sub">
                APP_ENV 为空只代表未声明，不代表非生产；真实姿态由 posture 裁决。
              </div>
            </div>
          </div>
          <div className="os-row">
            <span className="os-dot" style={{ background: 'var(--os-accent)' }} />
            <div className="os-row-main">
              <div className="os-row-title">需要人类令牌</div>
              <div className="os-row-sub">
                端点受 require_human_principal 保护，无令牌返回 401。
              </div>
            </div>
          </div>
        </div>
      </Card>
    </div>
  )
}
