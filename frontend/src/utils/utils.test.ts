/**
 * frontend 工具函数回归（D 组补测试：此前 frontend/src 零测试）。
 *
 * 运行方式：`npm test`（Node 24 自带 TypeScript 类型剥离，无需 vitest；
 * frontend 没有配测试运行器，刻意不引入 vitest/jsdom 重依赖——这里只测
 * 纯函数，不测 React 渲染）。
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  avatarColor,
  cn,
  formatBytes,
  formatCurrency,
  formatDuration,
  formatNumber,
  formatPercent,
  formatRelativeTime,
  formatTime,
  genId,
  initials,
  truncate,
} from './index.ts';

describe('cn', () => {
  it('合并真值 class，过滤假值', () => {
    assert.equal(cn('a', false, 'b', null, undefined, 'c'), 'a b c');
    assert.equal(cn(), '');
  });
});

describe('formatBytes', () => {
  it('按 1024 进位，非法/负数返回 0 B', () => {
    assert.equal(formatBytes(0), '0 B');
    assert.equal(formatBytes(512), '512 B');
    assert.equal(formatBytes(2048), '2.0 KB');
    assert.equal(formatBytes(3 * 1024 * 1024), '3.0 MB');
    assert.equal(formatBytes(-1), '0 B');
    assert.equal(formatBytes(Number.NaN), '0 B');
  });
});

describe('formatNumber', () => {
  it('万/百万以上缩写，其余千分位', () => {
    assert.equal(formatNumber(999), '999');
    assert.equal(formatNumber(15000), '15.0K');
    assert.equal(formatNumber(2500000), '2.5M');
    assert.equal(formatNumber(Number.NaN), '0');
  });
});

describe('formatCurrency', () => {
  it('小于 1 保留 4 位小数，其余 2 位', () => {
    assert.equal(formatCurrency(0.123456), '$0.1235');
    assert.equal(formatCurrency(12.345), '$12.35');
  });
});

describe('formatPercent / formatDuration', () => {
  it('百分比与时长换算', () => {
    assert.equal(formatPercent(0.856), '85.6%');
    assert.equal(formatPercent(1), '100.0%');
    assert.equal(formatDuration(500), '500ms');
    assert.equal(formatDuration(1500), '1.5s');
    assert.equal(formatDuration(90000), '1m 30s');
    assert.equal(formatDuration(3720000), '1h 2m');
  });
});

describe('formatRelativeTime', () => {
  it('刚刚 / 分钟前 / 小时前 / 天前分段正确', () => {
    const now = Date.now();
    assert.equal(formatRelativeTime(new Date(now - 30_000).toISOString()), '刚刚');
    assert.equal(formatRelativeTime(new Date(now - 5 * 60_000).toISOString()), '5 分钟前');
    assert.equal(formatRelativeTime(new Date(now - 3 * 3_600_000).toISOString()), '3 小时前');
    assert.equal(formatRelativeTime(new Date(now - 2 * 86_400_000).toISOString()), '2 天前');
  });

  it('非法时间原样返回，不编一个时间', () => {
    assert.equal(formatRelativeTime('not-a-date'), 'not-a-date');
  });
});

describe('formatTime', () => {
  it('今天只显示时间，非法原样返回', () => {
    const today = new Date();
    const out = formatTime(today.toISOString());
    assert.match(out, /^\d{2}:\d{2}$/);
    assert.equal(formatTime('not-a-date'), 'not-a-date');
    assert.match(formatTime(today.toISOString(), true), /^\d{2}:\d{2}:\d{2}$/);
  });
});

describe('truncate', () => {
  it('超长截断并补省略号，不超长原样返回', () => {
    assert.equal(truncate('hello', 10), 'hello');
    assert.equal(truncate('hello world', 6), 'hello…');
  });
});

describe('genId / avatarColor / initials', () => {
  it('genId 带前缀且每次不同', () => {
    const a = genId('task');
    const b = genId('task');
    assert.ok(a.startsWith('task-'));
    assert.notEqual(a, b);
  });

  it('同一 seed 永远同一底色，不同 seed 大概率不同', () => {
    assert.equal(avatarColor('alice'), avatarColor('alice'));
    assert.match(avatarColor('alice'), /^bg-(violet|blue|emerald|amber|rose|cyan|indigo|teal)-500$/);
  });

  it('中文取前两字，英文取首末词首字母，空白返回 ?', () => {
    assert.equal(initials('张小龙'), '张小');
    assert.equal(initials('John Smith'), 'JS');
    assert.equal(initials('Madonna'), 'MA');
    assert.equal(initials('   '), '?');
  });
});
