/**
 * useTask 常量回归（D 组补测试：此前 frontend/src 零测试）。
 *
 * 只测模块导出的纯常量（状态顺序与中文标签），不测 hook 本身：
 * hook 需要 React 渲染环境，frontend 没有配 @testing-library/react，
 * 刻意不为此引入测试渲染依赖。
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { TASK_STATUS_LABELS, TASK_STATUS_ORDER } from './useTask.ts';

describe('TASK_STATUS_ORDER', () => {
  it('六态齐全，顺序从待规划到已阻塞', () => {
    assert.deepEqual(TASK_STATUS_ORDER, [
      'backlog',
      'todo',
      'in_progress',
      'review',
      'done',
      'blocked',
    ]);
  });
});

describe('TASK_STATUS_LABELS', () => {
  it('每个状态都有中文标签，与顺序表一一对应', () => {
    assert.deepEqual(Object.keys(TASK_STATUS_LABELS).sort(), [...TASK_STATUS_ORDER].sort());
    assert.equal(TASK_STATUS_LABELS.backlog, '待规划');
    assert.equal(TASK_STATUS_LABELS.todo, '待处理');
    assert.equal(TASK_STATUS_LABELS.in_progress, '进行中');
    assert.equal(TASK_STATUS_LABELS.review, '待评审');
    assert.equal(TASK_STATUS_LABELS.done, '已完成');
    assert.equal(TASK_STATUS_LABELS.blocked, '已阻塞');
    for (const label of Object.values(TASK_STATUS_LABELS)) {
      assert.ok(label.length > 0, '标签不能为空');
    }
  });
});
