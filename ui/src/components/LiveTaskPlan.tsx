import React, { useState } from 'react';
import {
  CheckCircle2,
  Clock,
  AlertCircle,
  Loader2,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Layers,
  Sparkles,
} from 'lucide-react';
import { SubtaskNode, TaskPlan } from '../types';

interface LiveTaskPlanProps {
  plan: TaskPlan;
  onClose?: () => void;
}

export const LiveTaskPlan: React.FC<LiveTaskPlanProps> = ({ plan }) => {
  const [expandedNodes, setExpandedNodes] = useState<Record<string, boolean>>({});

  const toggleNode = (nodeId: string) => {
    setExpandedNodes((prev) => ({
      ...prev,
      [nodeId]: !prev[nodeId],
    }));
  };

  const getStatusIcon = (status: SubtaskNode['status']) => {
    switch (status) {
      case 'done':
        return <CheckCircle2 className="w-4 h-4 text-success" />;
      case 'running':
        return <Loader2 className="w-4 h-4 text-accent animate-spin" />;
      case 'failed':
        return <AlertCircle className="w-4 h-4 text-danger" />;
      case 'skipped':
        return <span className="w-4 h-4 text-muted text-xs">⊘</span>;
      default:
        return <Clock className="w-4 h-4 text-muted" />;
    }
  };

  const getKindColor = (kind: SubtaskNode['kind']) => {
    switch (kind) {
      case 'research':
        return 'text-accent border-accent/30 bg-accent/10';
      case 'fetch':
        return 'text-sky-400 border-sky-400/30 bg-sky-400/10';
      case 'compute':
        return 'text-purple-400 border-purple-400/30 bg-purple-400/10';
      case 'action':
        return 'text-amber-400 border-amber-400/30 bg-amber-400/10';
      default:
        return 'text-muted border-border bg-raised';
    }
  };

  const completedCount = plan.nodes.filter((n) => n.status === 'done').length;
  const progressPercent =
    plan.nodes.length > 0 ? Math.round((completedCount / plan.nodes.length) * 100) : 0;

  return (
    <div className="bg-surface border border-accent/30 rounded-xl overflow-hidden shadow-xl mb-4">
      {/* Plan Header */}
      <div className="p-4 bg-raised/50 border-b border-border/80 flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="p-1.5 rounded-lg bg-accent/10 text-accent border border-accent/20">
              <Sparkles className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-text flex items-center gap-2">
                Autonomous Task Plan
                <span className="text-[11px] font-mono px-2 py-0.5 rounded-full bg-raised text-muted border border-border">
                  DAG: {completedCount}/{plan.nodes.length} completed
                </span>
              </h3>
            </div>
          </div>
          <span className="text-xs font-mono font-medium text-accent">
            {progressPercent}%
          </span>
        </div>

        {/* Progress bar */}
        <div className="w-full bg-border h-1.5 rounded-full overflow-hidden">
          <div
            className="bg-accent h-full transition-all duration-300 ease-out"
            style={{ width: `${progressPercent}%` }}
          />
        </div>

        {/* Plan Goal */}
        <p className="text-xs text-muted line-clamp-1 italic">
          Goal: {plan.goal}
        </p>
      </div>

      {/* Nodes list */}
      <div className="divide-y divide-border/40 max-h-96 overflow-y-auto p-2">
        {plan.nodes.map((node, index) => {
          const isExpanded = !!expandedNodes[node.id];
          return (
            <div
              key={node.id}
              className={`rounded-lg transition-colors ${
                node.status === 'running'
                  ? 'bg-accent/5 border border-accent/20'
                  : 'hover:bg-raised/40'
              } p-2.5 my-1`}
            >
              <div
                className="flex items-center gap-2.5 cursor-pointer select-none"
                onClick={() => toggleNode(node.id)}
              >
                <div className="shrink-0">{getStatusIcon(node.status)}</div>

                <div className="flex items-center gap-2 flex-1 min-w-0">
                  <span className="text-xs font-mono text-muted">
                    #{index + 1}
                  </span>
                  <span className="text-xs font-medium text-text truncate">
                    {node.title}
                  </span>
                  <span
                    className={`text-[10px] font-mono uppercase px-1.5 py-0.5 rounded border ${getKindColor(
                      node.kind
                    )}`}
                  >
                    {node.kind}
                  </span>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  {node.duration_ms && (
                    <span className="text-[10px] font-mono text-muted">
                      {(node.duration_ms / 1000).toFixed(1)}s
                    </span>
                  )}
                  {isExpanded ? (
                    <ChevronDown className="w-4 h-4 text-muted" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-muted" />
                  )}
                </div>
              </div>

              {/* Expandable node details */}
              {isExpanded && (
                <div className="mt-2.5 pl-6 pt-2 border-t border-border/40 text-xs text-muted flex flex-col gap-2">
                  <div>
                    <span className="font-semibold text-text">Objective: </span>
                    <span>{node.objective}</span>
                  </div>

                  {node.depends_on.length > 0 && (
                    <div className="flex items-center gap-1.5 text-[11px]">
                      <Layers className="w-3 h-3" />
                      <span>Depends on: {node.depends_on.join(', ')}</span>
                    </div>
                  )}

                  {node.summary && (
                    <div className="p-2 rounded bg-raised border border-border/60 text-text font-mono text-[11px] leading-relaxed">
                      {node.summary}
                    </div>
                  )}

                  {node.sources && node.sources.length > 0 && (
                    <div className="flex flex-col gap-1 mt-1">
                      <span className="text-[11px] font-semibold text-text">
                        Sources Verified:
                      </span>
                      <div className="flex flex-wrap gap-1.5">
                        {node.sources.map((src, i) => (
                          <a
                            key={i}
                            href={src}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-[11px] text-accent hover:underline bg-raised px-2 py-0.5 rounded border border-border"
                          >
                            <ExternalLink className="w-3 h-3" />
                            <span className="max-w-[200px] truncate">{src}</span>
                          </a>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
