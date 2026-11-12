/**
 * 工作台名字：全站唯一的运行时名字来源。
 *
 * 出厂默认值来自 `brand.ts`（即 `src/brand.json`：Jarvis · 贾维斯）；
 * 用户在首次启动向导或设置页改了名字后，名字存在后端数据目录的
 * `workspace_settings.json`，这里只读 `GET /v1/workspace/name`
 *（登录页未登录时也要显示名字，所以该端点是公开的）。
 *
 * 顶栏（Shell）、登录页（Login）、助手名与问候（ChatPanel）、设置页
 * 里的名字编辑，都只读这一个 store，不再各自写死名字：
 * 服务端不可达 / 未改名时，store 就是出厂默认值，界面不受影响。
 */

import { useSyncExternalStore } from 'react'
import {
  WORKBENCH_LOCKUP,
  WORKBENCH_NAME_EN,
  WORKBENCH_NAME_ZH,
} from '../brand'

export interface WorkspaceName {
  name_en: string
  name_zh: string
  lockup: string
  customized: boolean
}

/** 出厂默认值 = `brand.ts` 的 WORKBENCH_*（即 `src/brand.json`：Jarvis · 贾维斯）。 */
export const DEFAULT_WORKSPACE_NAME: WorkspaceName = {
  name_en: WORKBENCH_NAME_EN,
  name_zh: WORKBENCH_NAME_ZH,
  lockup: WORKBENCH_LOCKUP,
  customized: false,
}

let current: WorkspaceName = DEFAULT_WORKSPACE_NAME
const listeners = new Set<() => void>()
let inFlight: Promise<WorkspaceName> | null = null

function emit() {
  listeners.forEach((listener) => listener())
}

/** 当前名字（同步读；服务端还没返回时就是出厂默认值）。 */
export function getWorkspaceName(): WorkspaceName {
  return current
}

/** 助手在界面上显示的名字：就是当前工作台的中文名。 */
export function getAssistantName(): string {
  return current.name_zh
}

function subscribeWorkspaceName(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** React 读取入口：名字变了，订阅它的顶栏 / 登录页 / 助手名一起重渲染。 */
export function useWorkspaceName(): WorkspaceName {
  return useSyncExternalStore(subscribeWorkspaceName, getWorkspaceName)
}

/**
 * 向后端取一次当前名字并写入 store；失败（后端没起来、静态托管）
 * 时保持现有值，不抛错、不白屏。调用方在 App 挂载时触发一次，
 * 设置页保存成功后也调一次以立即刷新。
 */
export function refreshWorkspaceName(): Promise<WorkspaceName> {
  if (inFlight) return inFlight
  inFlight = (async () => {
    try {
      const response = await fetch('/v1/workspace/name', {
        headers: { Accept: 'application/json' },
      })
      if (!response.ok) return current
      const data = (await response.json()) as Partial<WorkspaceName>
      const next: WorkspaceName = {
        name_en:
          typeof data.name_en === 'string' && data.name_en ? data.name_en : WORKBENCH_NAME_EN,
        name_zh:
          typeof data.name_zh === 'string' && data.name_zh ? data.name_zh : WORKBENCH_NAME_ZH,
        lockup:
          typeof data.lockup === 'string' && data.lockup ? data.lockup : WORKBENCH_LOCKUP,
        customized: data.customized === true,
      }
      if (next.lockup !== current.lockup || next.customized !== current.customized) {
        current = next
        emit()
      }
      return current
    } catch {
      return current
    } finally {
      inFlight = null
    }
  })()
  return inFlight
}

/** 设置页保存成功后直接写入 store（不等下一次拉取就刷新界面）。 */
export function setWorkspaceNameLocal(next: WorkspaceName): void {
  current = next
  emit()
}

/** 仅供测试：把 store 复位到出厂默认值。 */
export function resetWorkspaceNameForTest(): void {
  current = DEFAULT_WORKSPACE_NAME
  emit()
}
