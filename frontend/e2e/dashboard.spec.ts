import { expect, test, type Page } from '@playwright/test'

async function signIn(page: Page) {
  await page.goto('/app?ref=frontend-test')
  await page.getByPlaceholder('you@work.com').fill(`browser-${Date.now()}@example.com`)
  await page.getByRole('button', { name: 'Email me a sign-in code' }).click()
  const code = await page.getByText(/dev code \d{6}/).innerText()
  await page.getByPlaceholder('6-digit code').fill(code.match(/\d{6}/)![0])
  await page.getByRole('dialog', { name: 'Sign in' }).getByRole('button', { name: 'Sign in', exact: true }).click()
  await page.getByPlaceholder('Team name, e.g. Superdesign').fill('Browser test team')
  await page.getByRole('button', { name: 'Create team →', exact: true }).click()
  await expect(page.getByText('Which agent are you using?', { exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Skip', exact: true }).click()
  await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible()
}

test('sign in, create team, switch pages, refresh and navigate back', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await signIn(page)
  const navigation = page.getByRole('navigation', { name: 'Primary navigation' })
  for (const name of ['Catalog', 'Your own tools', 'Activity', 'Team']) {
    await navigation.getByRole('button', { name, exact: true }).click()
    await expect(navigation.getByRole('button', { name, exact: true })).toHaveAttribute('aria-current', 'page')
  }
  await page.reload()
  await expect(navigation.getByRole('button', { name: 'Team', exact: true })).toHaveAttribute('aria-current', 'page')
  await page.goBack()
  await expect(navigation.getByRole('button', { name: 'Activity', exact: true })).toHaveAttribute('aria-current', 'page')
  await page.goForward()
  await expect(navigation.getByRole('button', { name: 'Team', exact: true })).toHaveAttribute('aria-current', 'page')
  await page.locator('.rd-account-menu summary').click()
  await page.locator('.rd-account-menu').getByRole('button', { name: 'Billing', exact: true }).click()
  await expect(page).toHaveURL(/#orgs$/)
  expect(errors).toEqual([])
})

test('onboarding controls and images work on mobile and dark theme', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await signIn(page)
  await page.getByRole('button', { name: 'Getting started', exact: true }).click()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.locator('.rd-start')).toBeVisible()
  const trigger = page.locator('[aria-controls="rd-agent-options"]')
  await trigger.click()
  await page.locator('#rd-agent-options').getByRole('button', { name: 'Codex', exact: true }).click()
  await expect(trigger).toContainText('Codex')
  await page.getByRole('button', { name: 'Show key', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Hide key', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Hide key', exact: true }).click()
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', {
    configurable: true, value: { writeText: () => Promise.reject(new Error('denied')) },
  }))
  await page.locator('.rd-setup-panel').first().getByRole('button', { name: 'Copy', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Could not copy')
  await page.getByRole('button', { name: 'Dismiss', exact: true }).click()
  await page.locator('.rd-account-menu summary').click()
  await page.getByRole('button', { name: 'Dark appearance' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.waitForFunction(() => [...document.querySelectorAll<HTMLImageElement>('.rd-task-image, .rd-try .try-ico')].every(img => img.complete && img.naturalWidth > 0))
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('mobile-dark.png'), fullPage: true })
  expect(errors).toEqual([])
})

test('public catalog and shared deep links remain available without a session', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/catalog')
  await expect(page.locator('.pubnav')).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Start free', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Sign in' })).toBeVisible()
  await page.goto('/catalog/google')
  await expect(page.locator('.plat-head')).toBeVisible()
  await page.reload()
  await expect(page.locator('.plat-head')).toBeVisible()
  await page.goto('/app/tools/shared-example')
  await expect(page.getByRole('heading', { name: /shared-example/ })).toBeVisible()
  await expect(page.getByRole('dialog', { name: 'Sign in' })).toBeVisible()
  expect(errors).toEqual([])
})

test('session initialization never flashes the old signed-out landing page', async ({ page }) => {
  let releaseMeta!: () => void
  const metaGate = new Promise<void>(resolve => { releaseMeta = resolve })
  await page.route('**/meta', async route => { await metaGate; await route.continue() })
  await page.goto('/app?ref=frontend-test')
  await expect(page.getByRole('status')).toHaveText('Loading treg…')
  await expect(page.getByText('the tool catalog for your agent', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('dialog', { name: 'Sign in' })).toHaveCount(0)
  releaseMeta()
  await expect(page.getByPlaceholder('you@work.com')).toBeVisible()
  await page.unroute('**/meta')

  await signIn(page)
  let releaseSession!: () => void
  const sessionGate = new Promise<void>(resolve => { releaseSession = resolve })
  await page.route('**/auth/me', async route => { await sessionGate; await route.continue() })
  await page.reload()
  await expect(page.getByRole('status')).toHaveText('Loading treg…')
  await expect(page.getByText('Sign in to treg', { exact: true })).toHaveCount(0)
  releaseSession()
  await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible()
})
