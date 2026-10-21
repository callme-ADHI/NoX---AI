import React, { useState, useRef, useEffect } from 'react';
import { Cloud, CloudOff, Zap } from 'lucide-react';
import { CloudHealthInfo } from '../types';

export interface CloudStatusIndicatorProps {
  cloudHealth?: CloudHealthInfo;
  isCloudEnabled: boolean;
  onToggleCloud?: (enabled: boolean) => void;
}

export const CloudStatusIndicator: React.FC<CloudStatusIndicatorProps> = ({
  cloudHealth,
  isCloudEnabled,
  onToggleCloud,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const popoverRef = useRef<HTMLDivElement>(null);

  // Close popover on outside click
  useEffect(() => {
    const handleOutsideClick = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    if (isOpen) {
      document.addEventListener('mousedown', handleOutsideClick);
    }
    return () => document.removeEventListener('mousedown', handleOutsideClick);
  }, [isOpen]);

  const state = (cloudHealth?.state || (isCloudEnabled ? 'ACTIVE' : 'OFFLINE')).toUpperCase();
  const circuitBreaker = (cloudHealth?.circuit_breaker_state || 'CLOSED').toUpperCase();

  const getColor = () => {
    if (!isCloudEnabled || state === 'OFFLINE') return 'bg-gray-500';
    if (state === 'ACTIVE') return 'bg-success';
    if (state === 'NO_KEY' || state === 'INVALID_KEY') return 'bg-danger';
    if (state === 'RATE_LIMITED' || state === 'DEGRADED') return 'bg-warning';
    return 'bg-gray-500';
  };

  const getLabel = () => {
    if (!isCloudEnabled) return 'Cloud Disabled';
    if (state === 'ACTIVE') return 'Cloud Active';
    if (state === 'RATE_LIMITED') return 'Rate Limited (30 RPM)';
    if (state === 'NO_KEY') return 'No API Key';
    if (state === 'INVALID_KEY') return 'Invalid API Key';
    if (state === 'DEGRADED') return 'Degraded';
    return 'Cloud Offline';
  };

  return (
    <div className="relative inline-block" ref={popoverRef}>
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface hover:bg-raised border border-border text-xs text-text transition-colors"
        title="NVIDIA Cloud Health & Circuit Breaker Status"
        aria-label="Cloud status popover toggle"
      >
        <span className={`w-2 h-2 rounded-full ${getColor()} animate-pulse`} />
        {isCloudEnabled ? <Cloud className="w-3.5 h-3.5 text-sky-400" /> : <CloudOff className="w-3.5 h-3.5 text-muted" />}
        <span className="hidden sm:inline font-mono text-[11px] text-muted">{getLabel()}</span>
      </button>

      {isOpen && (
        <div className="absolute right-0 mt-2 w-72 p-3 bg-surface border border-border rounded-xl shadow-xl z-50 text-xs animate-in fade-in duration-150">
          <div className="flex items-center justify-between pb-2 border-b border-border/70 mb-2.5">
            <div className="flex items-center gap-1.5 font-semibold text-text">
              <Zap className="w-3.5 h-3.5 text-accent" />
              <span>Cloud Health Monitor</span>
            </div>
            <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold ${
              state === 'ACTIVE' ? 'bg-success/20 text-success' : 'bg-warning/20 text-warning'
            }`}>
              {state}
            </span>
          </div>

          <div className="space-y-2 text-[11px]">
            <div className="flex justify-between py-0.5">
              <span className="text-muted">Circuit Breaker:</span>
              <span className="font-mono font-bold text-text">{circuitBreaker}</span>
            </div>

            <div className="flex justify-between py-0.5">
              <span className="text-muted">Rate Budget:</span>
              <span className="font-mono text-text">30 RPM token bucket</span>
            </div>

            <div className="flex justify-between py-0.5">
              <span className="text-muted">Interactive Reserve:</span>
              <span className="font-mono text-accent">10 RPM protected</span>
            </div>

            {typeof cloudHealth?.cooldown_remaining_seconds === 'number' && cloudHealth.cooldown_remaining_seconds > 0 && (
              <div className="flex justify-between py-0.5 text-warning">
                <span>Cooldown Reset:</span>
                <span className="font-mono">{cloudHealth.cooldown_remaining_seconds}s</span>
              </div>
            )}

            {typeof cloudHealth?.average_latency_ms === 'number' && (
              <div className="flex justify-between py-0.5">
                <span className="text-muted">Avg Cloud Latency:</span>
                <span className="font-mono text-text">{Math.round(cloudHealth.average_latency_ms)}ms</span>
              </div>
            )}
          </div>

          <div className="pt-2.5 mt-2.5 border-t border-border flex items-center justify-between">
            <span className="text-muted text-[11px]">Allow Cloud Egress:</span>
            {onToggleCloud && (
              <button
                type="button"
                onClick={() => {
                  onToggleCloud(!isCloudEnabled);
                }}
                className={`px-2.5 py-1 rounded text-[11px] font-semibold transition-colors ${
                  isCloudEnabled
                    ? 'bg-danger/20 text-danger hover:bg-danger/30 border border-danger/30'
                    : 'bg-success/20 text-success hover:bg-success/30 border border-success/30'
                }`}
              >
                {isCloudEnabled ? 'Disable Cloud' : 'Enable Cloud'}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
