import React, { useState } from 'react';
import {
  Wrench,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  AlertCircle,
  Copy,
  Check,
} from 'lucide-react';
import { ToolCallItem } from '../types';

interface ToolCallCardProps {
  tool: ToolCallItem;
}

export const ToolCallCard: React.FC<ToolCallCardProps> = ({ tool }) => {
  const [isExpanded, setIsExpanded] = useState(tool.expanded ?? false);
  const [copied, setCopied] = useState(false);

  const getTierBadge = () => {
    switch (tool.tier) {
      case 'dangerous':
        return 'text-danger bg-danger/10 border-danger/30';
      case 'write':
        return 'text-warning bg-warning/10 border-warning/30';
      default:
        return 'text-muted bg-raised border-border';
    }
  };

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    const content = JSON.stringify(tool.arguments, null, 2);
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="my-2 rounded-lg bg-surface border border-border/80 overflow-hidden text-xs">
      {/* Header banner */}
      <div
        className="px-3 py-2 flex items-center justify-between cursor-pointer hover:bg-raised/40 select-none transition-colors"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <div className="p-1 rounded bg-raised text-muted border border-border/50">
            <Wrench className="w-3.5 h-3.5" />
          </div>
          <span className="font-mono font-medium text-text truncate">
            {tool.name}
          </span>
          {tool.tier && (
            <span
              className={`text-[9px] font-mono uppercase px-1.5 py-0.2 rounded border ${getTierBadge()}`}
            >
              {tool.tier}
            </span>
          )}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {tool.duration_ms !== undefined && (
            <span className="text-[10px] font-mono text-muted">
              {tool.duration_ms}ms
            </span>
          )}
          {tool.ok !== undefined && (
            tool.ok ? (
              <CheckCircle2 className="w-3.5 h-3.5 text-success" />
            ) : (
              <AlertCircle className="w-3.5 h-3.5 text-danger" />
            )
          )}
          {isExpanded ? (
            <ChevronDown className="w-3.5 h-3.5 text-muted" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 text-muted" />
          )}
        </div>
      </div>

      {/* Result summary if not expanded */}
      {!isExpanded && tool.result_summary && (
        <div className="px-3 py-1.5 bg-raised/30 text-muted border-t border-border/40 font-mono text-[11px] truncate">
          {tool.result_summary}
        </div>
      )}

      {/* Expanded details */}
      {isExpanded && (
        <div className="p-3 border-t border-border/60 bg-background/50 flex flex-col gap-2.5">
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-muted font-semibold text-[11px]">Arguments:</span>
              <button
                onClick={handleCopy}
                className="text-[11px] text-muted hover:text-text flex items-center gap-1 transition-colors"
                title="Copy arguments"
              >
                {copied ? <Check className="w-3 h-3 text-success" /> : <Copy className="w-3 h-3" />}
                {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
            <pre className="p-2 rounded bg-surface border border-border font-mono text-[11px] text-text overflow-x-auto">
              {JSON.stringify(tool.arguments, null, 2)}
            </pre>
          </div>

          {tool.result_summary && (
            <div>
              <span className="text-muted font-semibold text-[11px] block mb-1">
                Result Output:
              </span>
              <pre className="p-2 rounded bg-surface border border-border font-mono text-[11px] text-text overflow-x-auto whitespace-pre-wrap max-h-60">
                {tool.result_summary}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
