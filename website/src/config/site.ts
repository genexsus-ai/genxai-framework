export const site = {
  name: 'GenXAI',
  tagline: 'Graph-native agentic AI framework',
  description:
    'GenXAI is an advanced agentic AI framework with graph-based orchestration, multi-layer memory, extensible tools, and production-grade runtime features — fully open source under the MIT license.',
  links: {
    github: 'https://github.com/genexsus-ai/genxai-framework',
    docsIndexInRepo:
      'https://github.com/genexsus-ai/genxai-framework/blob/main/docs/DOCS_INDEX.md',
  },
} as const;

export type SiteConfig = typeof site;
