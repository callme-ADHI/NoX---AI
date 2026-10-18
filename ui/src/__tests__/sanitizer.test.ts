import { describe, it, expect } from 'vitest';
import { sanitizeUrl, escapeHtml, parseSafeMarkdown } from '../utils/sanitizer';

describe('Markdown and URL Sanitizer', () => {
  describe('sanitizeUrl', () => {
    it('allows valid safe URLs', () => {
      expect(sanitizeUrl('https://example.com')).toBe('https://example.com');
      expect(sanitizeUrl('http://127.0.0.1:7777/api/status')).toBe('http://127.0.0.1:7777/api/status');
      expect(sanitizeUrl('/jobs')).toBe('/jobs');
      expect(sanitizeUrl('#section1')).toBe('#section1');
      expect(sanitizeUrl('mailto:admin@example.com')).toBe('mailto:admin@example.com');
    });

    it('neutralizes dangerous javascript: URIs', () => {
      expect(sanitizeUrl('javascript:alert(1)')).toBe('#');
      expect(sanitizeUrl('  javascript:alert(1)  ')).toBe('#');
      expect(sanitizeUrl('JaVaScRiPt:alert(1)')).toBe('#');
      expect(sanitizeUrl('javascript://alert(1)')).toBe('#');
    });

    it('neutralizes data: URIs and vbscript:', () => {
      expect(sanitizeUrl('data:text/html,<script>alert(1)</script>')).toBe('#');
      expect(sanitizeUrl('vbscript:msgbox(1)')).toBe('#');
      expect(sanitizeUrl('file:///etc/passwd')).toBe('#');
    });

    it('handles null, undefined, or empty strings safely', () => {
      expect(sanitizeUrl('')).toBe('#');
      expect(sanitizeUrl(null)).toBe('#');
      expect(sanitizeUrl(undefined)).toBe('#');
    });
  });

  describe('escapeHtml', () => {
    it('escapes special HTML characters', () => {
      expect(escapeHtml('<script>alert("xss")</script>')).toBe(
        '&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;'
      );
      expect(escapeHtml("<img src='x' onerror='alert(1)'>")).toBe(
        '&lt;img src=&#39;x&#39; onerror=&#39;alert(1)&#39;&gt;'
      );
      expect(escapeHtml('Tom & Jerry')).toBe('Tom &amp; Jerry');
    });
  });

  describe('parseSafeMarkdown', () => {
    it('parses bold, italic, and inline code', () => {
      const tokens = parseSafeMarkdown('Here is **bold** and *italic* and `code`.');
      expect(tokens).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ type: 'bold', content: 'bold' }),
          expect.objectContaining({ type: 'italic', content: 'italic' }),
          expect.objectContaining({ type: 'code', content: 'code' }),
        ])
      );
    });

    it('parses safe markdown links', () => {
      const tokens = parseSafeMarkdown('[Open NoX](https://noxai.dev)');
      expect(tokens).toEqual([
        {
          type: 'link',
          content: 'Open NoX',
          url: 'https://noxai.dev',
        },
      ]);
    });

    it('sanitizes dangerous links so javascript: becomes #', () => {
      const tokens = parseSafeMarkdown('[Malicious Click](javascript:alert(1))');
      expect(tokens).toEqual([
        {
          type: 'link',
          content: 'Malicious Click',
          url: '#',
        },
      ]);
    });

    it('renders script and img tags inertly as text tokens', () => {
      const tokens = parseSafeMarkdown('<script>alert(1)</script> and <img src=x onerror=alert(1)>');
      // Should all be text tokens, never an executable element
      for (const token of tokens) {
        expect(token.type).toBe('text');
      }
      expect(tokens.map((t) => t.content).join('')).toBe(
        '<script>alert(1)</script> and <img src=x onerror=alert(1)>'
      );
    });
  });
});
