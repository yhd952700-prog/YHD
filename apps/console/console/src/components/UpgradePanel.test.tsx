import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { ApiError } from '../lib/api'
import type { BackupView, UpgradeStatus } from '../lib/upgrade'
import { UpgradePanel } from './UpgradePanel'
import {
  applyUpgrade,
  checkForUpdate,
  fetchUpgradeStatus,
  rollbackUpgrade,
} from '../lib/upgrade'

vi.mock('../lib/upgrade', () => ({
  fetchUpgradeStatus: vi.fn(),
  checkForUpdate: vi.fn(),
  applyUpgrade: vi.fn(),
  rollbackUpgrade: vi.fn(),
}))

const mockedStatus = vi.mocked(fetchUpgradeStatus)
const mockedCheck = vi.mocked(checkForUpdate)
const mockedApply = vi.mocked(applyUpgrade)
const mockedRollback = vi.mocked(rollbackUpgrade)

function makeBackup(patch: Partial<BackupView> = {}): BackupView {
  return {
    root: 'E:\\LiuHaoBackups',
    data_dir: 'D:\\LiuHaoData',
    same_volume: false,
    data_drive: 'D:',
    backup_drive: 'E:',
    backup_count: 2,
    last_backup_at: '2026-11-08T10:01:00Z',
    last_backup_id: 'backup-1',
    migration: null,
    ...patch,
  }
}

function makeStatus(patch: Partial<UpgradeStatus> = {}): UpgradeStatus {
  return {
    ok: true,
    source_configured: true,
    source: '本地发布目录',
    state: 'idle',
    phase: 'idle',
    message: '',
    current_version: '0.1.0',
    latest_version: null,
    update_available: false,
    last_check: null,
    last_operation: null,
    last_apply: null,
    last_rollback: null,
    backup: makeBackup(),
    error: null,
    updated_at: '',
    ...patch,
  }
}

const availableStatus = makeStatus({
  state: 'available',
  latest_version: '0.2.0',
  update_available: true,
  last_check: {
    status: 'update_available',
    message: '发现新版本 0.2.0',
    current_version: '0.1.0',
    latest_version: '0.2.0',
    release: {
      version: '0.2.0',
      artifact: 'liuhao-0.2.0.zip',
      sha256: 'a'.repeat(64),
      size: 1024,
    },
    source: '本地发布目录',
    offline: false,
    checked_at: '2026-11-08T10:00:00Z',
  },
})

const appliedStatus = makeStatus({
  state: 'applied',
  current_version: '0.2.0',
  latest_version: '0.2.0',
  last_operation: {
    id: 'r1',
    kind: 'apply',
    status: 'completed',
    created_at: '2026-11-08T10:01:00Z',
    from_version: '0.1.0',
    to_version: '0.2.0',
    backup_id: 'backup-1',
    reason: '',
    error_category: null,
  },
  last_apply: {
    id: 'r1',
    kind: 'apply',
    status: 'completed',
    created_at: '2026-11-08T10:01:00Z',
    from_version: '0.1.0',
    to_version: '0.2.0',
    backup_id: 'backup-1',
    reason: '',
    error_category: null,
  },
})

beforeEach(() => {
  mockedStatus.mockReset()
  mockedCheck.mockReset()
  mockedApply.mockReset()
  mockedRollback.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('软件更新面板', () => {
  test('未配置更新源时按钮禁用，并明确给出手动覆盖安装方式', async () => {
    mockedStatus.mockResolvedValue(
      makeStatus({
        source_configured: false,
        source: '',
        message: '未配置更新源',
      }),
    )

    render(<UpgradePanel />)

    await waitFor(() => expect(screen.getByText(/当前版本 0.1.0/)).toBeTruthy())
    const button = screen.getByRole('button', { name: '检查更新' }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect(screen.getByText('手动更新（覆盖安装）')).toBeTruthy()
    fireEvent.click(button)
    expect(mockedCheck).not.toHaveBeenCalled()
  })

  test('检查到新版本后可以下载安装，并提示必须重启才生效', async () => {
    mockedStatus.mockResolvedValue(makeStatus())
    mockedCheck.mockResolvedValue({
      ok: true,
      mode: 'synchronous',
      source_configured: true,
      check: availableStatus.last_check!,
      status: availableStatus,
    })
    mockedApply.mockResolvedValue({
      ok: true,
      mode: 'synchronous',
      warning: '',
      apply: {
        record_id: 'r1',
        backup_id: 'backup-1',
        from_version: '0.1.0',
        to_version: '0.2.0',
        installed_files: 3,
        data_checked_files: 1,
        message: '升级完成',
        self_check_passed: true,
        self_check_issues: 0,
      },
      status: appliedStatus,
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<UpgradePanel />)
    await waitFor(() => expect(screen.getByText(/当前版本 0.1.0/)).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: '检查更新' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '下载并安装 0.2.0' })).toBeTruthy(),
    )

    fireEvent.click(screen.getByRole('button', { name: '下载并安装 0.2.0' }))
    await waitFor(() => {
      expect(screen.getByRole('status').textContent).toContain('请关闭并重新启动')
    })
    expect(mockedApply).toHaveBeenCalledWith('0.2.0')
  })

  test('安装失败时展示服务端给出的真实原因，而不是泛泛的“失败”', async () => {
    mockedStatus.mockResolvedValue(availableStatus)
    mockedApply.mockRejectedValue(
      new ApiError(409, '发布包校验失败：内容与清单记录不一致，已停止安装', 'checksum_mismatch'),
    )
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<UpgradePanel />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '下载并安装 0.2.0' })).toBeTruthy(),
    )

    fireEvent.click(screen.getByRole('button', { name: '下载并安装 0.2.0' }))

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('发布包校验失败')
    })
  })

  test('备份信息说清三件事：放哪、是否与数据同盘、换机带走哪个目录', async () => {
    mockedStatus.mockResolvedValue(makeStatus())

    render(<UpgradePanel />)

    const info = await screen.findByTestId('upgrade-backup-info')
    expect(info.textContent).toContain('备份位置：E:\\LiuHaoBackups')
    expect(info.textContent).toContain('与数据目录不在同一块盘（数据在 D: · 备份在 E:）')
    expect(info.textContent).toContain('为什么放在数据目录外')
    expect(info.textContent).toContain('换机时带走：把 E:\\LiuHaoBackups 整个目录复制走')
    expect(info.textContent).toContain('最近一次备份')
    expect(info.textContent).toContain('共 2 份')
  })

  test('手机端不显示本机绝对路径与盘符，窄屏下也不会被长路径撑成竖排', async () => {
    const originalWidth = window.innerWidth
    Object.defineProperty(window, 'innerWidth', {
      configurable: true,
      writable: true,
      value: 360,
    })
    mockedStatus.mockResolvedValue(makeStatus())

    try {
      render(<UpgradePanel />)

      const info = await screen.findByTestId('upgrade-backup-info')
      expect(info.textContent).not.toContain('E:\\LiuHaoBackups')
      expect(info.textContent).not.toContain('D:\\LiuHaoData')
      expect(info.textContent).not.toContain('（数据在 D:')
      expect(info.textContent).toContain('手机端不显示本机路径')
      expect(info.getAttribute('style') ?? '').toContain('overflow-wrap')
    } finally {
      Object.defineProperty(window, 'innerWidth', {
        configurable: true,
        writable: true,
        value: originalWidth,
      })
    }
  })

  test('同盘且还没有备份时，明确警告会一起丢，并说明首次升级会自动生成', async () => {
    mockedStatus.mockResolvedValue(
      makeStatus({
        backup: makeBackup({
          root: 'C:\\Users\\me\\AppData\\Local\\LiuHao\\backups',
          data_dir: 'C:\\Users\\me\\AppData\\Local\\LiuHao\\data',
          same_volume: true,
          data_drive: 'C:',
          backup_drive: 'C:',
          backup_count: 0,
          last_backup_at: null,
          last_backup_id: null,
        }),
      }),
    )

    render(<UpgradePanel />)

    const info = await screen.findByTestId('upgrade-backup-info')
    expect(info.textContent).toContain('在同一块盘（C:）')
    expect(info.textContent).toContain('数据和备份会一起丢')
    expect(info.textContent).toContain('还没有升级备份（首次升级前会自动生成）')
  })

  test('升级成功后可以一键回滚，回滚使用这次升级前的备份', async () => {
    mockedStatus.mockResolvedValue(appliedStatus)
    mockedRollback.mockResolvedValue({
      ok: true,
      mode: 'synchronous',
      warning: '',
      notice: null,
      source_configured: true,
      rollback: {
        record_id: 'r2',
        backup_id: 'backup-1',
        from_version: '0.2.0',
        to_version: '0.1.0',
        restored_files: 3,
        message: '已回滚',
      },
      status: makeStatus({ state: 'rolled_back', current_version: '0.1.0' }),
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<UpgradePanel />)
    const rollback = await screen.findByRole('button', { name: '回滚到升级前版本' })
    fireEvent.click(rollback)

    await waitFor(() => {
      expect(screen.getByRole('status').textContent).toContain('已回滚到 0.1.0')
    })
    expect(mockedRollback).toHaveBeenCalledWith('backup-1')
  })
})
