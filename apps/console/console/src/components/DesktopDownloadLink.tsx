/**
 * 「下载桌面版」入口 —— **只在网页端出现**。
 *
 * 显隐完全交给 CSS（`os.css` 里按 `<html data-mode="web" data-client="browser">`
 * 显示）：桌面壳固定是 desktop，手机端是 mobile，两者都不会渲染出这个入口；
 * 这样也不需要在组件里再做一次形态判断。
 *
 * 目标指向**最新一次发布**（GitHub Releases 的 ``latest``），用户落地后看到的
 * 就是当前版本的安装包 `LiuHao-Setup-<版本>.exe`，而不是一串历史版本列表。
 * 这个地址在正式发布会之前会是 404（仓库还没有任何 Release），一旦发布即可用；
 * 若后续要改成一键直下，可换成
 * ``/releases/latest/download/LiuHao-Setup-<APP_VERSION>.exe``，但必须保证
 * 发布流程确实上传了同名附件（目前发布是手工流程，未见自动上传）。
 */

import { IconDesktop } from './icons'

export const DESKTOP_DOWNLOAD_URL =
  'https://github.com/yhd952700-prog/YHD/releases/latest'

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
