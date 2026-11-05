import React, { useEffect, useState } from 'react';
import { RotateCcw, X } from 'lucide-react';

export interface UndoToastProps {
  id: string;
  message: string;
  durationSeconds?: number;
  onUndo: () => void;
  onDismiss: () => void;
}

export const UndoToast: React.FC<UndoToastProps> = ({
  message,
  durationSeconds = 10,
  onUndo,
  onDismiss,
}) => {
  const [remaining, setRemaining] = useState(durationSeconds);

  useEffect(() => {
    setRemaining(durationSeconds);
    const interval = setInterval(() => {
      setRemaining((prev) => {
        if (prev <= 1) {
          clearInterval(interval);
          onDismiss();
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(interval);
  }, [durationSeconds, onDismiss]);

  return (
    <div
      role="status"
      aria-live="polite"
      className="bg-surface/95 backdrop-blur-md border border-border/80 shadow-2xl rounded-xl p-3.5 flex items-center justify-between gap-4 text-xs text-text animate-in slide-in-from-bottom-2 duration-150 min-w-[280px] max-w-md"
    >
      <div className="flex-1 truncate">
        <span className="font-medium">{message}</span>
        <span className="text-[10px] text-muted ml-2 font-mono">({remaining}s)</span>
      </div>

      <div className="flex items-center gap-2 shrink-0">
        <button
          onClick={onUndo}
          className="px-2.5 py-1 rounded bg-accent text-black font-semibold text-[11px] hover:bg-accent/90 transition-colors flex items-center gap-1 shadow-sm"
        >
          <RotateCcw className="w-3 h-3" />
          Undo
        </button>
        <button
          onClick={onDismiss}
          className="p-1 rounded text-muted hover:text-text hover:bg-raised transition-colors"
          aria-label="Dismiss toast"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
};
