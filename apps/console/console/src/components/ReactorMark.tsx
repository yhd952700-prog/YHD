/**
 * 方舟反应堆标识（原创 SVG，无字母）。
 *
 * 造型：外环带 10 个节点 + 线圈虚线环 + 内三角 + 发光核心，钢蓝色。
 * 它是贾维斯的标识，不是字母徽标：任何地方都不要再出现字母 J。
 *
 * 转速沿用已有的状态约定（`data-state`，与 `.sc-status` 同一套取值）：
 * 平时 `idle` 慢转（24s 一圈），干活时 `active` 转快（3s 一圈）且更亮。
 * 调用方把自己已有的 working/sending 状态传进来即可，不要另造状态机。
 * 尊重 `prefers-reduced-motion`：那时不转（见 os.css）。
 */

const NODES = Array.from({ length: 10 }, (_, k) => {
  const angle = (k * 36 * Math.PI) / 180
  return {
    cx: 32 + 28 * Math.cos(angle),
    cy: 32 + 28 * Math.sin(angle),
  }
})

export function ReactorMark({
  size = 24,
  working = false,
  label = '方舟反应堆',
}: {
  size?: number
  working?: boolean
  label?: string
}) {
  return (
    <span
      className="os-reactor"
      data-state={working ? 'active' : 'idle'}
      role="img"
      aria-label={label}
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden="true">
        {/* 转子：外环 + 节点 + 线圈虚线环，一起转；三角与核心不转。 */}
        <g className="os-reactor-rotor">
          <circle cx="32" cy="32" r="28" stroke="#5b9bd5" strokeWidth="2.5" />
          {NODES.map((node) => (
            <circle key={`${node.cx}-${node.cy}`} cx={node.cx} cy={node.cy} r="2.2" fill="#5b9bd5" />
          ))}
          <circle
            cx="32"
            cy="32"
            r="22"
            stroke="#5b9bd5"
            strokeWidth="1.6"
            strokeDasharray="3 4"
          />
        </g>
        {/* 内三角 */}
        <path
          d="M32 20 L42 40 L22 40 Z"
          stroke="#8fbde8"
          strokeWidth="2"
          strokeLinejoin="round"
        />
        {/* 发光核心：两层实心圆，不用渐变。 */}
        <circle cx="32" cy="32" r="7.5" fill="#bcd9f2" />
        <circle cx="32" cy="32" r="4" fill="#eaf4fc" />
      </svg>
    </span>
  )
}
