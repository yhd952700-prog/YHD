import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    // 刻意不开 `globals`：测试里显式 `import { test, expect } from 'vitest'`，
    // 这样"某个断言从哪来"永远是显式的，不靠全局注入隐式成立。
    globals: false,
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
})
