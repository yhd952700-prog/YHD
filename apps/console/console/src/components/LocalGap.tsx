/**
 * 后端确实没有的能力，在页面上如实说「本地服务还没提供」。
 *
 * 这个组件只做三件事：给统一的诚实文案、列出缺哪些接口/字段、给出仍可用的退路。
 * 它刻意不接收任何「示例数据」参数 —— 免得有人顺手把占位数据当成真实数据渲染。
 */
import './LocalGap.css'

export function LocalGap({
  title,
  summary,
  missing,
  note,
}: {
  title: string
  summary: string
  /** 缺失的接口/字段清单；每条都应能对应到后端真实代码。 */
  missing: readonly string[]
  /** 仍可用的部分，避免用户以为整页都坏了。 */
  note?: string
}) {
  return (
    <div className="lg-gap" role="note">
      <p className="lg-gap-head">
        <span className="lg-gap-badge">本地服务还没提供</span>
        <strong>{title}</strong>
      </p>
      <p className="lg-gap-summary">{summary}</p>
      <div className="lg-gap-missing">
        <span className="lg-gap-label">缺的接口 / 字段</span>
        <ul>
          {missing.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>
      {note && <p className="lg-gap-note">{note}</p>}
    </div>
  )
}

/**
 * 「看不到」与「没有」是两件事。
 *
 * 后端对越权与不存在常常返回同一个中性 404；页面上如果画成空列表，用户会以为
 * 这里真的没数据。这个块只陈述「没有权限/没有返回」，不猜原因、不显示 0 条。
 */
export function AccessDenied({ what, detail }: { what: string; detail?: string }) {
  return (
    <div className="lg-denied" role="status">
      <strong>无权限查看{what}</strong>
      <span>
        {detail ??
          '本地服务没有返回内容。为了避免把「看不到」误报成「没有」，这里不显示空列表。'}
      </span>
    </div>
  )
}
