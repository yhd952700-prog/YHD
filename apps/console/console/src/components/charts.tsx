/**
 * 图表组件 —— 纯 SVG，无第三方图表库。
 *
 * 为什么手写：驾驶舱要作为 PWA 装到手机上，图表库通常几百 KB；而这四个图形的
 * 需求都很窄（折线、环形、网络、同心圆）。手写换来的是完全可控的配色（跟随主题
 * 变量）、零依赖，以及"数据为空时能诚实地画出一个空态"而不是一条从 0 到 0 的
 * 假曲线。
 *
 * 所有组件都不做数据平滑或补齐：给什么画什么。
 */

import { useId } from 'react'
import { colorAt, formatCompact } from '../lib/chartUtils'

// ---------------------------------------------------------------------------
// 折线图
// ---------------------------------------------------------------------------

export interface LineSeries {
  key: string
  label: string
  values: number[]
  color?: string
}

export interface LineChartProps {
  labels: string[]
  series: LineSeries[]
  height?: number
  /** y 轴取值上限；不传则按数据最大值自动取整 */
  max?: number
  emptyHint?: string
}

export function LineChart({
  labels,
  series,
  height = 168,
  max,
  emptyHint = '暂无数据',
}: LineChartProps) {
  const gradientPrefix = useId().replace(/:/g, '')
  const count = labels.length

  if (count === 0 || series.length === 0) {
    return <EmptyChart height={height} hint={emptyHint} />
  }

  const width = 640
  const padLeft = 34
  const padRight = 10
  const padTop = 12
  const padBottom = 24
  const plotW = width - padLeft - padRight
  const plotH = height - padTop - padBottom

  const observed = Math.max(
    1,
    ...series.flatMap((item) => item.values.map((value) => Number(value) || 0)),
  )
  const top = max ?? niceCeil(observed)

  const xAt = (index: number) =>
    padLeft + (count === 1 ? plotW / 2 : (plotW * index) / (count - 1))
  const yAt = (value: number) => padTop + plotH - (Math.max(0, value) / top) * plotH

  const ticks = [0, 0.25, 0.5, 0.75, 1]
  // x 轴最多标 6 个日期，避免手机上挤成一团。
  const labelStep = Math.max(1, Math.ceil(count / 6))

  return (
    <svg
      className="os-chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`折线图：${series.map((s) => s.label).join('、')}`}
    >
      {ticks.map((ratio) => {
        const value = top * (1 - ratio)
        const y = padTop + plotH * ratio
        return (
          <g key={ratio}>
            <line
              className="os-chart-grid"
              x1={padLeft}
              x2={width - padRight}
              y1={y}
              y2={y}
            />
            <text className="os-chart-axis" x={padLeft - 6} y={y + 3} textAnchor="end">
              {formatCompact(value)}
            </text>
          </g>
        )
      })}

      {series.map((item) => {
        const color = item.color ?? colorAt(series.indexOf(item))
        const points = item.values.map((value, index) => ({
          x: xAt(index),
          y: yAt(Number(value) || 0),
        }))
        if (points.length === 0) return null
        const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x} ${p.y}`).join(' ')
        const area = `${line} L${points[points.length - 1].x} ${padTop + plotH} L${points[0].x} ${
          padTop + plotH
        } Z`
        return (
          <g key={item.key}>
            <defs>
              <linearGradient
                id={`${gradientPrefix}-${item.key}`}
                x1="0"
                y1="0"
                x2="0"
                y2="1"
              >
                <stop offset="0%" stopColor={color} stopOpacity="0.28" />
                <stop offset="100%" stopColor={color} stopOpacity="0" />
              </linearGradient>
            </defs>
            <path d={area} fill={`url(#${gradientPrefix}-${item.key})`} stroke="none" />
            <path
              d={line}
              fill="none"
              stroke={color}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            {points.map((point, index) => (
              <circle
                key={index}
                cx={point.x}
                cy={point.y}
                r={2.6}
                fill={color}
                stroke="var(--os-panel)"
                strokeWidth={1.2}
              />
            ))}
          </g>
        )
      })}

      {labels.map((label, index) =>
        index % labelStep === 0 || index === count - 1 ? (
          <text
            key={label + index}
            className="os-chart-axis"
            x={xAt(index)}
            y={height - 7}
            textAnchor="middle"
          >
            {label}
          </text>
        ) : null,
      )}
    </svg>
  )
}

// ---------------------------------------------------------------------------
// 环形图
// ---------------------------------------------------------------------------

export interface DonutSlice {
  label: string
  value: number
  color?: string
}

export interface DonutChartProps {
  slices: DonutSlice[]
  size?: number
  thickness?: number
  centerLabel?: string
  centerValue?: string
}

export function DonutChart({
  slices,
  size = 168,
  thickness = 20,
  centerLabel,
  centerValue,
}: DonutChartProps) {
  const usable = slices.filter((slice) => Number(slice.value) > 0)
  const total = usable.reduce((sum, slice) => sum + Number(slice.value), 0)

  if (total <= 0) {
    return <EmptyChart height={size} hint="暂无事件" />
  }

  const radius = (size - thickness) / 2
  const circumference = 2 * Math.PI * radius

  // 先把每段的弧长与起始偏移算完，再交给渲染 —— 在渲染过程中累加一个可变变量是不纯
  // 的（React 的纯度检查会拦，并发渲染下结果也不可预测）。用普通 for 而非 map/forEach，
  // 因为后者会把累加放进回调里，性质一样。
  const segments: { slice: DonutSlice; dash: string; offset: number; color: string }[] = []
  let cursor = 0
  for (let index = 0; index < usable.length; index += 1) {
    const slice = usable[index]
    const length = (Number(slice.value) / total) * circumference
    segments.push({
      slice,
      dash: `${length} ${circumference - length}`,
      offset: cursor,
      color: slice.color ?? colorAt(index),
    })
    cursor += length
  }

  return (
    <div style={{ display: 'flex', justifyContent: 'center' }}>
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={`环形图：共 ${total}`}
      >
        <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="var(--os-panel-2)"
            strokeWidth={thickness}
          />
          {segments.map((segment) => (
            <circle
              key={segment.slice.label}
              cx={size / 2}
              cy={size / 2}
              r={radius}
              fill="none"
              stroke={segment.color}
              strokeWidth={thickness}
              strokeDasharray={segment.dash}
              strokeDashoffset={-segment.offset}
              strokeLinecap="butt"
            />
          ))}
        </g>
        <text
          x={size / 2}
          y={size / 2 - 2}
          textAnchor="middle"
          fill="var(--os-text)"
          fontSize="21"
          fontWeight="660"
        >
          {centerValue ?? formatCompact(total)}
        </text>
        <text
          x={size / 2}
          y={size / 2 + 15}
          textAnchor="middle"
          fill="var(--os-text-faint)"
          fontSize="10.5"
        >
          {centerLabel ?? '合计'}
        </text>
      </svg>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 协作网络图
// ---------------------------------------------------------------------------

export interface NetworkNode {
  id: string
  label: string
  group: string
  groupLabel?: string
}

export interface NetworkGraphProps {
  nodes: NetworkNode[]
  hubLabel?: string
  height?: number
}

/**
 * 中心枢纽 + 按分组同心环布局。
 *
 * 布局完全由索引决定（黄金角分布），**不用随机数** —— 否则每次重渲染节点都会
 * 跳动，看起来像数据在变，实际上只是布局在抖。
 */
export function NetworkGraph({ nodes, hubLabel = '鎏灏 OS', height = 232 }: NetworkGraphProps) {
  const width = 340
  if (nodes.length === 0) {
    return <EmptyChart height={height} hint="暂无注册项" />
  }

  const cx = width / 2
  const cy = height / 2
  const groups = Array.from(new Set(nodes.map((node) => node.group)))
  const ringByGroup = new Map(groups.map((group, index) => [group, index]))

  const inner = 52
  const step = groups.length > 1 ? Math.min(26, (Math.min(width, height) / 2 - inner - 14) / (groups.length - 1)) : 0

  const placed = nodes.map((node, index) => {
    const ring = ringByGroup.get(node.group) ?? 0
    const peers = nodes.filter((item) => item.group === node.group)
    const positionInRing = peers.findIndex((item) => item.id === node.id)
    const angle = (2 * Math.PI * positionInRing) / Math.max(1, peers.length) + ring * 0.4
    const radius = inner + ring * step
    return {
      ...node,
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
      color: colorAt(index),
      radius,
    }
  })

  return (
    <svg
      className="os-chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`协作网络图：${nodes.length} 个节点`}
    >
      {groups.map((group, index) => (
        <circle
          key={group}
          cx={cx}
          cy={cy}
          r={inner + index * step}
          fill="none"
          stroke="var(--os-grid-line)"
          strokeDasharray="3 5"
        />
      ))}

      {placed.map((node) => (
        <line
          key={`link-${node.id}`}
          x1={cx}
          y1={cy}
          x2={node.x}
          y2={node.y}
          stroke="var(--os-grid-line)"
          strokeWidth={1}
        />
      ))}

      <circle cx={cx} cy={cy} r={19} fill="var(--os-accent-soft)" />
      <circle cx={cx} cy={cy} r={12} fill="var(--os-accent)" />
      <text
        x={cx}
        y={cy + 30}
        textAnchor="middle"
        fill="var(--os-text-dim)"
        fontSize="9.5"
      >
        {hubLabel}
      </text>

      {placed.map((node) => (
        <g key={node.id}>
          <circle cx={node.x} cy={node.y} r={5.4} fill={node.color} />
          <circle
            cx={node.x}
            cy={node.y}
            r={9.5}
            fill="none"
            stroke={node.color}
            strokeOpacity={0.32}
          />
        </g>
      ))}
    </svg>
  )
}

// ---------------------------------------------------------------------------
// 大脑地图（14 内核同心环）
// ---------------------------------------------------------------------------

export interface BrainRegion {
  id: string
  label: string
  group: string
}

export function BrainMap({ regions, height = 232 }: { regions: BrainRegion[]; height?: number }) {
  const width = 340
  if (regions.length === 0) {
    return <EmptyChart height={height} hint="暂无内核注册" />
  }

  const cx = width / 2
  const cy = height / 2
  const groups = Array.from(new Set(regions.map((region) => region.group)))

  const dots = regions.map((region, index) => {
    const ring = groups.indexOf(region.group)
    const peers = regions.filter((item) => item.group === region.group)
    const positionInRing = peers.findIndex((item) => item.id === region.id)
    const angle =
      (2 * Math.PI * positionInRing) / Math.max(1, peers.length) + ring * 0.55 - Math.PI / 2
    const radius = 40 + ring * 21
    return {
      ...region,
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
      color: colorAt(index),
    }
  })

  return (
    <div className="os-brain">
      <svg
        className="os-chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`大脑地图：${regions.length} 个内核`}
      >
        <defs>
          <radialGradient id="os-brain-core">
            <stop offset="0%" stopColor="var(--os-accent)" stopOpacity="0.5" />
            <stop offset="100%" stopColor="var(--os-accent)" stopOpacity="0" />
          </radialGradient>
        </defs>
        <circle cx={cx} cy={cy} r={58} fill="url(#os-brain-core)" />
        {groups.map((group, index) => (
          <circle
            key={group}
            cx={cx}
            cy={cy}
            r={40 + index * 21}
            fill="none"
            stroke="var(--os-grid-line)"
          />
        ))}
        <circle cx={cx} cy={cy} r={7} fill="var(--os-accent)" />

        {dots.map((dot) => (
          <g key={dot.id}>
            <circle cx={dot.x} cy={dot.y} r={3.6} fill={dot.color} />
            <circle
              cx={dot.x}
              cy={dot.y}
              r={7}
              fill="none"
              stroke={dot.color}
              strokeOpacity={0.3}
            />
          </g>
        ))}

        {/* 脉络：中心辐射，暗示"一个系统"而不是 14 个孤立模块 */}
        {dots.map((dot) => (
          <line
            key={`trace-${dot.id}`}
            x1={cx}
            y1={cy}
            x2={dot.x}
            y2={dot.y}
            stroke={dot.color}
            strokeOpacity={0.14}
            strokeWidth={1}
          />
        ))}
      </svg>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------

function EmptyChart({ height, hint }: { height: number; hint: string }) {
  return (
    <div className="os-empty" style={{ minHeight: height }}>
      <strong>{hint}</strong>
      <span>该区间没有真实数据，因此不画曲线</span>
    </div>
  )
}

/** 把数值取到一个"好看"的刻度上限（1/2/5 × 10^n）。 */
function niceCeil(value: number): number {
  if (value <= 5) return 5
  const magnitude = Math.pow(10, Math.floor(Math.log10(value)))
  const normalized = value / magnitude
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10
  return step * magnitude
}
