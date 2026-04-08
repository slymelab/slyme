<template>
  <div class="relative min-h-screen overflow-hidden font-sans text-gray-900 dark:text-gray-50">
    <header
      class="relative z-10 w-full max-w-[1200px] mx-auto px-8 pt-12 pb-16 grid grid-cols-1 lg:grid-cols-12 gap-16 items-center">
      <div class="lg:col-span-6 flex flex-col items-start gap-8">
        <div class="flex items-center gap-4">
          <SlymeIcon char="S" size="lg" />
          <h1 class="font-mono text-6xl tracking-tighter">Slyme</h1>
        </div>
        <p class="text-xl leading-relaxed font-medium max-w-lg">
          Slyme（发音为 /slaɪm/）是一个高度可组合的函数式执行框架。<br />
          <span class="text-gray-500 dark:text-gray-400 block mt-2 font-mono">Mold Everything, Safely.</span>
        </p>
        <div class="flex flex-col sm:flex-row items-center gap-8 w-full mt-2">
          <SlymeCodeSnippet command="pip install slyme" />
          <SlymeButtonLink :href="withBase('/zh/guide/intro/what-is-slyme')" variant="outline">
            开始了解 -&gt;
          </SlymeButtonLink>
        </div>
      </div>
      <div class="lg:col-span-6 relative">
        <SlymeCodeBlock filename="quick_start.py" :code="data.rawCode">
          <div v-html="data.html"></div>
        </SlymeCodeBlock>
      </div>
    </header>
    <div class="w-full max-w-[1200px] mx-auto px-8">
      <hr class="border-gray-200/80">
    </div>

    <section class="w-full max-w-[1200px] mx-auto px-8 py-16">
      <div class="mb-12">
        <h2 class="font-mono text-3xl font-semibold tracking-tight">为什么要使用 Slyme?</h2>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-3 gap-8">
        <SlymeDescription>
          <template #title>Pythonic 开发体验</template>
          <template #description>丢掉臃肿的样板代码，使用函数式组件和熟悉的 Python 语法，实现极致简洁。</template>
        </SlymeDescription>
        <SlymeDescription>
          <template #title>无限可组合性</template>
          <template #description>将节点像乐高积木一样组合起来，构建动态拓扑结构，从简单脚本到复杂自定义工作流，轻松应对。</template>
        </SlymeDescription>
        <SlymeDescription>
          <template #title>无缝协作</template>
          <template #description>告别烦人的代码冲突，实现团队内部 / 开源社区的无缝协作。</template>
        </SlymeDescription>
      </div>
    </section>

    <section class="relative w-full max-w-[1200px] mx-auto px-8 py-12">
      <SlymeCard :hoverEffect="false" size="lg">
        <template #header>
          <SlymeBadge variant="default">核心架构</SlymeBadge>
        </template>
        <template #title>
          阶段划分：构建期可变、执行期不可变
        </template>
        <span class="text-md">传统的计算图要么过于死板而难以调试，要么容易陷入混乱的状态突变。Slyme 引入了明确的边界：在<b>构建阶段</b>，您可以像操作标准 Python 对象一样，动态地构建 / 修改拓扑结构。而在<b>执行阶段</b>，只需一次 <code>prepare()</code> 调用，即可将所有内容冻结为严格且不可变的执行流。</span>
        <template #aside>
          <div
            class="bg-gray-50 dark:bg-white/5 rounded-2xl border border-gray-200 dark:border-white/10 p-6 flex flex-col justify-center gap-2 font-mono text-sm h-full w-full shadow-sm">

            <div class="flex flex-col gap-1">
              <div class="flex items-center justify-between text-gray-800 dark:text-gray-200 font-bold">
                <span class="text-base">构建阶段</span>
                <span class="text-blue-600 bg-blue-100 dark:bg-blue-900/40 px-2 py-1 rounded text-xs">可变</span>
              </div>
              <div
                class="p-4 border border-dashed border-blue-300 dark:border-blue-700/60 rounded-lg bg-white dark:bg-black/20 text-gray-700 dark:text-gray-300 leading-relaxed">
                <span class="text-gray-400"># 动态构建节点</span><br />
                read_data.add_wrappers(retry, log)<br />
                pipeline = sequential([read_data, model_forward])
              </div>
            </div>
            <div class="flex justify-center text-gray-400">
              <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 14l-7 7m0 0l-7-7m7 7V3">
                </path>
              </svg>
            </div>
            <div class="flex flex-col gap-1">
              <div class="flex items-center justify-between text-gray-800 dark:text-gray-200 font-bold">
                <span class="text-base">执行阶段</span>
                <span
                  class="text-green-600 bg-green-100 dark:bg-green-900/40 px-2 py-1 rounded text-xs">不可变</span>
              </div>
              <div
                class="p-4 border border-green-400/50 rounded-lg bg-green-50 dark:bg-green-900/10 text-green-800 dark:text-green-300 leading-relaxed">
                <span class="text-green-600/70 dark:text-green-400/70"># 冻结拓扑结构以确保安全执行</span><br />
                frozen_chain = pipeline.prepare()<br />
                ctx = frozen_chain(ctx)
              </div>
            </div>
          </div>
        </template>
      </SlymeCard>
    </section>
    <SlymeFooter>
      <template #brand>
        <div class="flex flex-row items-center">
          <SlymeIcon size="md" char="S" />
          <p class="ml-3 text-lg font-bold">Slyme Lab</p>
        </div>
        <p class="font-mono text-sm leading-relaxed text-gray-600 dark:text-gray-400">
          一个以极致的简洁构建、编排与扩展函数式执行流程的统一生态系统。
        </p>
      </template>
      <template #columns>
        <SlymeFooterColumn title="资源">
          <li>
            <SlymeLink :href="withBase('/zh/')" theme="muted" class="text-sm">首页</SlymeLink>
          </li>
          <li>
            <SlymeLink :href="withBase('/zh/guide/intro/what-is-slyme')" theme="muted" class="text-sm">
              指南
            </SlymeLink>
          </li>
        </SlymeFooterColumn>
        <SlymeFooterColumn title="联系我们">
          <li>
            <SlymeLink href="https://github.com/slymelab/slyme" target="_blank" theme="muted" class="text-sm">
              <div class="flex flex-row items-center">
                <svg class="w-6 h-6" fill="currentColor" viewBox="0 0 24 24">
                  <path
                    d="M12 2A10 10 0 0 0 2 12c0 4.42 2.87 8.17 6.84 9.5.5.08.66-.23.66-.5v-1.69c-2.77.6-3.36-1.34-3.36-1.34-.46-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.87 1.52 2.34 1.07 2.91.83.09-.65.35-1.09.63-1.34-2.22-.25-4.55-1.11-4.55-4.92 0-1.11.38-2 1.03-2.71-.1-.25-.45-1.29.1-2.64 0 0 .84-.27 2.75 1.02.79-.22 1.63-.33 2.47-.33.84 0 1.68.11 2.47.33 1.91-1.29 2.75-1.02 2.75-1.02.55 1.35.2 2.39.1 2.64.65.71 1.03 1.6 1.03 2.71 0 3.82-2.34 4.66-4.57 4.91.36.31.69.92.69 1.85V21c0 .27.16.59.67.5C19.14 20.16 22 16.42 22 12A10 10 0 0 0 12 2z" />
                </svg>
                <p class="text-sm ml-1">GitHub</p>
              </div>
            </SlymeLink>
          </li>
        </SlymeFooterColumn>
      </template>
      <template #bottom-left>
        &copy; {{ new Date().getFullYear() }} The SlymeLab Team.
      </template>
      <template #bottom-right>
        Built with Pythonic Passion.
      </template>
    </SlymeFooter>
  </div>
</template>

<script setup>
import { withBase } from 'vitepress'
import { SlymeDescription, SlymeCodeSnippet, SlymeButtonLink, SlymeCodeBlock, SlymeCard, SlymeBadge, SlymeIcon, SlymeFooter, SlymeFooterColumn, SlymeLink } from '@slymelab/theme'
import { data } from '@/data/index.data.mjs'
</script>
