/**
 * 工作台 —— 你自己看任务、看文件、看产出，也可以直接对话。
 *
 * 与「鎏灏管家」的分工写在页面第一句，避免两页互相抄功能：
 *   管家：你下目标，它拆解分派，给结论；
 *   工作台：你自己看、自己翻、直接对话。
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
            <strong>工作台</strong>：你自己看任务、看文件、看产出，也可以直接对话。
            想让管家替你拆解目标，去「鎏灏管家」。
          </span>
          <span className="os-position-links">
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
        sub="真实流式对话；工具调用原样展示，不做摘要或美化"
        span={12}
        className="sc-panel-active sc-hud"
      >
        <ChatPanel provider={provider} onConfigureModel={onConfigureModel} />
      </Card>
    </div>
  )
}
