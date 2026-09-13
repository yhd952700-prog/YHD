/**
 * 内联 SVG 图标集。
 *
 * 刻意不引入图标库：驾驶舱要作为 PWA 装到手机上，每多一个依赖就多一份体积与
 * 供应链面。这里的图标都是 24×24、`stroke="currentColor"`，颜色随主题走，
 * 不需要为深色/浅色各准备一套。
 */

import type { ReactNode } from 'react'

export interface IconProps {
  size?: number
  className?: string
}

function Glyph({ size = 18, className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

export const IconDashboard = (p: IconProps) => (
  <Glyph {...p}>
    <rect x="3" y="3" width="7.5" height="7.5" rx="1.6" />
    <rect x="13.5" y="3" width="7.5" height="7.5" rx="1.6" />
    <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.6" />
    <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.6" />
  </Glyph>
)

export const IconEmployees = (p: IconProps) => (
  <Glyph {...p}>
    <circle cx="9" cy="8" r="3.1" />
    <path d="M3.4 20c0-3.1 2.5-5.2 5.6-5.2s5.6 2.1 5.6 5.2" />
    <path d="M16.4 5.6a3 3 0 0 1 0 5.6" />
    <path d="M17.6 14.9c1.9.6 3.2 2.2 3.2 4.3" />
  </Glyph>
)

export const IconBusiness = (p: IconProps) => (
  <Glyph {...p}>
    <path d="M3 20h18" />
    <rect x="4.5" y="11" width="3.4" height="6" rx="1" />
    <rect x="10.3" y="6.5" width="3.4" height="10.5" rx="1" />
    <rect x="16.1" y="9" width="3.4" height="8" rx="1" />
  </Glyph>
)

export const IconKnowledge = (p: IconProps) => (
  <Glyph {...p}>
    <path d="M4 4.8A1.8 1.8 0 0 1 5.8 3H18a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5.8A1.8 1.8 0 0 1 4 19.2z" />
    <path d="M8 3v18" />
    <path d="M12.5 8h4M12.5 12h4" />
  </Glyph>
)

export const IconWorkbench = (p: IconProps) => (
  <Glyph {...p}>
    <rect x="2.8" y="4" width="18.4" height="16" rx="2.2" />
    <path d="M7 9.2l2.6 2.6L7 14.4" />
    <path d="M12.6 15h4.4" />
  </Glyph>
)

export const IconData = (p: IconProps) => (
  <Glyph {...p}>
    <ellipse cx="12" cy="5.6" rx="7.6" ry="2.9" />
    <path d="M4.4 5.6v12.8c0 1.6 3.4 2.9 7.6 2.9s7.6-1.3 7.6-2.9V5.6" />
    <path d="M4.4 12c0 1.6 3.4 2.9 7.6 2.9s7.6-1.3 7.6-2.9" />
  </Glyph>
)

export const IconSettings = (p: IconProps) => (
  <Glyph {...p}>
    <circle cx="12" cy="12" r="3.1" />
    <path d="M19.4 14.4a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.11-1.56 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.56-1.11 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34H9a1.7 1.7 0 0 0 1.03-1.56V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87V9a1.7 1.7 0 0 0 1.56 1.03H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1.03z" />
  </Glyph>
)

export const IconShield = (p: IconProps) => (
  <Glyph {...p}>
    <path d="M12 3l7.2 2.8v5.4c0 4.4-3 8.3-7.2 9.6-4.2-1.3-7.2-5.2-7.2-9.6V5.8z" />
    <path d="M9.2 12.2l2 2 3.6-3.9" />
  </Glyph>
)

export const IconActivity = (p: IconProps) => (
  <Glyph {...p}>
    <path d="M3 12.5h3.6l2.2-6.4 3.1 11.6 2.5-7.2 1.7 3.6H21" />
  </Glyph>
)

export const IconSearch = (p: IconProps) => (
  <Glyph {...p}>
    <circle cx="10.8" cy="10.8" r="6.4" />
    <path d="M15.6 15.6L20.4 20.4" />
  </Glyph>
)

export const IconBell = (p: IconProps) => (
  <Glyph {...p}>
    <path d="M18 15.6V10a6 6 0 0 0-12 0v5.6L4.4 18h15.2z" />
    <path d="M10 21h4" />
  </Glyph>
)

export const IconLogout = (p: IconProps) => (
  <Glyph {...p}>
    <path d="M15 4h3.2A1.8 1.8 0 0 1 20 5.8v12.4A1.8 1.8 0 0 1 18.2 20H15" />
    <path d="M10.6 8.2L7 12l3.6 3.8" />
    <path d="M7 12h8" />
  </Glyph>
)

export const IconEye = (p: IconProps) => (
  <Glyph {...p}>
    <path d="M2.4 12S6 5.8 12 5.8 21.6 12 21.6 12 18 18.2 12 18.2 2.4 12 2.4 12z" />
    <circle cx="12" cy="12" r="2.9" />
  </Glyph>
)

export const IconEyeOff = (p: IconProps) => (
  <Glyph {...p}>
    <path d="M4.2 4.2l15.6 15.6" />
    <path d="M9.6 5.9A8.6 8.6 0 0 1 12 5.8c6 0 9.6 6.2 9.6 6.2a17 17 0 0 1-3.1 3.9" />
    <path d="M6.3 7.7A16.7 16.7 0 0 0 2.4 12s3.6 6.2 9.6 6.2a8.6 8.6 0 0 0 3.2-.6" />
    <path d="M10.1 10.2a2.9 2.9 0 0 0 4 4" />
  </Glyph>
)

export const IconDesktop = (p: IconProps) => (
  <Glyph {...p}>
    <rect x="2.8" y="4" width="18.4" height="12.4" rx="1.8" />
    <path d="M9 20h6M12 16.4V20" />
  </Glyph>
)

export const IconGlobe = (p: IconProps) => (
  <Glyph {...p}>
    <circle cx="12" cy="12" r="8.6" />
    <path d="M3.4 12h17.2" />
    <path d="M12 3.4c2.3 2.3 3.5 5.3 3.5 8.6S14.3 18.3 12 20.6c-2.3-2.3-3.5-5.3-3.5-8.6S9.7 5.7 12 3.4z" />
  </Glyph>
)

export const IconMobile = (p: IconProps) => (
  <Glyph {...p}>
    <rect x="6.6" y="2.6" width="10.8" height="18.8" rx="2.4" />
    <path d="M10.8 18.6h2.4" />
  </Glyph>
)

export const IconCheck = (p: IconProps) => (
  <Glyph {...p}>
    <path d="M4.6 12.6l4.6 4.6L19.4 7" />
  </Glyph>
)

export const IconAlert = (p: IconProps) => (
  <Glyph {...p}>
    <path d="M12 3.6l9 15.6H3z" />
    <path d="M12 9.6v4.2M12 16.6h.01" />
  </Glyph>
)

export const IconRefresh = (p: IconProps) => (
  <Glyph {...p}>
    <path d="M20.2 11.4a8.2 8.2 0 1 0-2.1 6" />
    <path d="M20.4 5.6v5.8h-5.8" />
  </Glyph>
)

export const IconServer = (p: IconProps) => (
  <Glyph {...p}>
    <rect x="3" y="4" width="18" height="6.4" rx="1.8" />
    <rect x="3" y="13.6" width="18" height="6.4" rx="1.8" />
    <path d="M7 7.2h.01M7 16.8h.01" />
  </Glyph>
)

export const IconSparkle = (p: IconProps) => (
  <Glyph {...p}>
    <path d="M12 3.2l1.8 4.9 4.9 1.8-4.9 1.8L12 16.6l-1.8-4.9L5.3 9.9l4.9-1.8z" />
    <path d="M18.6 15.4l.8 2.1 2.1.8-2.1.8-.8 2.1-.8-2.1-2.1-.8 2.1-.8z" />
  </Glyph>
)

export const IconLock = (p: IconProps) => (
  <Glyph {...p}>
    <rect x="4.6" y="10.4" width="14.8" height="10" rx="2.2" />
    <path d="M8.2 10.4V7.8a3.8 3.8 0 0 1 7.6 0v2.6" />
  </Glyph>
)
