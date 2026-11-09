/**
 * 「下载桌面版」入口 —— **只在网页端出现**。
 *
 * 显隐完全交给 CSS（`os.css` 里按 `<html data-mode="web" data-client="browser">`
 * 显示）：桌面壳固定是 desktop，手机端是 mobile，两者都不会渲染出这个入口；
 * 这样也不需要在组件里再做一次形态判断。
 *
 * 目标指向项目的发布页（GitHub Releases）。发布页目前可能还没有安装包，
 * 但链接本身有效，不会把用户带到 404。
 */

import { IconDesktop } from './icons'

export const DESKTOP_DOWNLOAD_URL = 'https://github.com/yhd952700-prog/YHD/releases'

export interface DesktopDownloadLinkProps {
  /** 顶栏用紧凑文案（「下载桌面版」），登录卡用完整文案。 */
  compact?: boolean
}

export function DesktopDownloadLink({ compact = false }: DesktopDownloadLinkProps) {
  return (
    <a
      className="os-download-link"
      href={DESKTOP_DOWNLOAD_URL}
      target="_blank"
      rel="noopener noreferrer"
      data-testid="desktop-download-link"
      aria-label="下载 Windows 桌面版（前往发布页）"
    >
      <IconDesktop size={15} />
      <span>{compact ? '下载桌面版' : '下载 Windows 桌面版'}</span>
    </a>
  )
}
