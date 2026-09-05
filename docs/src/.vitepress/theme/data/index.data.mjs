import { codeToHtml } from 'shiki'

const rawCode = `from slyme.context import Context
from slyme.node import node

@node
def hello(ctx: Context, /):
    print("Hello World!")
    return ctx

hello()(Context())  # Hello World!
`

export default {
  async load() {
    const html = await codeToHtml(rawCode, {
      lang: 'python',
      themes: { light: 'github-light', dark: 'github-dark' },
      defaultColor: false,
    })
    return { rawCode, html }
  }
}
