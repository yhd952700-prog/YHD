/**
 * 工作台 —— JARVIS 对话台。
 *
 * 这一页复用既有的 `ChatPanel`（真实的流式对话 + 工具调用展示），只做外壳适配：
 * 把面板放进三形态网格里。它没有自己的取数逻辑，因此不涉及 NO-FAKE 边界 ——
 * 对话内容全部来自后端流式响应，工具调用的输入输出原样展示。
 *
 * 模型状态（是否真实模型）由 App 从 `/v1/health` 取一次后传入 —— 不在这一页
 * 自己推导，避免与总览/provider 面板出现第二份答案。
 */

import { ChatPanel } from '../components/ChatPanel'
import { Card } from '../components/ui'
import type { ActiveProviderStatus } from '../lib/providerStatus'

export function Workbench({
  provider,
  onConfigureModel,
}: {
  provider: ActiveProviderStatus
  onConfigureModel: () => void
}) {
  return (
    <div className="os-grid">
      <Card
        title="JARVIS 工作台"
        sub="真实流式对话；工具调用原样展示，不做摘要或美化"
        span={12}
        className="sc-panel-active sc-hud"
      >
        <ChatPanel provider={provider} onConfigureModel={onConfigureModel} />
      </Card>
    </div>
  )
}
