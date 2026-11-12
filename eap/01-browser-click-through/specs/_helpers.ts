/**
 * Shared helpers for the EAP click-through specs.
 *
 * NOTE: Playwright only collects files matching the spec pattern
 * (`*.spec.ts`); this file is imported, never run as a test, so it is safe
 * to keep shared logic here.
 *
 * Selectors used below are all VERIFIED against the real console source:
 *   - Login.tsx: input#os-principal, input#os-secret, button.os-btn (text 登录)
 *   - auth.ts: token persisted at localStorage['liuhao.session'] as { token }
 *   - App.tsx: unauthenticated state renders ONLY the Login card; on success
 *     the shell renders with `.os-page-title` (page title).
 */

import type { Page } from '@playwright/test'

/** Human principal seeded out-of-band (see fixtures/seed-data.md). */
export const EAP_PRINCIPAL = process.env.EAP_PRINCIPAL || 'eap_admin'
/** Password for that principal. MUST be supplied via env on the host. */
export const EAP_SECRET = process.env.EAP_SECRET || ''

/** Login through the real UI. Idempotent: if already authed, it just waits for the shell. */
export async function loginViaUi(page: Page): Promise<void> {
  await page.goto('/')
  const loginVisible = await page.locator('#os-principal').isVisible().catch(() => false)
  if (loginVisible) {
    await page.fill('#os-principal', EAP_PRINCIPAL)
    await page.fill('#os-secret', EAP_SECRET)
    await page.click('button.os-btn')
  }
  // Shell appears once authed (App.tsx renders Shell with .os-page-title).
  await page.waitForSelector('.os-page-title', { timeout: 15_000 })
}

/** Read the live bearer token out of the session store (localStorage first, then sessionStorage). */
export async function getToken(page: Page): Promise<string> {
  return page.evaluate(() => {
    const read = (key: string): string | null => {
      const raw = (window.localStorage.getItem(key) || window.sessionStorage.getItem(key)) ?? null
      if (!raw) return null
      try {
        return JSON.parse(raw).token as string
      } catch {
        return null
      }
    }
    return read('liuhao.session') || read('liuhao.session.tab') || ''
  })
}

/** Standard dual-write auth headers the console uses (auth.TOKEN_HEADER + Authorization). */
export async function authHeaders(page: Page): Promise<Record<string, string>> {
  const token = await getToken(page)
  return {
    'X-Liuhao-Token': `Bearer ${token}`,
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  }
}
