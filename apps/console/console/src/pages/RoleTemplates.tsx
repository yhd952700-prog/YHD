/**
 * 角色模板（根专属，只读）。
 *
 * 后端目前只暴露固定的三档预设（`_accounts.ROLE_PRESETS`），没有模板编辑端点。
 * 页面如实展示"这是预设、不可编辑"，不画一个保存按钮假装能改（IA C4）。
 */

import type { AccountCaps } from '../lib/accountCaps'
import { loadRoles, useAccountData } from '../lib/accounts'
import { Card, Guard } from '../components/ui'
import { StubBadge } from '../components/AccountChrome'

export function RoleTemplates({ caps }: { caps: AccountCaps }) {
  const roles = useAccountData(() => loadRoles())

  if (!caps.canTemplates) {
    return (
      <Card title="角色模板" sub="当前账号没有该入口" span={12}>
        <div className="os-empty">无权执行该操作</div>
      </Card>
    )
  }

  return (
    <div className="os-grid">
      <Card
        title="角色模板"
        sub="预设模板只读；成员的实际范围以权限矩阵为准。"
        span={12}
        action={<StubBadge source={roles.source} />}
      >
        <Guard
          loading={roles.loading}
          error={roles.error}
          data={roles.data}
          isEmpty={(data) => data.roles.length === 0}
          emptyTitle="没有可用模板"
          onRetry={roles.reload}
        >
          {(data) => (
            <div className="os-table-wrap">
              <table className="os-table">
                <thead>
                  <tr>
                    <th>模板</th>
                    <th>默认范围</th>
                    <th>编辑</th>
                  </tr>
                </thead>
                <tbody>
                  {data.roles.map((role) => (
                    <tr key={role.id}>
                      <td>{role.id}</td>
                      <td>
                        <span className="os-hint">{role.permissions.join(' · ')}</span>
                      </td>
                      <td>
                        <span className="os-hint">待开放（暂不支持模板编辑）</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Guard>
        <p className="os-hint">
          成员范围可逐项调整；模板本身由本机安装版本固定。
        </p>
      </Card>
    </div>
  )
}
