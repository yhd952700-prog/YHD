/**
 * 工作台 —— JARVIS 对话台。
 *
 * 这一页复用既有的 `ChatPanel`（真实的流式对话 + 工具调用展示），只做外壳适配：
 * 把面板放进三形态网格里。它没有自己的取数逻辑，因此不涉及 NO-FAKE 边界 ——
 * 对话内容全部来自后端流式响应，工具调用的输入输出原样展示。
 */

import { ChatPanel } from '../components/ChatPanel'
import { Card } from '../components/ui'

export function Workbench() {
  return (
    <div className="os-grid">
      <Card
        title="JARVIS 工作台"
        sub="真实流式对话；工具调用原样展示，不做摘要或美化"
        span={12}
      >
        <ChatPanel />
      </Card>
    </div>
  )
}
