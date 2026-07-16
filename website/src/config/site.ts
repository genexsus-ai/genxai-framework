export const site = {
  name: 'GenXAI',
  tagline: 'Graph-native agentic AI framework',
  description:
    'GenXAI is an advanced agentic AI framework with graph-based orchestration, multi-layer memory, extensible tools, and an enterprise-grade path to production.',
  links: {
    github: 'https://github.com/genexsus-ai/genxai',
    docsIndexInRepo:
      'https://github.com/genexsus-ai/genxai/blob/main/docs/DOCS_INDEX.md',
  },
} as const;

export type SiteConfig = typeof site;
