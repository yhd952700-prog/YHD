import { render, screen } from '@testing-library/react'
import { describe, expect, test } from 'vitest'
import { GoalStatus } from './GoalStatus'

describe('GoalStatus 真实渲染', () => {
  test('有产出 = 绿色，并写明产物数量', () => {
    const { container } = render(
      <GoalStatus goal={{ state: 'completed', artifacts: ['a.md', 'b.html'] }} />,
    )
    expect(screen.getByText('已完成并产出')).toBeTruthy()
    expect(container.querySelector('.os-tone-ok')).toBeTruthy()
    expect(container.querySelector('.os-tone-warning')).toBeNull()
  })

  test('无产出 = 黄色，不能渲染成绿色成功', () => {
    const { container } = render(
      <GoalStatus goal={{ state: 'completed', artifacts: [] }} showHint />,
    )
    expect(screen.getByText(/已完成但无产出/)).toBeTruthy()
    expect(container.querySelector('.os-tone-warning')).toBeTruthy()
    expect(container.querySelector('.os-tone-ok')).toBeNull()
    expect(screen.getByText(/没有写出任何文件/)).toBeTruthy()
    expect(screen.getByText(/可重试/)).toBeTruthy()
  })

  test('未完成 = 红色，并给出重试 / 改需求的下一步', () => {
    const { container } = render(
      <GoalStatus goal={{ state: 'failed', failed_tasks: 1 }} showHint />,
    )
    expect(screen.getByText(/未完成/)).toBeTruthy()
    expect(container.querySelector('.os-tone-danger')).toBeTruthy()
    expect(screen.getAllByText(/重试/).length).toBeGreaterThan(0)
    expect(screen.getByText(/补充需求/)).toBeTruthy()
  })
})
