/**
 * Preflight 严重级别的分色与结论判定 —— 抽成**可执行、可单测**的纯模块。
 *
 * 为什么要抽出来（这是一次被工具缺陷逼出来的架构改进）
 * ------------------------------------------------------
 * `severityTone` 最初直接写在 ``pages/Preflight.tsx`` 里，而守卫它的测试是
 * **用正则从源码里抠出函数体**、再断言函数体里含有某个三元表达式。它验证的是
 * "源码长得像不像"，不是"函数行为对不对"，失效方向通常是**假绿**（漏掉真缺陷），
 * 比假红更危险，因为它让人误以为这里有人看着。
 *
 * 下面是一段**构造的反例**（用来演示这类断言的失效模式，**并非某次真实提交的
 * 历史**）：保留那串被断言的三元文本，但**实际**无条件返回 ``'warn'``::
 *
 *     function severityTone(severity: string): 'danger' | 'warn' {
 *       void (severity === 'warning' ? 'warn' : 'danger')  // 诱饵：正则/子串断言会命中
 *       return 'warn'                                       // 真实行为：对所有输入都 fail-open
 *     }
 *
 * 对这个构造体，旧式文本断言实测：``"warning" in body and "danger" in body``
 * 为 **True**、正则 ``===\s*'warning'\s*\?\s*'warn'\s*:\s*'danger'`` 亦命中
 * ⇒ **假绿**，而这行代码对任何输入都返回 ``'warn'``。文本解析类断言的失效
 * 方向正是这种假绿。
 *
 * 所以把纯逻辑搬到这里：``pages/Preflight.tsx`` 只负责渲染，分色与结论在这里。
 * 守卫改为**真正执行**本模块（node --experimental-strip-types + node:test，
 * 零新依赖），断言的是行为而不是文本。
 *
 * 判定原则（fail-closed）
 * ----------------------
 * ``severityTone`` 对**任何未知取值**都落``danger``。理由：把没识别出来的
 * 严重级别显示成安全，等于凭猜测放行一份生产配置。宁可误报严重，不可漏报。
 */

export type PreflightTone = 'danger' | 'warn'

/** 严重级别 → 色调。只有 ``warning`` 降级；其余（含未知值）一律 danger。 */
export function severityTone(severity: string): PreflightTone {
  return severity === 'warning' ? 'warn' : 'danger'
}

/** 预检结论。``ok`` 唯一由``critical_count === 0`` 决定，不由前端自行猜测。 */
export interface PreflightVerdict {
  tone: PreflightTone
  title: string
  detail: string
  /** ``true`` 表示"此配置不应承载生产流量"。 */
  blocked: boolean
}

/**
 * 由真实计数推导结论。
 *
 * ``criticalCount`` 与 ``warningCount`` 必须来自**后端**，不由前端数出来 ——
 * 前端重新数一遍等于产生第二份真相，两份一旦分叉，界面就会显示
 * "结论通过、下面却列着严重问题"这种自相矛盾（``/v1/ready`` 曾踩过同一个坑）。
 */
export function preflightVerdict(
  ok: boolean,
  criticalCount: number,
  warningCount: number,
): PreflightVerdict {
  if (ok) {
    return {
      tone: 'warn',
      title: warningCount > 0 ? `预检通过，但有 ${warningCount} 项警告` : '预检通过',
      detail: warningCount > 0 ? '没有 critical 项；警告不阻断，但需知悉。' : '没有 critical 项。',
      blocked: false,
    }
  }
  return {
    tone: 'danger',
    title: `预检未通过：${criticalCount} 项严重问题`,
    detail: '在此配置下不应承载生产流量。',
    blocked: true,
  }
}