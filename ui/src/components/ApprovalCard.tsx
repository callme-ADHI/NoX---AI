import React, { useEffect, useState } from 'react';
import {
  ShieldAlert,
  ShieldCheck,
  AlertTriangle,
  Clock,
  Check,
  X,
  Lock,
} from 'lucide-react';
import { ApprovalRequest } from '../types';

interface ApprovalCardProps {
  approval: ApprovalRequest;
  onDecision: (id: number, decision: 'approve' | 'deny') => void;
}

export const ApprovalCard: React.FC<ApprovalCardProps> = ({
  approval,
  onDecision,
}) => {
  const [typedConfirm, setTypedConfirm] = useState('');
  const [timeLeft, setTimeLeft] = useState(300); // 300s approval timeout per spec §5
  const isDangerous = approval.tier === 'dangerous';

  useEffect(() => {
    const timer = setInterval(() => {
      setTimeLeft((prev) => {
        if (prev <= 1) {
          clearInterval(timer);
          onDecision(approval.id, 'deny');
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [approval.id, onDecision]);

  const canApprove = !isDangerous || typedConfirm.trim().toLowerCase() === 'confirm';

  return (
    <div
      className={`p-4 rounded-xl border ${
        isDangerous
          ? 'bg-danger/5 border-danger/40'
          : 'bg-warning/5 border-warning/40'
      } my-3 shadow-lg select-text`}
    >
      {/* Header */}
      <div className="flex items-center justify-between pb-3 border-b border-border/50">
        <div className="flex items-center gap-2">
          {isDangerous ? (
            <ShieldAlert className="w-5 h-5 text-danger shrink-0" />
          ) : (
            <ShieldCheck className="w-5 h-5 text-warning shrink-0" />
          )}
          <div>
            <h4 className="text-sm font-semibold text-text flex items-center gap-2">
              Action Approval Required
              <span
                className={`text-[10px] font-mono uppercase px-2 py-0.5 rounded font-bold ${
                  isDangerous
                    ? 'bg-danger/20 text-danger border border-danger/30'
                    : 'bg-warning/20 text-warning border border-warning/30'
                }`}
              >
                {approval.tier} tier
              </span>
            </h4>
          </div>
        </div>

        {/* Timeout counter */}
        <div className="flex items-center gap-1.5 text-xs font-mono text-muted">
          <Clock className="w-3.5 h-3.5" />
          <span>
            {Math.floor(timeLeft / 60)}:
            {(timeLeft % 60).toString().padStart(2, '0')}
          </span>
        </div>
      </div>

      {/* Untrusted taint warning */}
      {approval.untrusted_warning && (
        <div className="mt-3 p-2.5 rounded-lg bg-danger/10 border border-danger/30 flex items-start gap-2 text-xs text-danger">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <div>
            <span className="font-semibold">Security Warning: </span>
            This run previously inspected untrusted external web content. Confirm
            that the tool execution below aligns with your original intent.
          </div>
        </div>
      )}

      {/* Tool & Arguments */}
      <div className="mt-3 text-xs flex flex-col gap-1.5">
        <div>
          <span className="text-muted">Target Tool: </span>
          <span className="font-mono font-medium text-text bg-raised px-1.5 py-0.5 rounded border border-border">
            {approval.tool_name}
          </span>
        </div>

        {approval.reason && (
          <div>
            <span className="text-muted">Reason: </span>
            <span className="text-text">{approval.reason}</span>
          </div>
        )}

        <div className="mt-1">
          <span className="text-muted block mb-1">Exact Arguments:</span>
          <pre className="p-2.5 rounded-lg bg-background border border-border font-mono text-[11px] overflow-x-auto text-text leading-relaxed">
            {JSON.stringify(approval.arguments, null, 2)}
          </pre>
        </div>
      </div>

      {/* Dangerous typed confirmation */}
      {isDangerous && (
        <div className="mt-3 p-2.5 bg-background border border-danger/30 rounded-lg">
          <label className="block text-[11px] text-danger font-medium mb-1.5">
            Type <span className="font-mono font-bold uppercase underline">confirm</span> below to approve this dangerous action:
          </label>
          <input
            type="text"
            value={typedConfirm}
            onChange={(e) => setTypedConfirm(e.target.value)}
            placeholder="Type 'confirm'..."
            className="w-full bg-surface border border-border rounded px-2.5 py-1.5 text-xs text-text placeholder:text-muted focus:outline-none focus:border-danger font-mono"
          />
        </div>
      )}

      {/* Action buttons */}
      <div className="mt-4 flex items-center justify-end gap-2.5 flex-wrap">
        <button
          onClick={() => onDecision(approval.id, 'deny')}
          className="px-3 py-1.5 rounded-lg bg-raised hover:bg-border text-muted hover:text-text border border-border text-xs font-medium flex items-center gap-1.5 transition-colors"
        >
          <X className="w-3.5 h-3.5" />
          Deny
        </button>

        {!isDangerous && (
          <button
            onClick={() => {
              sessionStorage.setItem(`nox_allow_session_${approval.tool_name}`, 'true');
              onDecision(approval.id, 'approve');
            }}
            title="Automatically approve this tool for the rest of this browser session"
            className="px-3 py-1.5 rounded-lg bg-surface hover:bg-raised text-muted hover:text-text border border-border text-xs font-medium transition-colors"
          >
            Always Allow for Session
          </button>
        )}

        <button
          disabled={!canApprove}
          onClick={() => onDecision(approval.id, 'approve')}
          className={`px-4 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-colors shadow-sm ${
            canApprove
              ? isDangerous
                ? 'bg-danger text-white hover:bg-danger/90'
                : 'bg-accent text-black hover:bg-accent/90'
              : 'bg-raised text-muted border border-border cursor-not-allowed opacity-50'
          }`}
        >
          {isDangerous ? <Lock className="w-3.5 h-3.5" /> : <Check className="w-3.5 h-3.5" />}
          Approve Action
        </button>
      </div>
    </div>
  );
};
