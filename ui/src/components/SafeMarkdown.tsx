import React from 'react';
import { parseSafeMarkdown } from '../utils/sanitizer';

interface SafeMarkdownProps {
  content: string;
  className?: string;
}

export const SafeMarkdown: React.FC<SafeMarkdownProps> = ({ content, className = '' }) => {
  const cleanedContent = (content || '')
    .replace(/<tool_call>[\s\S]*?(?:<\/tool_call>|$)/gi, '')
    .replace(/<function=[a-zA-Z0-9_\-]+>[\s\S]*?(?:<\/function>|$)/gi, '')
    .replace(/<parameter=[a-zA-Z0-9_\-]+>[\s\S]*?(?:<\/parameter>|$)/gi, '')
    .replace(/<\/?(?:tool_call|function|parameter)[^>]*>/gi, '')
    .trim();

  const tokens = parseSafeMarkdown(cleanedContent);

  return (
    <div className={`whitespace-pre-wrap font-sans ${className}`}>
      {tokens.map((token, index) => {
        switch (token.type) {
          case 'bold':
            return <strong key={index} className="font-semibold text-text">{token.content}</strong>;
          case 'italic':
            return <em key={index} className="italic text-text">{token.content}</em>;
          case 'code':
            return (
              <code key={index} className="px-1.5 py-0.5 rounded bg-raised border border-border/80 font-mono text-[11px] text-accent">
                {token.content}
              </code>
            );
          case 'codeblock':
            return (
              <pre key={index} className="my-2 p-3 rounded-lg bg-surface border border-border/80 font-mono text-[11px] text-text overflow-x-auto leading-relaxed">
                {token.lang && (
                  <div className="text-[10px] text-muted uppercase font-bold mb-1 border-b border-border/50 pb-0.5">
                    {token.lang}
                  </div>
                )}
                <code>{token.content}</code>
              </pre>
            );
          case 'link':
            return (
              <a
                key={index}
                href={token.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-accent underline hover:opacity-80 transition-opacity"
              >
                {token.content}
              </a>
            );
          case 'heading1':
            return (
              <h1 key={index} className="text-base font-bold text-text mt-3 mb-1.5 pb-1 border-b border-border/60">
                {token.content}
              </h1>
            );
          case 'heading2':
            return (
              <h2 key={index} className="text-xs font-bold text-accent uppercase tracking-wider mt-3 mb-1">
                {token.content}
              </h2>
            );
          case 'heading3':
            return (
              <h3 key={index} className="text-xs font-semibold text-text mt-2 mb-0.5">
                {token.content}
              </h3>
            );
          case 'hr':
            return <hr key={index} className="my-3 border-border/60" />;
          case 'bullet':
            return (
              <span key={index} className="inline-block mr-1.5 text-accent font-bold">
                •
              </span>
            );
          case 'newline':
            return '\n';
          case 'text':
          default:
            return token.content;
        }
      })}
    </div>
  );
};
