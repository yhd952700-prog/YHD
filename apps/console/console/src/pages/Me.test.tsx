/**
 * 「我的」页：手机端账号、数据与隐私入口的收敛页。
 */

import { expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Me } from './Me'
import { deriveCaps } from '../lib/accountCaps'

test('成员可从「我的」进入账号安全、我的数据与关于与隐私', () => {
  const onNavigate = vi.fn()
  render(<Me caps={deriveCaps(null)} onNavigate={onNavigate} />)

  for (const name of ['账号安全', '我的数据', '关于与隐私', '我的 Key']) {
    expect(screen.getByRole('button', { name: new RegExp(name) })).toBeTruthy()
  }
  // 根才有的入口不在成员页上出现，避免点了被拒。
  expect(screen.queryByRole('button', { name: /恢复码/ })).toBeNull()
  expect(screen.queryByRole('button', { name: /系统设置/ })).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: /账号安全/ }))
  expect(onNavigate).toHaveBeenCalledWith('account')
})
