import React from 'react';
import { Cloud, Cpu, Activity, Clock } from 'lucide-react';

export interface RouteChipProps {
  lane?: string;
  modelName?: string;
  isCloud?: boolean;
  latencyMs?: number;
  onOpenTrace?: () => void;
}

export const RouteChip: React.FC<RouteChipProps> = ({
  lane = 'front_desk',
  modelName,
  isCloud = false,
  latencyMs,
  onOpenTrace,
}) => {
  const displayModel = modelName || (isCloud ? 'nemotron-super-49b' : 'qwen2.5:14b');
  const formattedLane = lane.replace('_', ' ');

  return (
    <button
      type="button"
      onClick={onOpenTrace}
      title="Click to view execution trace & intent analysis"
      className="inline-flex items-center gap-2 px-2.5 py-1 rounded-full text-[10px] font-mono border transition-all duration-150 hover:shadow-sm cursor-pointer select-none group bg-surface border-border hover:border-accent/50 text-muted hover:text-text"
    >
      {/* Cloud vs Local badge */}
      <span className="flex items-center gap-1 font-semibold">
        {isCloud ? (
          <>
            <Cloud className="w-3 h-3 text-sky-400 group-hover:scale-110 transition-transform" />
            <span className="text-sky-400">Cloud</span>
          </>
        ) : (
          <>
            <Cpu className="w-3 h-3 text-accent group-hover:scale-110 transition-transform" />
            <span className="text-accent">Local</span>
          </>
        )}
      </span>

      <span className="text-border">|</span>

      {/* Lane */}
      <span className="capitalize text-text/80">{formattedLane}</span>

      <span className="text-border">|</span>

      {/* Model Name */}
      <span className="text-text font-medium truncate max-w-[120px]">{displayModel}</span>

      {/* Latency if available */}
      {typeof latencyMs === 'number' && latencyMs > 0 && (
        <>
          <span className="text-border">|</span>
          <span className="flex items-center gap-0.5 text-muted group-hover:text-text">
            <Clock className="w-2.5 h-2.5" />
            {latencyMs}ms
          </span>
        </>
      )}

      {/* Trace hint icon */}
      <Activity className="w-2.5 h-2.5 text-muted group-hover:text-accent ml-0.5" />
    </button>
  );
};
