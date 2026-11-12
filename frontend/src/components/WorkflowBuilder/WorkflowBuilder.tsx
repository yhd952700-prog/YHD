import React, { memo, useCallback, useMemo, useRef, useState } from 'react';
import type { WorkflowEdge, WorkflowNode, WorkflowNodeType } from '../../types';
import { cn, genId } from '../../utils';

export interface WorkflowBuilderProps {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  /** 节点位置/内容变化回调 */
  onNodesChange?: (nodes: WorkflowNode[]) => void;
  onEdgesChange?: (edges: WorkflowEdge[]) => void;
  onNodeSelect?: (node: WorkflowNode | null) => void;
  /** 只读模式（禁止拖拽与编辑） */
  readOnly?: boolean;
  /** 画布高度 */
  height?: number;
  /** 显示小地图图例 */
  showLegend?: boolean;
  className?: string;
}

export const NODE_TYPE_META: Record<WorkflowNodeType, { label: string; color: string; ring: string }> = {
  trigger: { label: '触发器', color: 'bg-emerald-500', ring: 'ring-emerald-500/40' },
  agent: { label: 'Agent', color: 'bg-brand-500', ring: 'ring-brand-500/40' },
  condition: { label: '条件', color: 'bg-amber-500', ring: 'ring-amber-500/40' },
  transform: { label: '转换', color: 'bg-cyan-500', ring: 'ring-cyan-500/40' },
  tool: { label: '工具', color: 'bg-violet-500', ring: 'ring-violet-500/40' },
  output: { label: '输出', color: 'bg-rose-500', ring: 'ring-rose-500/40' },
};

const NODE_W = 150;
const NODE_H = 44;

interface DragState {
  nodeId: string;
  offsetX: number;
  offsetY: number;
}

interface PendingEdge {
  sourceId: string;
  x: number;
  y: number;
}

/** WorkflowBuilder - 轻量节点画布：节点拖拽、连线创建与选择。 */
export const WorkflowBuilder = memo<WorkflowBuilderProps>(function WorkflowBuilder({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onNodeSelect,
  readOnly = false,
  height = 480,
  showLegend = true,
  className,
}) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const [dragState, setDragState] = useState<DragState | null>(null);
  const [pendingEdge, setPendingEdge] = useState<PendingEdge | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const nodeMap = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);

  const canvasPoint = useCallback((clientX: number, clientY: number) => {
    const rect = canvasRef.current?.getBoundingClientRect();
    return {
      x: clientX - (rect?.left ?? 0),
      y: clientY - (rect?.top ?? 0),
    };
  }, []);

  const updateNodePosition = useCallback(
    (nodeId: string, x: number, y: number) => {
      if (!onNodesChange) return;
      onNodesChange(
        nodes.map((n) => (n.id === nodeId ? { ...n, position: { x, y } } : n)),
      );
    },
    [nodes, onNodesChange],
  );

  const handleNodeMouseDown = (e: React.MouseEvent, node: WorkflowNode) => {
    if (readOnly) return;
    e.stopPropagation();
    const point = canvasPoint(e.clientX, e.clientY);
    setDragState({
      nodeId: node.id,
      offsetX: point.x - node.position.x,
      offsetY: point.y - node.position.y,
    });
    setSelectedNodeId(node.id);
    onNodeSelect?.(node);
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    const point = canvasPoint(e.clientX, e.clientY);
    if (dragState) {
      updateNodePosition(
        dragState.nodeId,
        Math.max(0, point.x - dragState.offsetX),
        Math.max(0, point.y - dragState.offsetY),
      );
    } else if (pendingEdge) {
      setPendingEdge({ ...pendingEdge, x: point.x, y: point.y });
    }
  };

  const handleMouseUp = () => {
    setDragState(null);
    setPendingEdge(null);
  };

  const startEdge = (e: React.MouseEvent, node: WorkflowNode) => {
    if (readOnly) return;
    e.stopPropagation();
    const point = canvasPoint(e.clientX, e.clientY);
    setPendingEdge({ sourceId: node.id, x: point.x, y: point.y });
  };

  const completeEdge = (e: React.MouseEvent, target: WorkflowNode) => {
    if (!pendingEdge || pendingEdge.sourceId === target.id) return;
    e.stopPropagation();
    const exists = edges.some(
      (edge) => edge.source === pendingEdge.sourceId && edge.target === target.id,
    );
    if (!exists) {
      onEdgesChange?.([
        ...edges,
        { id: genId('edge'), source: pendingEdge.sourceId, target: target.id },
      ]);
    }
    setPendingEdge(null);
  };

  const removeEdge = (edgeId: string) => {
    if (readOnly) return;
    onEdgesChange?.(edges.filter((e) => e.id !== edgeId));
  };

  const removeNode = (nodeId: string) => {
    if (readOnly) return;
    onNodesChange?.(nodes.filter((n) => n.id !== nodeId));
    onEdgesChange?.(edges.filter((e) => e.source !== nodeId && e.target !== nodeId));
    if (selectedNodeId === nodeId) {
      setSelectedNodeId(null);
      onNodeSelect?.(null);
    }
  };

  const edgePath = (source: WorkflowNode, target: WorkflowNode): string => {
    const x1 = source.position.x + NODE_W;
    const y1 = source.position.y + NODE_H / 2;
    const x2 = target.position.x;
    const y2 = target.position.y + NODE_H / 2;
    const dx = Math.max(40, Math.abs(x2 - x1) / 2);
    return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
  };

  return (
    <div className={cn('flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900', className)}>
      {showLegend && (
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-3 py-2 dark:border-slate-800">
          {(Object.keys(NODE_TYPE_META) as WorkflowNodeType[]).map((type) => (
            <span key={type} className="inline-flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
              <span className={cn('h-2 w-2 rounded-full', NODE_TYPE_META[type].color)} />
              {NODE_TYPE_META[type].label}
            </span>
          ))}
          {!readOnly && (
            <span className="ml-auto text-[11px] text-slate-400">
              拖拽移动节点 · 从节点右侧圆点拉线连接 · 双击删除
            </span>
          )}
        </div>
      )}

      <div
        ref={canvasRef}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onClick={() => {
          setSelectedNodeId(null);
          onNodeSelect?.(null);
        }}
        className="relative w-full select-none overflow-hidden bg-[radial-gradient(circle,_theme(colors.slate.200)_1px,_transparent_1px)] [background-size:20px_20px] dark:bg-[radial-gradient(circle,_theme(colors.slate.800)_1px,_transparent_1px)]"
        style={{ height }}
      >
        {/* 连线层 */}
        <svg className="pointer-events-none absolute inset-0 h-full w-full">
          <defs>
            <marker id="lh-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="#94a3b8" />
            </marker>
          </defs>
          {edges.map((edge) => {
            const source = nodeMap.get(edge.source);
            const target = nodeMap.get(edge.target);
            if (!source || !target) return null;
            return (
              <g key={edge.id}>
                <path
                  d={edgePath(source, target)}
                  fill="none"
                  stroke="#94a3b8"
                  strokeWidth={1.5}
                  markerEnd="url(#lh-arrow)"
                  className="pointer-events-auto cursor-pointer hover:stroke-brand-500"
                  onClick={(e) => {
                    e.stopPropagation();
                    removeEdge(edge.id);
                  }}
                >
                  <title>{readOnly ? edge.label : `${edge.label ?? '连线'}（点击删除）`}</title>
                </path>
                {edge.label && (
                  <text
                    x={(source.position.x + NODE_W + target.position.x) / 2}
                    y={(source.position.y + target.position.y) / 2 + NODE_H / 2 - 6}
                    textAnchor="middle"
                    className="fill-slate-400 text-[10px]"
                  >
                    {edge.label}
                  </text>
                )}
              </g>
            );
          })}

          {pendingEdge && nodeMap.get(pendingEdge.sourceId) && (
            <path
              d={`M${nodeMap.get(pendingEdge.sourceId)!.position.x + NODE_W},${
                nodeMap.get(pendingEdge.sourceId)!.position.y + NODE_H / 2
              } L${pendingEdge.x},${pendingEdge.y}`}
              fill="none"
              stroke="#5e6ef6"
              strokeWidth={1.5}
              strokeDasharray="4 3"
            />
          )}
        </svg>

        {/* 节点层 */}
        {nodes.map((node) => {
          const meta = NODE_TYPE_META[node.type];
          const selected = node.id === selectedNodeId;
          return (
            <div
              key={node.id}
              onMouseDown={(e) => handleNodeMouseDown(e, node)}
              onMouseUp={(e) => completeEdge(e, node)}
              onDoubleClick={(e) => {
                e.stopPropagation();
                removeNode(node.id);
              }}
              className={cn(
                'absolute flex items-center gap-2 rounded-lg border bg-white px-3 shadow-card transition-shadow dark:bg-slate-900',
                selected
                  ? 'border-brand-500 ring-2 ring-brand-500/30'
                  : 'border-slate-200 hover:shadow-card-hover dark:border-slate-700',
                !readOnly && 'cursor-grab active:cursor-grabbing',
              )}
              style={{
                left: node.position.x,
                top: node.position.y,
                width: NODE_W,
                height: NODE_H,
              }}
              role="button"
              aria-label={`${meta.label}节点 ${node.label}`}
            >
              <span className={cn('h-2.5 w-2.5 shrink-0 rounded-full', meta.color)} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium text-slate-800 dark:text-slate-100">
                  {node.label}
                </p>
                <p className="text-[10px] text-slate-400">{meta.label}</p>
              </div>
              {!readOnly && (
                <span
                  onMouseDown={(e) => startEdge(e, node)}
                  className="absolute -right-1.5 top-1/2 h-3 w-3 -translate-y-1/2 cursor-crosshair rounded-full border-2 border-white bg-slate-400 transition-colors hover:bg-brand-500 dark:border-slate-900"
                  title="拖出以连接"
                />
              )}
            </div>
          );
        })}

        {nodes.length === 0 && (
          <div className="flex h-full items-center justify-center text-sm text-slate-400">
            空画布 — 请通过 onNodesChange 添加节点
          </div>
        )}
      </div>
    </div>
  );
});
