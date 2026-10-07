/**
 * Preflight 纯逻辑的**真实执行**测试 —— 不是"读源码猜它对不对"。
 *
 * 为什么必须真跑（这是一次工具缺陷逼出来的重构）
 * ----------------------------------------------
 * 这些逻辑最初写在 ``pages/Preflight.tsx`` 里，守卫方式是**用正则从源码里抠出函数体**、
 * 再断言函数体里含有某个三元表达式。那种断言验证的是"源码长得像不像"，不是
 * "函数行为对不对"，失效方向通常是**假绿**（漏掉真缺陷）。
 *
 * 下面是一段**构造的反例**（演示这类断言的失效模式，**并非某次真实提交的历史**）：
 * 保留被断言的三元文本、但实际无条件返回 ``'warn'``::
 *
 *     function severityTone(severity: string): 'danger' | 'warn' {
 *       void (severity === 'warning' ? 'warn' : 'danger')  // 诱饵
 *       return 'warn'                                       // 真实行为：fail-open
 *     }
 *
 * 对该构造体，旧式文本断言（子串两词俱在 + 正则命中）实测**双双为真 ⇒ 假绿**。
 * 所以逻辑被抽到 ``lib/preflightLogic.ts``，由本文件**真正执行**行为：
 * ``node --experimental-strip-types`` + ``node:test``（Node 22 内置，零新依赖）。
 *
 * 运行链（**注意，本文件不被 vitest 收集**）：``vitest.config.ts`` 的 ``include``
 * 只覆盖 ``src`` 目录下的 ``.test.ts`` 与 ``.test.tsx``；本文件位于仓根 ``tests/``
 * 且后缀是 ``.mjs``，两条都不匹配，故 ``npm run test`` **不会**跑到它。
 * 它由 ``tests/test_preflight_surface.py::TestPreflightLogicRunsForReal`` 用
 * ``node --experimental-strip-types --test`` 真跑（跨工具链只此一处；本文件也可手动
 * 执行）。刻意**不**把它拉进 vitest：它是 ``node:test`` 写法，vitest 跑不了，硬拉
 * 只会造出第二条执行链路。
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { preflightVerdict, severityTone } from '../apps/console/console/src/lib/preflightLogic.ts'

test('只有 warning 降级；critical 与任何未知取值都落 danger', () => {
  assert.equal(severityTone('warning'), 'warn')
  assert.equal(severityTone('critical'), 'danger')
})

test('未知 severity 必须 fail-closed 落 danger，绝不落 warn', () => {
  // 把没识别出来的严重级别显示成安全，等于凭猜测放行一份生产配置。
  for (const unknown of ['', 'CRITICAL', 'Warn', 'fatal', 'info', 'unknown', 'undefined']) {
    assert.equal(
      severityTone(unknown),
      'danger',
      `未知取值 ${JSON.stringify(unknown)} 必须落 danger，实际 ${severityTone(unknown)}`,
    )
  }
})

test('ok=true 且无警告：绿、不阻断', () => {
  const v = preflightVerdict(true, 0, 0)
  assert.equal(v.blocked, false)
  assert.equal(v.tone, 'warn')
  assert.match(v.title, /通过/)
})

test('ok=true 但有警告：仍然不阻断，但必须把警告说出来', () => {
  const v = preflightVerdict(true, 0, 3)
  assert.equal(v.blocked, false, 'warning 不影响 ok —— 阻断它就是虚报故障')
  assert.match(v.title, /3 项警告/)
})

test('ok=false：一律 danger 且阻断，并带上真实 critical 计数', () => {
  const v = preflightVerdict(false, 2, 0)
  assert.equal(v.blocked, true)
  assert.equal(v.tone, 'danger')
  assert.match(v.title, /2 项严重问题/)
})

test('结论不因前端重新计数而漂移：计数直接来自调用方（后端）', () => {
  // 若前端自己数 findings，传入的 criticalCount 会被忽略；这里锁死"参数即真相"。
  const v = preflightVerdict(false, 7, 0)
  assert.match(v.title, /7 项严重问题/)
})