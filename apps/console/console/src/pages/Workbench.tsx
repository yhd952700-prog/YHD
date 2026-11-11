/**
 * 自由对话 —— 你自己问、自己翻，不经过管家拆解。
 *
 * 与「贾维斯」的分工写在页面第一句，避免两页互相抄功能：
 *   管家：你下目标，它拆解分派，给结论；
 *   自由对话：你自己问、自己翻，直接对话。
 *
 * 对话内容全部来自后端流式响应，工具调用的输入输出原样展示。
 * 模型状态（是否真实模型）由 App 从 `/v1/health` 取一次后传入 —— 不在这一页
 * 自己推导，避免出现第二份答案。
 */

import { ChatPanel } from '../components/ChatPanel'
import { Card } from '../components/ui'
import type { ActiveProviderStatus } from '../lib/providerStatus'

export function Workbench({
  provider,
  onConfigureModel,
  onNavigate,
}: {
  provider: ActiveProviderStatus
  onConfigureModel: () => void
  onNavigate: (key: string) => void
}) {
  return (
    <div className="os-grid">
      <div className="os-span-12">
        <div className="os-position" role="note">
          <span>
            <strong>自由对话</strong>：你自己问、自己翻，不经过管家拆解；任务、文件与产出在右边。
            想让管家替你拆解目标，去「贾维斯」。
          </span>
          <span className="os-position-links">
            <button
              className="os-btn os-btn-primary"
              style={{ width: 'auto' }}
              onClick={() => {
                const wrap = document.querySelector('.jarvis-input-wrap')
                wrap?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                wrap?.querySelector('textarea')?.focus()
              }}
            >
              开始对话
            </button>
            <button className="os-link" onClick={() => onNavigate('goals')}>
              看任务
            </button>
            <button className="os-link" onClick={() => onNavigate('files')}>
              看文件与产出
            </button>
            <button className="os-link" onClick={() => onNavigate('data')}>
              看运行记录
            </button>
          </span>
        </div>
      </div>

      <Card
        title="直接对话"
        sub="在下面输入框里提问，回车发送；工具调用原样展示，不做摘要或美化"
        span={12}
        className="sc-panel-active sc-hud"
      >
        <ChatPanel
          provider={provider}
          onConfigureModel={onConfigureModel}
          onOpenUsage={() => onNavigate('my-keys')}
        />
      </Card>
    </div>
  )
}
