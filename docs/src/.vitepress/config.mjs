import { defineConfig } from 'vitepress'
import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import { navEn, navZh } from './theme/config/nav.js'
import { sidebarEn, sidebarZh } from './theme/config/sidebar.js'

// https://vitepress.dev/reference/site-config
export default defineConfig({
  title: "Slyme",
  base: "/slyme/",
  head: [['link', { rel: 'icon', href: '/Slyme.svg' }]],
  themeConfig: {
    // https://vitepress.dev/reference/default-theme-config
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
      themeConfig: {
        nav: navEn,
        sidebar: sidebarEn,
      }
    },
    zh: {
      label: '简体中文',
      lang: 'zh-CN',
      description: '欢迎使用 Slyme!',
      themeConfig: {
        nav: navZh,
        sidebar: sidebarZh,
        outline: { label: '页面导航' },
        docFooter: {
          prev: '上一页',
          next: '下一页'
        },
      }
    }
  }
})
