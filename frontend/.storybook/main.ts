import type { StorybookConfig } from '@storybook/react-vite'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

const config: StorybookConfig = {
  stories: ['../src/**/*.stories.@(ts|tsx)'],
  addons: ['@storybook/addon-a11y'],
  framework: {
    name: '@storybook/react-vite',
    options: {},
  },
  // Storybook builds its own Vite instance, separate from vite.config.ts -- the Tailwind v4
  // plugin and the "@/*" path alias both need to be reapplied here or component styles and
  // imports silently break in Storybook only.
  async viteFinal(viteConfig) {
    viteConfig.plugins ??= []
    viteConfig.plugins.push(tailwindcss())
    viteConfig.resolve ??= {}
    viteConfig.resolve.alias = {
      ...viteConfig.resolve.alias,
      '@': path.resolve(import.meta.dirname, '../src'),
    }
    return viteConfig
  },
}

export default config
