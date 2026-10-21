/**
 * Markdown and URL Sanitizer utility for NoX UI.
 * Prevents XSS, script injection, and javascript: URI execution.
 */

const DANGEROUS_PROTOCOLS = ['javascript:', 'data:', 'vbscript:', 'file:'];

/**
 * Sanitizes a URL, blocking dangerous protocols like javascript: and data:
 * Returns '#' if the URL is dangerous or invalid.
 */
export function sanitizeUrl(url: string | null | undefined): string {
  if (!url) return '#';
  const trimmed = url.trim();

  // Strip control characters, null bytes, and whitespace
  const normalized = trimmed
    .split('')
    .filter((c) => {
      const code = c.charCodeAt(0);
      return code >= 32 && code !== 127 && !/\s/.test(c);
    })
    .join('')
    .toLowerCase();

  for (const proto of DANGEROUS_PROTOCOLS) {
    if (normalized.startsWith(proto)) {
      return '#';
    }
  }

  // Ensure protocol is safe (http, https, mailto, relative paths, anchor)
  if (
    normalized.startsWith('http://') ||
    normalized.startsWith('https://') ||
    normalized.startsWith('mailto:') ||
    normalized.startsWith('/') ||
    normalized.startsWith('#')
  ) {
    return trimmed;
  }

  // If no scheme is present and not absolute/anchor, treat as relative or safe hash
  if (!normalized.includes(':')) {
    return trimmed;
  }

  return '#';
}

/**
 * HTML escapes raw strings so they render inertly as text.
 */
export function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export interface MarkdownToken {
  type:
    | 'text'
    | 'bold'
    | 'italic'
    | 'code'
    | 'codeblock'
    | 'link'
    | 'newline'
    | 'heading1'
    | 'heading2'
    | 'heading3'
    | 'hr'
    | 'bullet';
  content: string;
  url?: string;
  lang?: string;
}

/**
 * Tokenizes basic markdown safely without innerHTML.
 */
export function parseSafeMarkdown(text: string): MarkdownToken[] {
  if (!text) return [];

  const tokens: MarkdownToken[] = [];
  const lines = text.split('\n');

  let inCodeBlock = false;
  let codeBlockLang = '';
  let codeBlockLines: string[] = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (line.trim().startsWith('```')) {
      if (inCodeBlock) {
        tokens.push({
          type: 'codeblock',
          content: codeBlockLines.join('\n'),
          lang: codeBlockLang,
        });
        inCodeBlock = false;
        codeBlockLang = '';
        codeBlockLines = [];
      } else {
        inCodeBlock = true;
        codeBlockLang = line.trim().slice(3).trim();
        codeBlockLines = [];
      }
      continue;
    }

    if (inCodeBlock) {
      codeBlockLines.push(line);
      continue;
    }

    const trimmed = line.trim();
    if (trimmed === '---' || trimmed === '***') {
      tokens.push({ type: 'hr', content: '' });
      if (i < lines.length - 1) tokens.push({ type: 'newline', content: '\n' });
      continue;
    }

    if (line.startsWith('# ')) {
      tokens.push({ type: 'heading1', content: line.slice(2).trim() });
      if (i < lines.length - 1) tokens.push({ type: 'newline', content: '\n' });
      continue;
    }
    if (line.startsWith('## ')) {
      tokens.push({ type: 'heading2', content: line.slice(3).trim() });
      if (i < lines.length - 1) tokens.push({ type: 'newline', content: '\n' });
      continue;
    }
    if (line.startsWith('### ')) {
      tokens.push({ type: 'heading3', content: line.slice(4).trim() });
      if (i < lines.length - 1) tokens.push({ type: 'newline', content: '\n' });
      continue;
    }

    // Parse inline tokens for this line
    let pos = 0;
    if (line.startsWith('- ') || line.startsWith('* ')) {
      tokens.push({ type: 'bullet', content: '' });
      pos = 2;
    }

    while (pos < line.length) {
      // Inline link: [text](url) - supports nested parens like alert(1) or wiki/Foo_(bar)
      const linkMatch = line.slice(pos).match(/^\[([^\]]+)\]\(((?:[^()]+|\([^()]*\))+)\)/);
      if (linkMatch) {
        tokens.push({
          type: 'link',
          content: linkMatch[1],
          url: sanitizeUrl(linkMatch[2]),
        });
        pos += linkMatch[0].length;
        continue;
      }

      // Inline code: `code`
      const codeMatch = line.slice(pos).match(/^`([^`]+)`/);
      if (codeMatch) {
        tokens.push({
          type: 'code',
          content: codeMatch[1],
        });
        pos += codeMatch[0].length;
        continue;
      }

      // Bold: **text**
      const boldMatch = line.slice(pos).match(/^\*\*([^*]+)\*\*/);
      if (boldMatch) {
        tokens.push({
          type: 'bold',
          content: boldMatch[1],
        });
        pos += boldMatch[0].length;
        continue;
      }

      // Italic: *text*
      const italicMatch = line.slice(pos).match(/^\*([^*]+)\*/);
      if (italicMatch) {
        tokens.push({
          type: 'italic',
          content: italicMatch[1],
        });
        pos += italicMatch[0].length;
        continue;
      }

      // Plain text character or run
      const nextSpecial = line.slice(pos).search(/(\[|`|\*)/);
      if (nextSpecial === -1) {
        tokens.push({
          type: 'text',
          content: line.slice(pos),
        });
        pos = line.length;
      } else if (nextSpecial === 0) {
        tokens.push({
          type: 'text',
          content: line[pos],
        });
        pos++;
      } else {
        tokens.push({
          type: 'text',
          content: line.slice(pos, pos + nextSpecial),
        });
        pos += nextSpecial;
      }
    }

    if (i < lines.length - 1) {
      tokens.push({ type: 'newline', content: '\n' });
    }
  }

  if (inCodeBlock) {
    tokens.push({
      type: 'codeblock',
      content: codeBlockLines.join('\n'),
      lang: codeBlockLang,
    });
  }

  return tokens;
}
