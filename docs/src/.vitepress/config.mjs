import { defineConfig } from 'vitepress'
import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import nav from './theme/config/nav.js'
import sidebar from './theme/config/sidebar.js'

// https://vitepress.dev/reference/site-config
export default defineConfig({
  title: "Slyme",
  base: "/slyme/",
  head: [['link', { rel: 'icon', href: '/Slyme.svg' }]],
  themeConfig: {
    // https://vitepress.dev/reference/default-theme-config
    nav: nav,
    sidebar: sidebar,
    socialLinks: [
      { icon: 'github', link: 'https://github.com/slymelab/slyme' }
    ],
    logo: '/Slyme.svg',
  },
  vite: {
    plugins: [tailwindcss()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./theme', import.meta.url))
      },
    },
    ssr: {
      noExternal: [
        '@slymelab/theme',
      ],
    }
  },
  locales: {
    root: {
      label: 'English',
      lang: 'en-US',
      description: 'Welcome to Slyme!',
    },
  }
})
