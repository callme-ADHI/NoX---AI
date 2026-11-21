import React, { useEffect, useState } from 'react';
import {
  Mail,
  RefreshCw,
  Trash2,
  Reply,
  Clock,
  User,
  Copy,
  Check,
  X,
  ShieldAlert,
} from 'lucide-react';
import { api } from '../services/api';
import { MailItem } from '../types';
import { UndoToast } from '../components/UndoToast';

interface DraftReplyData {
  mailId: string;
  to: string;
  subject: string;
  body: string;
}

export const MailPage: React.FC = () => {
  const [mails, setMails] = useState<MailItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [activeCategory, setActiveCategory] = useState<string>('all');
  const [unreadOnly, setUnreadOnly] = useState<boolean>(false);
  const [draftingId, setDraftingId] = useState<string | null>(null);
  const [activeDraft, setActiveDraft] = useState<DraftReplyData | null>(null);
  const [copied, setCopied] = useState<boolean>(false);
  const [undoItem, setUndoItem] = useState<{ id: string; subject: string } | null>(null);

  const categories = [
    { id: 'all', label: 'All Mail' },
    { id: 'primary', label: 'Primary' },
    { id: 'updates', label: 'Updates' },
    { id: 'social', label: 'Social' },
    { id: 'promotions', label: 'Promotions' },
    { id: 'phishing', label: 'Suspected Phishing' },
  ];

  const loadMail = async () => {
    try {
      setLoading(true);
      const data = await api.getMail({
        category: activeCategory === 'all' ? undefined : activeCategory,
        unread_only: unreadOnly,
      });
      setMails(data);
    } catch (err) {
      console.error('Failed to load mail:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadMail();
  }, [activeCategory, unreadOnly]);

  const handleDelete = async (mail: MailItem) => {
    try {
      await api.deleteMail(mail.id);
      setMails((prev) => prev.filter((m) => m.id !== mail.id));
      setUndoItem({ id: mail.id, subject: mail.subject });
    } catch (err) {
      console.error('Failed to delete mail:', err);
    }
  };

  const handleUndo = async () => {
    if (!undoItem) return;
    try {
      await api.restoreMail(undoItem.id);
      setUndoItem(null);
      await loadMail();
    } catch (err) {
      console.error('Failed to restore mail:', err);
    }
  };

  const handleDraftReply = async (mail: MailItem) => {
    try {
      setDraftingId(mail.id);
      const res = await api.draftMailReply(mail.id);
      setActiveDraft({
        mailId: mail.id,
        to: res.to,
        subject: res.subject,
        body: res.draft_body,
      });
    } catch (err) {
      console.error('Failed to draft reply:', err);
    } finally {
      setDraftingId(null);
    }
  };

  const handleCopyDraft = () => {
    if (!activeDraft) return;
    navigator.clipboard.writeText(activeDraft.body);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div
      className="flex-1 flex flex-col p-6 md:p-8 bg-background overflow-y-auto max-w-5xl mx-auto w-full relative"
      tabIndex={0}
      aria-label="Mail inbox panel"
      role="region"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-6 pb-4 border-b border-border/70">
        <div>
          <h1 className="text-xl font-bold text-text flex items-center gap-2">
            <Mail className="w-5 h-5 text-accent" />
            Unified Mail & Triage
          </h1>
          <p className="text-xs text-muted mt-1">
            Categorized inbox with automated phishing detection, AI draft replies, and soft-delete protection.
          </p>
        </div>

        <button
          onClick={loadMail}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-raised hover:bg-border text-muted hover:text-text border border-border text-xs transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Category Tabs & Filter Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
        <div className="flex flex-wrap items-center gap-1.5 bg-surface p-1 rounded-xl border border-border">
          {categories.map((cat) => (
            <button
              key={cat.id}
              onClick={() => setActiveCategory(cat.id)}
              className={`px-3 py-1 rounded-lg text-xs font-medium transition-all ${
                activeCategory === cat.id
                  ? 'bg-accent text-black shadow-sm font-semibold'
                  : 'text-muted hover:text-text hover:bg-white/[0.04]'
              }`}
            >
              {cat.label}
            </button>
          ))}
        </div>

        <label className="flex items-center gap-2 text-xs text-muted cursor-pointer select-none">
          <input
            type="checkbox"
            checked={unreadOnly}
            onChange={(e) => setUnreadOnly(e.target.checked)}
            className="rounded border-border bg-surface text-accent focus:ring-0"
          />
          <span>Unread only</span>
        </label>
      </div>

      {/* Email List */}
      <div className="space-y-3 mb-8">
        {loading && mails.length === 0 ? (
          <div className="p-8 text-center text-muted text-xs flex items-center justify-center gap-2">
            <RefreshCw className="w-4 h-4 animate-spin text-accent" />
            Loading messages...
          </div>
        ) : mails.length === 0 ? (
          <div className="p-12 text-center rounded-xl bg-surface/50 border border-border text-muted text-xs">
            <Mail className="w-8 h-8 text-muted/50 mx-auto mb-2" />
            No emails found in this category.
          </div>
        ) : (
          mails.map((mail) => (
            <div
              key={mail.id}
              className={`p-4 rounded-xl border transition-all ${
                mail.is_phishing
                  ? 'bg-danger/[0.03] border-danger/30 hover:border-danger/50'
                  : 'bg-surface border-border hover:border-white/20'
              }`}
            >
              {/* Phishing Banner */}
              {mail.is_phishing && (
                <div className="mb-3 px-3 py-1.5 rounded-lg bg-danger/15 border border-danger/30 text-danger text-[11px] font-medium flex items-center gap-2">
                  <ShieldAlert className="w-4 h-4 shrink-0" />
                  <span>SUSPECTED PHISHING: Detected suspicious keywords or spoofed headers. Handle with caution.</span>
                </div>
              )}

              <div className="flex items-start justify-between gap-4 mb-2">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <span className="font-bold text-sm text-text truncate">
                      {mail.subject || '(No Subject)'}
                    </span>
                    <span className="px-2 py-0.5 rounded text-[10px] font-mono uppercase bg-white/[0.05] border border-white/10 text-muted">
                      {mail.category}
                    </span>
                    <span className="text-[10px] text-muted/70 font-mono">
                      via {mail.connector_id}
                    </span>
                  </div>

                  <div className="flex items-center gap-3 text-xs text-muted mb-2">
                    <span className="flex items-center gap-1">
                      <User className="w-3 h-3 text-accent" />
                      <span className="font-medium text-text/80">{mail.sender}</span>
                    </span>
                    <span className="flex items-center gap-1">
                      <Clock className="w-3 h-3" />
                      <span>{new Date(mail.date).toLocaleString()}</span>
                    </span>
                  </div>

                  <p className="text-xs text-text/70 line-clamp-2 leading-relaxed font-sans">
                    {mail.snippet || '(No preview snippet available)'}
                  </p>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1.5 shrink-0">
                  <button
                    onClick={() => handleDraftReply(mail)}
                    disabled={draftingId === mail.id}
                    className="p-1.5 rounded-lg bg-white/[0.06] hover:bg-white/[0.12] text-text hover:text-white border border-white/10 text-xs transition-colors flex items-center gap-1"
                    title="Generate Draft Reply"
                  >
                    <Reply className={`w-3.5 h-3.5 ${draftingId === mail.id ? 'animate-spin' : ''}`} />
                    <span className="hidden sm:inline">Draft Reply</span>
                  </button>

                  <button
                    onClick={() => handleDelete(mail)}
                    className="p-1.5 rounded-lg text-muted hover:text-danger hover:bg-danger/10 border border-transparent hover:border-danger/20 transition-colors"
                    title="Soft Delete Email"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Draft Reply Modal / Drawer */}
      {activeDraft && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in">
          <div className="w-full max-w-xl bg-surface border border-border rounded-xl p-5 shadow-2xl flex flex-col gap-4 text-xs animate-in zoom-in-95">
            <div className="flex items-center justify-between border-b border-border/70 pb-3">
              <div className="flex items-center gap-2">
                <Reply className="w-4 h-4 text-accent" />
                <h3 className="text-sm font-bold text-text">AI Draft Reply</h3>
              </div>
              <button
                onClick={() => setActiveDraft(null)}
                className="p-1 rounded text-muted hover:text-text hover:bg-raised transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-2 text-xs">
              <div className="flex items-center gap-2">
                <span className="text-muted font-mono w-16">To:</span>
                <span className="text-text font-medium">{activeDraft.to}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-muted font-mono w-16">Subject:</span>
                <span className="text-text font-medium">{activeDraft.subject}</span>
              </div>
            </div>

            <div className="p-3 rounded-lg bg-black/40 border border-border/60 text-xs text-text/90 whitespace-pre-wrap font-sans leading-relaxed max-h-60 overflow-y-auto">
              {activeDraft.body}
            </div>

            <div className="flex items-center justify-between pt-3 border-t border-border">
              <span className="text-[11px] text-muted">
                Requires user approval prior to outgoing SMTP transmission.
              </span>
              <div className="flex items-center gap-2">
                <button
                  onClick={handleCopyDraft}
                  className="px-3 py-1.5 rounded-lg bg-accent text-black font-semibold text-xs hover:bg-accent/90 transition-colors flex items-center gap-1.5 shadow-sm"
                >
                  {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                  {copied ? 'Copied' : 'Copy Draft'}
                </button>
                <button
                  onClick={() => setActiveDraft(null)}
                  className="px-3 py-1.5 rounded-lg bg-raised hover:bg-border text-muted hover:text-text border border-border text-xs transition-colors"
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Undo Toast */}
      {undoItem && (
        <div className="fixed bottom-6 right-6 z-40">
          <UndoToast
            id={undoItem.id}
            message={`Deleted "${undoItem.subject || 'email'}"`}
            durationSeconds={10}
            onUndo={handleUndo}
            onDismiss={() => setUndoItem(null)}
          />
        </div>
      )}
    </div>
  );
};
