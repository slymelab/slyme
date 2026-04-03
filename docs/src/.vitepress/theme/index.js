// https://vitepress.dev/guide/custom-theme
import Theme from '@slymelab/theme'
import '@/style.css'

/** @type {import('vitepress').Theme} */
export default {
  extends: Theme,
  enhanceApp({ app, router, siteData }) {
    // ...
  }
}
