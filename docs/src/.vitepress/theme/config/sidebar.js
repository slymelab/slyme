const guideEn = [
  {
    text: 'Get Started',
    items: [
      { text: 'What is slyme?', link: '/guide/intro/what-is-slyme' },
      { text: 'Quick Start', link: '/guide/intro/quick-start' },
      { text: 'Using the Slyme Skill', link: '/guide/intro/using-slyme-skill' },
    ],
    collapsed: false,
  },
  {
    text: 'Essentials',
    items: [
      { text: 'Context', link: '/guide/essentials/context' },
      { text: 'Node', link: '/guide/essentials/node' },
      { text: 'Builder', link: '/guide/essentials/builder' },
      { text: 'Lifecycle', link: '/guide/essentials/lifecycle' },
    ],
    collapsed: false,
  },
  {
    text: 'Slyme In Depth',
    items: [
      { text: 'Functional Programming Basics', link: '/guide/slyme-in-depth/functional-programming-basics' },
      { text: 'PyTree in Slyme', link: '/guide/slyme-in-depth/pytree-in-slyme' },
      { text: 'Dependency Injection', link: '/guide/slyme-in-depth/dependency-injection' },
    ],
    collapsed: false,
  },
  {
    text: 'Contributing',
    items: [
      { text: 'Overview', link: '/guide/contributing/' },
    ],
    collapsed: false,
  },
]

const sidebarEn = {
  '/guide/': guideEn,
}

const guideZh = [
  {
    text: '开始使用',
    items: [
      { text: 'Slyme 是什么？', link: '/zh/guide/intro/what-is-slyme' },
      { text: '快速上手', link: '/zh/guide/intro/quick-start' },
      { text: '使用 Slyme Skill', link: '/zh/guide/intro/using-slyme-skill' },
    ],
    collapsed: false,
  },
  {
    text: '基础',
    items: [
      { text: 'Context', link: '/zh/guide/essentials/context' },
      { text: 'Node', link: '/zh/guide/essentials/node' },
      { text: 'Builder', link: '/zh/guide/essentials/builder' },
      { text: '生命周期', link: '/zh/guide/essentials/lifecycle' },
    ],
    collapsed: false,
  },
  {
    text: '深入 Slyme',
    items: [
      { text: '函数式编程基础', link: '/zh/guide/slyme-in-depth/functional-programming-basics' },
      { text: '深入理解 Slyme 中的 PyTree', link: '/zh/guide/slyme-in-depth/pytree-in-slyme' },
      { text: '依赖注入', link: '/zh/guide/slyme-in-depth/dependency-injection' },
    ],
    collapsed: false,
  },
  {
    text: '参与贡献',
    items: [
      { text: '概述', link: '/zh/guide/contributing/' },
    ],
    collapsed: false,
  },
]

const sidebarZh = {
  '/zh/guide/': guideZh,
}


export {
  sidebarEn,
  sidebarZh,
}
