/**
 * 聊天操作台 —— 登录后的默认落点。
 *
 * 不是一堆状态卡片的仪表盘，也不是演示版：中间是与唯一助手（贾维斯，
 * Muse 能力 + 小龙虾风格的结合版，没有可切换的人格）的对话区，底部
 * 是输入框，输入框上方是工具箱；左边导航保留原来的全部入口。
 *
 * 这一页本身不做任何对话逻辑，只复用 `ChatPanel`：聊天走
 * `POST /v1/chat/stream`（见 `lib/chatClient.streamChat`），历史走
 * `GET /v1/chat/history`，模型状态只读 `/v1/health` 的 `ai_provider`。
 * 未配置模型时 `ChatPanel` 会明确提示"未配置模型，请先配置模型密钥"，
 * 不会生成模拟回答。
 */

import { ChatPanel } from '../components/ChatPanel'
import type { ActiveProviderStatus } from '../lib/providerStatus'

export const OPERATOR_CONSOLE_PAGE = {
  key: 'console',
  title: '操作台',
  sub: '跟贾维斯直接对话：聊天为主，工具箱在输入框上方',
} as const

export function OperatorConsole({
  provider,
  onConfigureModel,
  onOpenUsage,
}: {
  /** 模型状态唯一来源（/v1/health → lib/providerStatus）。 */
  provider: ActiveProviderStatus
  /** "去配置模型"：跳到模型管理。 */
  onConfigureModel: () => void
  /** "去用量页调额度"：402 预算拒绝时的可点出口。 */
  onOpenUsage?: () => void
}) {
  return (
    <div className="os-grid">
      <div className="os-span-12">
        <ChatPanel
          provider={provider}
          onConfigureModel={onConfigureModel}
          onOpenUsage={onOpenUsage}
        />
      </div>
    </div>
  )
}
