# 前端 DOM 契约（wb 世代 dist ↔ 主题叠加层）

生成口径：第二季 V4（2026-11-18）从 `console/theme/` 四文件程序化提取全部 `.wb-*` 选择器，
逐个与发布 dist（`console/index.html` + `console/assets/` 全文）对账。自动复验：
`python scripts/check_frontend_guardrails.py dom`（组级判定：逗号备选组主选命中即成立）。

**契约语义**：主题叠加层（经典 + 未来双主题的全部界面行为）靠这些类名钩住 dist 的 DOM。
A3 重建可维护源码时，新 DOM 必须继续提供下表「在 dist」为「是」的全部类名且语义不变，
或同步改叠加层并在本文件登记。表外 3 个「备选」选择器是给旧标记变体的保险（同组主选在 dist 内），
重建时可保留可删除，删除时在本文件销账。

共 71 个选择器：在 dist 内 68，备选 3。

| 选择器 | 在 dist | JS 查询钩子 | 出现文件 |
|---|---|---|---|
| `.wb-active` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-bot` | 是 | 是 | jarvis-theme.js |
| `.wb-bots` | 是 | 是 | jarvis-theme.css, jarvis-theme.js |
| `.wb-chat` | 是 | 是 | jarvis-future-theme.css, jarvis-future-theme.js, jarvis-theme.css, jarvis-theme.js |
| `.wb-chat-head` | 是 | — | jarvis-theme.css |
| `.wb-chat-list` | 是 | 是 | jarvis-future-theme.css, jarvis-future-theme.js, jarvis-theme.css |
| `.wb-chat-row` | 备选 | — | jarvis-theme.css |
| `.wb-chat-status` | 是 | — | jarvis-theme.css |
| `.wb-chat-title` | 是 | — | jarvis-theme.css |
| `.wb-composer` | 是 | 是 | jarvis-future-theme.css, jarvis-future-theme.js, jarvis-theme.css, jarvis-theme.js |
| `.wb-composer-foot` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-composer-hint` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-composer-row` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-context-menu` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-dialog` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-dot` | 是 | — | jarvis-theme.css |
| `.wb-editing` | 是 | — | jarvis-theme.css |
| `.wb-empty` | 是 | — | jarvis-theme.css |
| `.wb-empty-chat` | 是 | — | jarvis-theme.css |
| `.wb-first-open` | 是 | 是 | jarvis-future-theme.css, jarvis-future-theme.js, jarvis-theme.css, jarvis-theme.js |
| `.wb-grid-btn` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-groups-chat` | 是 | — | jarvis-theme.css |
| `.wb-help-btn` | 是 | — | jarvis-theme.css |
| `.wb-icon-btn` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-inbox` | 是 | — | jarvis-theme.css |
| `.wb-inbox-item` | 是 | 是 | jarvis-future-theme.css, jarvis-future-theme.js, jarvis-theme.css |
| `.wb-intro` | 是 | 是 | jarvis-future-theme.css, jarvis-future-theme.js, jarvis-theme.css, jarvis-theme.js |
| `.wb-jarvis` | 是 | — | jarvis-theme.css |
| `.wb-jarvis-btn` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-jarvis-name` | 是 | — | jarvis-theme.css |
| `.wb-jarvis-state` | 是 | — | jarvis-theme.css |
| `.wb-main` | 是 | 是 | jarvis-future-theme.css, jarvis-future-theme.js, jarvis-theme.css |
| `.wb-mention-picker` | 是 | — | jarvis-theme.css |
| `.wb-message` | 备选 | — | jarvis-theme.css |
| `.wb-msg` | 是 | 是 | jarvis-theme.css, jarvis-theme.js |
| `.wb-msg-actions` | 是 | — | jarvis-theme.css |
| `.wb-msg-author` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-msg-body` | 是 | 是 | jarvis-future-theme.css, jarvis-theme.css, jarvis-theme.js |
| `.wb-msg-bot` | 是 | 是 | jarvis-future-theme.js |
| `.wb-msg-content` | 备选 | 是 | jarvis-theme.js |
| `.wb-msg-notice` | 是 | — | jarvis-theme.css |
| `.wb-msg-text` | 是 | 是 | jarvis-theme.js |
| `.wb-msg-time` | 是 | — | jarvis-theme.css |
| `.wb-msg-user` | 是 | 是 | jarvis-future-theme.css, jarvis-theme.css, jarvis-theme.js |
| `.wb-notice` | 是 | — | jarvis-theme.css |
| `.wb-plus-btn` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-popover` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-primary-btn` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-quiet-btn` | 是 | — | jarvis-theme.css |
| `.wb-rail` | 是 | 是 | jarvis-future-theme.css, jarvis-theme.css, jarvis-theme.js |
| `.wb-rail-bottom` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-rail-scroll` | 是 | 是 | jarvis-theme.css, jarvis-theme.js |
| `.wb-rail-top` | 是 | — | jarvis-theme.css |
| `.wb-replying` | 是 | — | jarvis-theme.css |
| `.wb-result-card` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-resume-row` | 是 | — | jarvis-theme.css |
| `.wb-right` | 是 | 是 | jarvis-future-theme.css, jarvis-future-theme.js, jarvis-theme.css, jarvis-theme.js |
| `.wb-right-body` | 是 | 是 | jarvis-future-theme.css, jarvis-future-theme.js |
| `.wb-right-close` | 是 | — | jarvis-future-theme.css |
| `.wb-routines` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-search-panel` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-send-btn` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-shell` | 是 | 是 | jarvis-future-theme.css, jarvis-future-theme.js, jarvis-theme.css, jarvis-theme.js |
| `.wb-space-detail` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-spaces` | 是 | — | jarvis-theme.css |
| `.wb-spaces-item` | 是 | — | jarvis-future-theme.css, jarvis-theme.css |
| `.wb-thinking` | 是 | 是 | jarvis-future-theme.css, jarvis-future-theme.js, jarvis-theme.css |
| `.wb-tries` | 是 | 是 | jarvis-future-theme.css, jarvis-future-theme.js, jarvis-theme.css, jarvis-theme.js |
| `.wb-voice-btn` | 是 | 是 | jarvis-future-theme.css, jarvis-future-theme.js, jarvis-theme.css |
| `.wb-voice-confirm` | 是 | 是 | jarvis-future-theme.js, jarvis-theme.css |
| `.wb-workspace-btn` | 是 | — | jarvis-theme.css |

## 结构锚点（语义说明）

- `.wb-shell`：wb 世代外壳根节点（正典断言标记，位于 Workbench 懒加载 chunk）。
- `.wb-rail` / `.wb-boss-card` / `.wb-boss-panel`：经典主题左栏与 Boss 枢纽（V1.5 角标与列表重构钩子）。
- `.wb-chat` / `.wb-msg` / `.wb-composer`：聊天列、消息行、输入区（W2/W5 与手机端单行化钩子）。
- `.wb-right`：未来主题右屏（数据/我的空间；手机端空间抽屉钩子）。
- `.wb-voice-btn`：真实控制台语音钮（未来主题中央语音钮向它转发指针事件）。
- `.wb-first-open` / `.wb-quiet-btn`：首次打开欢迎卡与「知道了」幽灵钮（W3 钩子）。
- `.wb-thinking`：思考中标记（未来主题进度弧的 MutationObserver 近似信号源）。
