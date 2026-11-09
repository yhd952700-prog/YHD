/**
 * 关于与隐私。
 *
 * 首次披露的落地页：把「数据在哪、会不会上网、怎么卸载干净」放在产品里，
 * 而不是只写在安装前的 txt 里。数据来自 GET /v1/about（只读，不含任何密钥）。
 */

import { useState } from 'react'
import { useApi } from '../lib/api'
import { detectClient } from '../lib/mode'
import { Card, Guard } from '../components/ui'

interface AboutPayload {
  app: string
  version: string
  commit: string | null
  build_time: string | null
  app_dir: string
  data_dir: string
  log_dir: string | null
  /** 由后端给出的实时联网状态，界面不自己编造默认值。 */
  network?: {
    web_research: {
      enabled: boolean
      default_enabled: boolean
      hosts: string[]
      disclosure: string
    }
    embedding: {
      cloud_by_default: boolean
      note: string
    }
    telemetry: boolean
    auto_update_check: boolean
  }
  product_license: {
    path: string | null
    exists: boolean
    bytes: number
    text: string
  }
  third_party: {
    notices_path: string
    notices_exists: boolean
    notices_text: string
    licenses_dir: string
    license_files: string[]
    /** 逐份许可证全文（读自本机 licenses\，断网可展开）。 */
    license_entries: { name: string; bytes: number; text: string }[]
  }
  sbom: {
    path: string | null
    exists: boolean
    spec_version: string | null
    component_count: number
    generated_at: string | null
    product_version: string | null
    parse_error: string | null
  }
}

/** `GET /v1/network/egress`：出站登记表的只读投影（与后端同源，页面不写第二份）。 */
interface EgressEntryPayload {
  host: string
  feature: string
  trigger: string
  fields: string
  personal_data: string
  default_state: string
  egress: boolean
}

interface EgressPayload {
  total: number
  egress_count: number
  placeholder_count: number
  groups: { name: string; entries: EgressEntryPayload[] }[]
}

/** 许可证全文的只读展示样式（NOTICES / 单份许可证共用）。 */
const licensePreStyle = {
  maxHeight: 320,
  overflow: 'auto',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-all',
  fontSize: 12,
  lineHeight: 1.6,
  background: 'var(--os-panel-2)',
  padding: 12,
  borderRadius: 8,
  marginTop: 8,
} as const

export function About() {
  const about = useApi<AboutPayload>('/v1/about')
  const egress = useApi<EgressPayload>('/v1/network/egress', 0)
  const [copied, setCopied] = useState(false)
  // 手机端不展示主机的盘符与绝对路径：既用不上，也会把设备目录结构露出来。
  const mobile = detectClient() === 'mobile'

  const copyDataDir = async (path: string) => {
    try {
      await navigator.clipboard.writeText(path)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="os-grid">
      <Card
        title="关于与隐私"
        sub="版本、数据位置、加密边界、联网说明、备份与卸载、许可证与合规材料。"
        span={12}
      >
        <Guard
          loading={about.loading}
          error={about.error}
          data={about.data}
          onRetry={about.reload}
        >
          {(data) => (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              <dl className="os-kv">
                <dt>产品</dt>
                <dd>{data.app}</dd>
                <dt>版本</dt>
                <dd>
                  {data.version}
                  {data.commit ? `（提交 ${data.commit.slice(0, 12)}）` : ''}
                  {data.build_time ? `，构建于 ${data.build_time}` : ''}
                </dd>
                {!mobile && (
                  <>
                    <dt>程序目录</dt>
                    <dd>{data.app_dir}</dd>
                  </>
                )}
                <dt>数据目录</dt>
                <dd>{mobile ? '本机数据目录（桌面端可见）' : data.data_dir}</dd>
                {!mobile && data.log_dir && (
                  <>
                    <dt>日志目录</dt>
                    <dd>{data.log_dir}</dd>
                  </>
                )}
              </dl>

              {!mobile && (
                <button
                  className="os-btn os-btn-ghost"
                  style={{ width: 'auto' }}
                  onClick={() => void copyDataDir(data.data_dir)}
                >
                  {copied ? '已复制数据目录路径' : '复制数据目录路径'}
                </button>
              )}

              <section>
                <h3 className="os-card-title" style={{ fontSize: 15 }}>
                  数据与加密
                </h3>
                <p style={{ color: 'var(--os-text-dim)', lineHeight: 1.8 }}>
                  账号、对话、知识库、记忆、审计记录和日志都保存在上面的数据目录里，
                  不会上传到鎏灏服务器。
                  <br />
                  只有 API Key 用 AES-256-GCM 加密保存（密钥文件 data\model_config.key 与
                  数据库同目录，仅当前 Windows 用户可读）；对话、知识库、审计是
                  <strong>本地明文 SQLite</strong>，不做逐条加密，依赖 Windows 账号权限保护。
                  多人共用或设备可能丢失时，建议开启 BitLocker。
                </p>
              </section>

              <section>
                <h3 className="os-card-title" style={{ fontSize: 15 }}>
                  什么会上网
                </h3>
                <p style={{ color: 'var(--os-text-dim)', lineHeight: 1.8 }}>
                  默认不主动联网。只有部署方配置了云端模型、在部署时显式设置了云端向量化，
                  或在设置里显式开启「联网检索」后，才会发生传输：
                  <br />
                  · 会发到模型服务商：对话内容，以及为回答而检索到的知识库片段（作为上下文）；
                  若部署时显式设置了云端向量化，知识库入库文本也会发送给该服务商。
                  云端向量化没有界面开关：只有部署时通过部署环境变量指定向量化服务商与对应凭据/地址才会生效。
                  环境里存在 OPENAI_API_KEY 不会自动让向量化改走云端。
                  <br />
                  · 联网检索（默认关闭）：只有你显式开启后，才会把提炼后的检索关键词
                  （最多 48 个字符，已去掉邮箱、电话、密钥和链接）发到
                  {data.network ? data.network.web_research.hosts.join('、') : 'hn.algolia.com、api.github.com、export.arxiv.org'}
                  这三个公开接口。只发送提炼后的关键词；不发送你的对话内容、对话历史、个人画像、账号信息或 API Key。
                  {data.network && (
                    <>
                      <br />
                      当前状态：
                      <strong>{data.network.web_research.enabled ? '已开启' : '已关闭'}</strong>
                      （可在「设置 → 隐私与联网」随时切换）。
                    </>
                  )}
                  <br />
                  · 不会发到模型服务商：API Key（只在本机加密保存）、账号密码、审计记录，
                  以及本机的对话/知识数据库文件本身。
                  <br />
                  · 本机 Ollama 模式的模型推理只连 127.0.0.1:11434，不出本机；
                  若显式开启联网检索，检索关键词仍会按上一条发出。
                  <br />
                  不收集使用统计、不做遥测上报；未配置更新源时也不会主动检查更新。
                  <br />
                  运维可选功能默认全部关闭：漏洞情报（api.osv.dev / api.github.com）、
                  升级清单、RFC3161 时间戳、告警 Webhook 都需要显式配置后才会请求。
                  第三方集成与 MCP Server 只有你主动连接后才会访问对应域名。
                </p>
              </section>

              <section>
                <h3 className="os-card-title" style={{ fontSize: 15 }}>
                  它到底发了什么（逐条清单）
                </h3>
                <p style={{ color: 'var(--os-text-dim)', lineHeight: 1.8 }}>
                  下面这一节直接来自后端的出站登记表，不是页面里另写的文案：
                  每一条都写明「功能 / 何时发 / 发什么字段 / 是否含个人信息 / 默认开关」。
                  {egress.data && (
                    <>
                      <br />
                      共登记 {egress.data.total} 个主机：{egress.data.egress_count} 条会真实发起网络请求，
                      {egress.data.placeholder_count} 条只作为链接或占位符出现、从不请求。
                    </>
                  )}
                </p>
                {egress.loading && (
                  <p style={{ color: 'var(--os-text-dim)', lineHeight: 1.8 }}>
                    正在读取出站登记表…
                  </p>
                )}
                {egress.error && (
                  <p style={{ color: 'var(--os-text-dim)', lineHeight: 1.8 }}>
                    逐条清单暂时读不到（{egress.error}）。上面的说明仍然有效，可稍后重试。
                  </p>
                )}
                {egress.data && (
                  <details>
                    <summary style={{ cursor: 'pointer', color: 'var(--os-text-dim)' }}>
                      展开逐条清单（按功能分组，共 {egress.data.egress_count} 条真实出站）
                    </summary>
                    {egress.data.groups.map((group) => (
                      <details key={group.name} style={{ marginTop: 8 }}>
                        <summary style={{ cursor: 'pointer', color: 'var(--os-text-dim)' }}>
                          {group.name}（{group.entries.length} 条）
                        </summary>
                        <div
                          style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 6 }}
                        >
                          {group.entries.map((entry) => (
                            <p
                              key={entry.host}
                              style={{ margin: 0, lineHeight: 1.7, color: 'var(--os-text-dim)' }}
                            >
                              <strong style={{ color: 'var(--os-text)' }}>{entry.host}</strong>
                              ：{entry.feature}；{entry.trigger}；发送 {entry.fields}；个人信息{' '}
                              {entry.personal_data}；默认 {entry.default_state}。
                            </p>
                          ))}
                        </div>
                      </details>
                    ))}
                  </details>
                )}
              </section>

              <section>
                <h3 className="os-card-title" style={{ fontSize: 15 }}>
                  备份与卸载
                </h3>
                <p style={{ color: 'var(--os-text-dim)', lineHeight: 1.8 }}>
                  备份：退出鎏灏后，把整个数据目录复制到移动硬盘或另一台电脑；
                  必须同时复制 data\model_config.key 与 data\model_registry.sqlite3，
                  缺一个已存 API Key 就解不开。
                  <br />
                  卸载：默认保留数据；只有连续两次确认才会永久删除数据目录。
                  卸载入口在 Windows「设置 → 应用 → 已安装的应用」。
                </p>
              </section>

              <section>
                <h3 className="os-card-title" style={{ fontSize: 15 }}>
                  许可证与合规材料
                </h3>
                <p style={{ color: 'var(--os-text-dim)', lineHeight: 1.8 }}>
                  以下为随包材料的查看入口，供核对使用：
                  {data.product_license.exists ? (
                    mobile ? (
                      <span>产品许可证全文随包提供（本机路径在桌面端可见）。</span>
                    ) : (
                      <span style={{ wordBreak: 'break-all' }}>{data.product_license.path}</span>
                    )
                  ) : (
                    '本机未找到 LICENSE 文件，以随包许可证或交付授权为准。'
                  )}
                </p>
                {data.product_license.exists && (
                  <details>
                    <summary style={{ cursor: 'pointer', color: 'var(--os-text-dim)' }}>
                      展开产品许可证全文（{data.product_license.bytes} 字节）
                    </summary>
                    <pre style={licensePreStyle}>{data.product_license.text}</pre>
                  </details>
                )}
                {data.third_party.notices_exists ? (
                  <>
                    <p
                      style={{
                        color: 'var(--os-text-dim)',
                        lineHeight: 1.8,
                        wordBreak: 'break-all',
                      }}
                    >
                      第三方组件声明：
                      {mobile
                        ? '随包提供（本机路径在桌面端可见）'
                        : data.third_party.notices_path}
                    </p>
                    {data.third_party.license_entries.length > 0 && (
                      <>
                        <p style={{ color: 'var(--os-text-dim)', lineHeight: 1.8 }}>
                          {mobile
                            ? '许可证全文随包提供（本机路径在桌面端可见）。'
                            : `许可证全文目录：${data.third_party.licenses_dir}`}
                          <br />
                          共 {data.third_party.license_entries.length} 份，逐份点击展开，
                          内容读自本机文件，断网可用：
                        </p>
                        <div
                          style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
                        >
                          {data.third_party.license_entries.map((entry) => (
                            <details key={entry.name}>
                              <summary
                                style={{ cursor: 'pointer', color: 'var(--os-text-dim)' }}
                              >
                                {entry.name}（{entry.bytes} 字节）
                              </summary>
                              <pre style={licensePreStyle}>{entry.text}</pre>
                            </details>
                          ))}
                        </div>
                      </>
                    )}
                    <details>
                      <summary style={{ cursor: 'pointer', color: 'var(--os-text-dim)' }}>
                        展开第三方组件声明全文
                      </summary>
                      <pre style={licensePreStyle}>{data.third_party.notices_text}</pre>
                    </details>
                  </>
                ) : (
                  <p style={{ color: 'var(--os-text-dim)', lineHeight: 1.8 }}>
                    本机未找到 THIRD-PARTY-NOTICES.txt 与 licenses 目录。如果你用的是
                    0.1.0 旧包，这是已知缺失；可升级到 0.1.1，或向发布方索取第三方组件清单。
                  </p>
                )}
              </section>

              <section>
                <h3 className="os-card-title" style={{ fontSize: 15 }}>
                  软件物料清单（SBOM）
                </h3>
                {data.sbom.exists ? (
                  <>
                    <dl className="os-kv">
                      <dt>组件数</dt>
                      <dd>{data.sbom.component_count} 个</dd>
                      <dt>生成时间</dt>
                      <dd>{data.sbom.generated_at ?? '文件未记录生成时间'}</dd>
                      <dt>规范版本</dt>
                      <dd>CycloneDX {data.sbom.spec_version ?? '未知'}</dd>
                      <dt>文件</dt>
                      <dd style={{ wordBreak: 'break-all' }}>
                        {mobile ? '随包 SBOM 文件（本机路径在桌面端可见）' : data.sbom.path}
                      </dd>
                    </dl>
                    {data.sbom.parse_error && (
                      <p style={{ color: 'var(--os-text-dim)', lineHeight: 1.8 }}>
                        摘要解析失败（文件存在但内容不可读）：{data.sbom.parse_error}
                      </p>
                    )}
                  </>
                ) : (
                  <p style={{ color: 'var(--os-text-dim)', lineHeight: 1.8 }}>
                    本机未找到软件物料清单（SBOM）文件。0.1.1 起的安装包会把它放在程序目录，
                    可向发布方索取。
                  </p>
                )}
                <p style={{ color: 'var(--os-text-dim)', lineHeight: 1.8 }}>
                  以上内容全部读自本机文件，不发起任何网络请求。
                </p>
              </section>
            </div>
          )}
        </Guard>
      </Card>
    </div>
  )
}
