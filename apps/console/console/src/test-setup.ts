/**
 * 测试环境准备。
 *
 * 存在的原因：Testing Library 的自动清理只在 `globals: true` 时注册。我们刻意不开
 * globals（见 `vitest.config.ts`），所以必须在这里显式清理 —— 否则前一个用例渲染的
 * DOM 会留在 `document.body` 里，让后一个用例的 `queryByText` 命中**上一个用例的
 * 元素**。那种失败比真失败更糟：它看起来像断言写错了，实际上是环境污染。
 */

import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
})
