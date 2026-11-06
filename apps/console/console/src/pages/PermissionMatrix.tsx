/**
 * 权限矩阵（根专属）。
 *
 * 单元格 = 成员 × 可执行范围。勾选变化直接落到 `PATCH /v1/accounts/{p}/permissions`，
 * 服务端负责"不能超过自己已有范围"与审计；前端不做预判，失败原样显示。
 */

import { useState } from 'react'
import type { AccountCaps, AccountProfile } from '../lib/accountCaps'
import { grantPermissions, loadAccounts, loadRoles, useAccountData } from '../lib/accounts'
import { Card, Guard } from '../components/ui'
import { StubBadge } from '../components/AccountChrome'

export function PermissionMatrix({
  profile,
  caps,
  readOnly,
}: {
  profile: AccountProfile
  caps: AccountCaps
  readOnly: boolean
}) {
  const accounts = useAccountData(() =>
    loadAccounts({ principal: profile.principal, isRoot: profile.isRoot }),
  )
  const roles = useAccountData(() => loadRoles())
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)

  if (!caps.canMatrix) {
    return (
      <Card title="权限矩阵" sub="当前账号没有该入口" span={12}>
        <div className="os-empty">无权执行该操作</div>
      </Card>
    )
  }

  const toggle = async (principal: string, scope: string, enabled: boolean) => {
    if (readOnly) {
      setNotice({ tone: 'error', text: '只读查看中，写操作已禁用' })
      return
    }
    setBusyKey(`${principal}:${scope}`)
    setNotice(null)
    try {
      await grantPermissions(principal, enabled ? [scope] : [], enabled ? [] : [scope])
      setNotice({ tone: 'ok', text: `${principal} · ${scope} 已${enabled ? '授予' : '撤销'}` })
      accounts.reload()
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : '权限变更失败',
      })
    } finally {
      setBusyKey(null)
    }
  }

  const source = accounts.source === 'stub' || roles.source === 'stub' ? 'stub' : 'live'
  const scopes = roles.data?.scopes ?? []

  return (
    <div className="os-grid">
      <Card
        title="权限矩阵"
        sub="勾选即生效；每次变更都由服务端记录审计。"
        span={12}
        action={<StubBadge source={source} />}
      >
        {notice && (
          <div className={notice.tone === 'ok' ? 'os-note' : 'os-alert'} role="status">
            {notice.text}
          </div>
        )}
        <Guard
          loading={accounts.loading || roles.loading}
          error={accounts.error ?? roles.error}
          data={
            accounts.data && roles.data
              ? { members: accounts.data.members, scopes }
              : null
          }
          isEmpty={(data) => data.members.length === 0}
          emptyTitle="还没有成员"
          emptyHint="先在成员与权限管理中创建成员。"
          onRetry={() => {
            accounts.reload()
            roles.reload()
          }}
        >
          {(data) => (
            <div className="os-table-wrap">
              <table className="os-table">
                <thead>
                  <tr>
                    <th>成员</th>
                    <th>状态</th>
                    {data.scopes.map((scope) => (
                      <th key={scope}>{scope}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.members.map((member) => {
                    const self = member.principal === profile.principal
                    return (
                      <tr key={member.principal}>
                        <td>{member.principal}</td>
                        <td>{member.status}</td>
                        {data.scopes.map((scope) => {
                          const enabled = member.permissions.includes(scope)
                          const busy = busyKey === `${member.principal}:${scope}`
                          return (
                            <td key={scope}>
                              <input
                                type="checkbox"
                                aria-label={`${member.principal} · ${scope}`}
                                checked={enabled}
                                disabled={readOnly || busy || self}
                                title={self ? '自己的范围不可在这里修改' : undefined}
                                onChange={(event) =>
                                  void toggle(member.principal, scope, event.target.checked)
                                }
                              />
                            </td>
                          )
                        })}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Guard>
      </Card>
    </div>
  )
}
