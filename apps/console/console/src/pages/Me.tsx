/**
 * 「我的」—— 手机端账号、数据与隐私入口的收敛页。
 *
 * 为什么需要这一页
 * ----------------
 * 账号安全、我的数据、恢复码、关于与隐私此前只存在于桌面侧栏的治理分组里，
 * 手机底栏没有入口，只能靠深链打开。把 24 个桌面页整块塞进底栏会挤爆 360px，
 * 也不符合手机端的使用场景；这里只收与**本人**直接相关的入口。
 *
 * 取舍
 * ----
 * * 收进来：账号安全、我的数据、恢复码、我的 Key、关于与隐私、系统设置。
 * * 留在桌面侧栏：成员与权限、权限矩阵、角色模板、备份与迁移，以及系统运维类页面。
 *   这些操作需要多列对照或主机路径，手机端只给一条说明，不摆放了会失败的按钮。
 */

import { Card } from '../components/ui'
import type { AccountCaps } from '../lib/accountCaps'

interface MeProps {
  caps: AccountCaps
  onNavigate: (key: string) => void
}

interface Entry {
  key: string
  title: string
  desc: string
  rootOnly?: boolean
}

const ENTRIES: Entry[] = [
  { key: 'account', title: '账号安全', desc: '修改本人密码；需要当前密码' },
  { key: 'my-data', title: '我的数据', desc: '导出人可读 ZIP；记忆与知识库可回导' },
  {
    key: 'recovery',
    title: '恢复码',
    desc: '忘记密码时重置；轮换后旧码立即失效',
    rootOnly: true,
  },
  { key: 'my-keys', title: '我的 Key', desc: 'Key 归属与用量' },
  { key: 'about', title: '关于与隐私', desc: '版本、数据位置、许可证与联网说明' },
  { key: 'settings', title: '系统设置', desc: '布局、主题、模型与通知', rootOnly: true },
]

export function Me({ caps, onNavigate }: MeProps) {
  const entries = ENTRIES.filter((entry) => !entry.rootOnly || caps.isRoot).map((entry) =>
    entry.key === 'my-keys' && caps.isRoot ? { ...entry, title: '全部 Key' } : entry,
  )

  return (
    <div className="os-grid">
      <Card
        title="我的"
        sub="账号、数据与隐私入口；手机上也能走到，不必记深链。"
        span={12}
      >
        <div className="os-list">
          {entries.map((entry) => (
            <button
              key={entry.key}
              type="button"
              className="os-row os-me-entry"
              onClick={() => onNavigate(entry.key)}
            >
              <div className="os-row-main">
                <div className="os-row-title">{entry.title}</div>
                <div className="os-row-sub">{entry.desc}</div>
              </div>
              <span className="os-badge" data-tone="muted">
                打开
              </span>
            </button>
          ))}
        </div>
        <p className="os-note" style={{ marginTop: 12 }}>
          成员与权限、权限矩阵、角色模板、备份与迁移，以及系统运维类页面仍保留在桌面端侧栏：
          手机端只保留与本人账号、数据和隐私直接相关的入口，避免把桌面侧栏整块塞进底栏。
        </p>
      </Card>
    </div>
  )
}
