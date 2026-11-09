import { useCallback, useRef } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'

/**
 * 组合输入结束后的余波窗口。
 *
 * 部分浏览器（典型是 Safari / 某些 Windows 输入法）在 `compositionend` 之后仍会补发一次
 * Enter 的 keydown，此时 `isComposing` 已经是 false、`keyCode` 也回到了 13。如果不兜这一下，
 * 用户"选词的那一下回车"会紧接着被当成发送。50ms 足够覆盖同一物理按键的事件序列，
 * 又远小于人再按一次回车的最短间隔。
 */
const POST_COMPOSITION_ENTER_MS = 50

export interface ImeCompositionHandlers<E extends HTMLElement = HTMLElement> {
  onCompositionStart: () => void
  onCompositionEnd: () => void
  /**
   * true 表示这次按键属于输入法组合过程（或刚结束的余波），业务层必须直接 return：
   * 不能发送、不能查询，也不能 preventDefault 吞掉输入法自己要用的事件。
   */
  isImeKey: (event: ReactKeyboardEvent<E>) => boolean
}

/**
 * 中文/日文输入法组词期间的键盘事件护栏。
 *
 * 判据是"双保险"：组合状态（compositionstart/end 维护的 ref）+ 原生 `isComposing` +
 * `keyCode === 229`（Windows Chrome/Edge 在组合期间固定发 229，而 `isComposing` 在
 * compositionend 之后可能已为 false）。三个条件任一命中都必须放行给输入法。
 */
export function useImeComposition<E extends HTMLElement = HTMLElement>(): ImeCompositionHandlers<E> {
  const composing = useRef(false)
  const endedAt = useRef(0)

  const onCompositionStart = useCallback(() => {
    composing.current = true
  }, [])

  const onCompositionEnd = useCallback(() => {
    composing.current = false
    endedAt.current = Date.now()
  }, [])

  const isImeKey = useCallback((event: ReactKeyboardEvent<E>) => {
    if (composing.current) return true
    if (event.nativeEvent.isComposing) return true
    if (event.keyCode === 229) return true
    if (
      event.key === 'Enter' &&
      endedAt.current > 0 &&
      Date.now() - endedAt.current < POST_COMPOSITION_ENTER_MS
    ) {
      return true
    }
    return false
  }, [])

  return { onCompositionStart, onCompositionEnd, isImeKey }
}
