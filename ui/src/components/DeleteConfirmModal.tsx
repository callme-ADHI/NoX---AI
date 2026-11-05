import React from 'react';
import { AlertTriangle, X } from 'lucide-react';

export interface DeleteConfirmModalProps {
  isOpen: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  isDangerous?: boolean;
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export const DeleteConfirmModal: React.FC<DeleteConfirmModalProps> = ({
  isOpen,
  title,
  message,
  confirmLabel = 'Delete',
  isDangerous = true,
  loading = false,
  onConfirm,
  onCancel,
}) => {
  if (!isOpen) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in duration-100"
    >
      <div className="w-full max-w-md bg-surface border border-border rounded-xl p-5 shadow-2xl flex flex-col gap-4 text-xs animate-in zoom-in-95 duration-150">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-lg bg-danger/10 text-danger border border-danger/20">
              <AlertTriangle className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-text">{title}</h3>
              <p className="text-[11px] text-muted mt-0.5">Please confirm this action</p>
            </div>
          </div>
          <button
            onClick={onCancel}
            disabled={loading}
            className="p-1 rounded text-muted hover:text-text hover:bg-raised transition-colors"
            aria-label="Close dialog"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="py-2 text-text/90 leading-relaxed font-sans">
          {message}
        </div>

        <div className="flex items-center justify-end gap-2 pt-3 border-t border-border">
          <button
            type="button"
            onClick={onCancel}
            disabled={loading}
            className="px-3 py-1.5 rounded-lg bg-raised hover:bg-border text-muted hover:text-text border border-border font-medium transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={loading}
            className={`px-3.5 py-1.5 rounded-lg font-semibold text-white transition-colors flex items-center gap-1.5 ${
              isDangerous
                ? 'bg-danger hover:bg-danger/90'
                : 'bg-accent text-black hover:bg-accent/90'
            } disabled:opacity-50`}
          >
            {loading ? 'Processing...' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
};
