import { Anchor, Code, Text, Typography } from '@mantine/core';
import ReactMarkdown, { type Components, defaultUrlTransform } from 'react-markdown';

/**
 * Renders AI or rule markdown as untrusted text.
 *
 * - `skipHtml`: raw HTML in the source is dropped (no rehype-raw, ever).
 * - Links keep only safe protocols (react-markdown's default transform), open external targets in a new tab and always
 *   carry rel="noreferrer".
 * - Images are not loaded (no remote requests from AI text); their alt text is shown instead.
 * - `{{f:<fact_key>}}` tokens are filled from `facts` (the finding's evidence), so numbers in prose come from facts.
 */
export interface MarkdownProps {
  children: string | null | undefined;
  facts?: Record<string, unknown>;
  size?: 'xs' | 'sm' | 'md';
}

const TOKEN_RE = /\{\{f:([a-zA-Z0-9_.:-]{1,120})\}\}/g;

function escapeMarkdown(value: string): string {
  return value.replace(/[\\`*_{}[\]()#+\-.!|<>~]/g, (ch) => `\\${ch}`);
}

export function fillFactTokens(source: string, facts: Record<string, unknown> | undefined): string {
  return source.replace(TOKEN_RE, (_, key: string) => {
    if (!facts || !(key in facts)) return escapeMarkdown(`[missing fact ${key}]`);
    const value = facts[key];
    const text = typeof value === 'number' ? value.toLocaleString('en-GB') : value === null ? '–' : String(value);
    return escapeMarkdown(text);
  });
}

const components: Components = {
  a: ({ href, children }) => {
    const external = typeof href === 'string' && /^(https?:|mailto:)/i.test(href);
    return (
      <Anchor href={href} target={external ? '_blank' : undefined} rel="noreferrer" inherit>
        {children}
      </Anchor>
    );
  },
  img: ({ alt }) => (
    <Text span c="dimmed" inherit>
      [image{alt ? `: ${alt}` : ''}]
    </Text>
  ),
  code: ({ children }) => <Code>{children}</Code>,
};

export function Markdown({ children, facts, size = 'sm' }: MarkdownProps) {
  if (!children) return null;
  return (
    <Typography fz={size} style={{ overflowWrap: 'anywhere' }}>
      <ReactMarkdown skipHtml components={components} urlTransform={defaultUrlTransform}>
        {fillFactTokens(children, facts)}
      </ReactMarkdown>
    </Typography>
  );
}
