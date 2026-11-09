/**
 * 模型写入口的判定 —— 与后端 `require_model_manager` 同一口径。
 *
 * 后端接受三类令牌：管理员角色、L0 人类主权、账号节点上的 `model:manage`
 * （成员默认持有）。这里只决定**渲染哪些入口**，授权仍由后端裁决；两侧
 * 口径不一致会变成「界面有入口、点了 403」或「后端允许、入口被藏起来」。
 *
 * 不挂在 `ModelManager.tsx` 里导出：组件模块额外导出函数会触发
 * `react-refresh/only-export-components`。
 */
import type { Session } from './auth'

export function canManageModels(session: Session): boolean {
  const permissions = session.permissions
  return (
    permissions.includes('admin') ||
    session.scope === 'L0' ||
    permissions.includes('model:manage')
  )
}
