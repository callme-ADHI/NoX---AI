import React, { useEffect, useState } from 'react';
import {
  Newspaper,
  ThumbsUp,
  ThumbsDown,
  Play,
  Calendar,
  Loader2,
  Copy,
  Check,
  ExternalLink,
  Sparkles,
  ShieldCheck,
  Send,
} from 'lucide-react';
import { Briefing } from '../types';
import { api } from '../services/api';
import { SafeMarkdown } from '../components/SafeMarkdown';

const FEEDBACK_TAGS = [
  'Actionable',
  'Too generic',
  'Irrelevant topic',
  'High priority',
  'Already knew this',
  'Great sources',
];

export const BriefingsPage: React.FC = () => {
  const [briefings, setBriefings] = useState<Briefing[]>([]);
  const [selectedBriefing, setSelectedBriefing] = useState<Briefing | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [copied, setCopied] = useState(false);
  const [selectedSlot, setSelectedSlot] = useState<'morning' | 'evening'>('morning');

  // Granular feedback state
  const [showFeedbackModal, setShowFeedbackModal] = useState(false);
  const [feedbackRating, setFeedbackRating] = useState<'like' | 'dislike'>('like');
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [feedbackReason, setFeedbackReason] = useState('');
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false);

  useEffect(() => {
    loadBriefings();
  }, []);

  const loadBriefings = async () => {
    setLoading(true);
    try {
      const data = await api.getBriefings();
      setBriefings(data);
      if (data.length > 0) {
        setSelectedBriefing(data[0]);
      }
    } catch (err) {
      console.error('Failed to load briefings:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleRunSlot = async (slot: 'morning' | 'evening') => {
    setRunning(true);
    try {
      await api.runBriefingSlot(slot, true);
      setTimeout(loadBriefings, 4000);
    } catch (err) {
      console.error('Failed to trigger briefing:', err);
    } finally {
      setRunning(false);
    }
  };

  const openFeedback = (rating: 'like' | 'dislike') => {
    setFeedbackRating(rating);
    setShowFeedbackModal(true);
    setSelectedTags([]);
    setFeedbackReason('');
  };

  const toggleTag = (tag: string) => {
    setSelectedTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]
    );
  };

  const handleSubmitFeedback = async () => {
    if (!selectedBriefing) return;
    try {
      await api.submitDetailedBriefingFeedback(String(selectedBriefing.id), {
        rating: feedbackRating,
        tags: selectedTags,
        reason_text: feedbackReason || undefined,
      });
      // Also update standard rating
      const numericRating = feedbackRating === 'like' ? 1 : -1;
      await api.submitBriefingFeedback(selectedBriefing.id, numericRating, feedbackReason || undefined);

      setBriefings((prev) =>
        prev.map((b) => (b.id === selectedBriefing.id ? { ...b, feedback: numericRating } : b))
      );
      setSelectedBriefing((prev) =>
        prev ? { ...prev, feedback: numericRating } : null
      );
      setFeedbackSubmitted(true);
      setTimeout(() => {
        setFeedbackSubmitted(false);
        setShowFeedbackModal(false);
      }, 1500);
    } catch (err) {
      console.error('Failed to submit feedback:', err);
    }
  };

  // Helper to extract TL;DR from markdown
  const extractTldr = (md: string) => {
    const match = md.match(/##\s+Executive Summary \(TL;DR\)\s*\n+([\s\S]*?)(?=\n##|\Z)/i);
    return match ? match[1].trim() : null;
  };

  const tldrContent = selectedBriefing ? extractTldr(selectedBriefing.markdown) : null;

  return (
    <div className="flex-1 flex overflow-hidden bg-background">
      {/* Sidebar: Archive list */}
      <aside className="w-72 bg-surface/80 backdrop-blur-md border-r border-border flex flex-col shrink-0">
        <div className="p-4 border-b border-border flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Newspaper className="w-4 h-4 text-accent" />
              <h2 className="text-sm font-semibold text-text">Briefings Archive</h2>
            </div>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-raised text-muted font-mono">
              Asia/Kolkata
            </span>
          </div>

          <div className="flex items-center gap-1.5">
            <button
              onClick={() => {
                setSelectedSlot('morning');
                handleRunSlot('morning');
              }}
              disabled={running}
              className={`flex-1 flex items-center justify-center gap-1 px-2 py-1.5 rounded-lg text-[11px] font-medium border transition-colors ${
                selectedSlot === 'morning'
                  ? 'bg-accent/15 text-accent border-accent/30'
                  : 'bg-raised/50 text-muted border-border hover:text-text'
              }`}
            >
              {running && selectedSlot === 'morning' ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <Play className="w-3 h-3 fill-current" />
              )}
              Morning 04:00
            </button>

            <button
              onClick={() => {
                setSelectedSlot('evening');
                handleRunSlot('evening');
              }}
              disabled={running}
              className={`flex-1 flex items-center justify-center gap-1 px-2 py-1.5 rounded-lg text-[11px] font-medium border transition-colors ${
                selectedSlot === 'evening'
                  ? 'bg-accent/15 text-accent border-accent/30'
                  : 'bg-raised/50 text-muted border-border hover:text-text'
              }`}
            >
              {running && selectedSlot === 'evening' ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <Play className="w-3 h-3 fill-current" />
              )}
              Evening 18:00
            </button>
          </div>
        </div>

        <div
          tabIndex={0}
          aria-label="Briefings archive list"
          className="flex-1 overflow-y-auto p-2 space-y-1 focus:outline-none"
        >
          {loading ? (
            <div className="p-4 text-center text-xs text-muted">
              Loading archive...
            </div>
          ) : briefings.length === 0 ? (
            <div className="p-4 text-center text-xs text-muted">
              No briefings delivered yet. Click a slot above to generate one.
            </div>
          ) : (
            briefings.map((b) => {
              const isSelected = selectedBriefing?.id === b.id;
              return (
                <div
                  key={b.id}
                  onClick={() => setSelectedBriefing(b)}
                  className={`p-3 rounded-lg cursor-pointer text-xs transition-colors flex flex-col gap-1 ${
                    isSelected
                      ? 'bg-raised text-text border border-border shadow-sm'
                      : 'text-muted hover:text-text hover:bg-raised/40'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-text truncate max-w-[170px]">
                      {b.title || 'Briefing Report'}
                    </span>
                    {b.feedback && (
                      <span className="text-[10px] text-accent">
                        {b.feedback === 1 ? '👍' : '👎'}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1 text-[11px] text-muted">
                    <Calendar className="w-3 h-3" />
                    <span>{b.created_at ? new Date(b.created_at).toLocaleString() : 'Today'}</span>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </aside>

      {/* Main Content Area: Briefing Display */}
      <main className="flex-1 overflow-y-auto p-6 md:p-8 flex flex-col max-w-4xl mx-auto">
        {selectedBriefing ? (
          <div className="flex flex-col gap-6">
            {/* Briefing Header Card */}
            <div className="p-6 rounded-xl bg-surface/80 backdrop-blur-md border border-border shadow-sm flex flex-col gap-4">
              <div className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-accent/15 text-accent border border-accent/30 uppercase tracking-wider font-mono">
                      Verified Briefing
                    </span>
                    <span className="text-xs text-muted">
                      {new Date(selectedBriefing.created_at).toLocaleDateString(undefined, {
                        weekday: 'long',
                        year: 'numeric',
                        month: 'long',
                        day: 'numeric',
                      })}
                    </span>
                  </div>
                  <h1 className="text-xl font-bold text-text">
                    {selectedBriefing.title || 'Cybersecurity & Tech Briefing'}
                  </h1>
                </div>

                {/* Actions & Feedback controls */}
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(selectedBriefing.markdown);
                      setCopied(true);
                      setTimeout(() => setCopied(false), 2000);
                    }}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border bg-raised border-border text-muted hover:text-text text-xs transition-colors"
                    title="Copy briefing to clipboard"
                  >
                    {copied ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
                    <span>{copied ? 'Copied' : 'Copy'}</span>
                  </button>
                  <button
                    onClick={() => openFeedback('like')}
                    className={`flex items-center gap-1 px-3 py-1.5 rounded-lg border text-xs transition-colors ${
                      selectedBriefing.feedback === 1
                        ? 'bg-success/20 border-success/40 text-success'
                        : 'bg-raised border-border text-muted hover:text-text'
                    }`}
                    title="Thumbs up & tune prompt"
                  >
                    <ThumbsUp className="w-3.5 h-3.5" />
                    <span>Relevant</span>
                  </button>
                  <button
                    onClick={() => openFeedback('dislike')}
                    className={`flex items-center gap-1 px-3 py-1.5 rounded-lg border text-xs transition-colors ${
                      selectedBriefing.feedback === -1
                        ? 'bg-danger/20 border-danger/40 text-danger'
                        : 'bg-raised border-border text-muted hover:text-text'
                    }`}
                    title="Thumbs down & tune prompt"
                  >
                    <ThumbsDown className="w-3.5 h-3.5" />
                    <span>Not Relevant</span>
                  </button>
                </div>
              </div>

              {/* Granular Feedback Drawer / Modal */}
              {showFeedbackModal && (
                <div className="mt-3 p-4 rounded-lg bg-raised/60 border border-border flex flex-col gap-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-text flex items-center gap-1.5">
                      <Sparkles className="w-3.5 h-3.5 text-accent" />
                      {feedbackRating === 'like' ? 'What worked well?' : 'Why was this not relevant?'}
                      <span className="text-[10px] text-muted font-normal">
                        (Tunes self-rewriting prompt weights)
                      </span>
                    </span>
                    <button
                      onClick={() => setShowFeedbackModal(false)}
                      className="text-xs text-muted hover:text-text"
                    >
                      Cancel
                    </button>
                  </div>

                  <div className="flex flex-wrap gap-1.5">
                    {FEEDBACK_TAGS.map((tag) => (
                      <button
                        key={tag}
                        onClick={() => toggleTag(tag)}
                        className={`px-2.5 py-1 rounded-full text-[11px] font-medium border transition-colors ${
                          selectedTags.includes(tag)
                            ? 'bg-accent text-black border-accent'
                            : 'bg-surface text-muted border-border hover:text-text'
                        }`}
                      >
                        {tag}
                      </button>
                    ))}
                  </div>

                  <div className="flex gap-2">
                    <input
                      type="text"
                      placeholder="Optional notes or topic refinement guidance..."
                      value={feedbackReason}
                      onChange={(e) => setFeedbackReason(e.target.value)}
                      className="flex-1 bg-surface border border-border rounded-lg px-3 py-1.5 text-xs text-text placeholder-muted focus:outline-none focus:border-accent"
                    />
                    <button
                      onClick={handleSubmitFeedback}
                      disabled={feedbackSubmitted}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent text-black font-semibold text-xs hover:bg-accent/90 transition-colors"
                    >
                      {feedbackSubmitted ? <Check className="w-3.5 h-3.5" /> : <Send className="w-3.5 h-3.5" />}
                      {feedbackSubmitted ? 'Saved' : 'Submit'}
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* TL;DR Highlight Card if present */}
            {tldrContent && (
              <div className="p-5 rounded-xl bg-accent/5 border border-accent/25 shadow-sm flex flex-col gap-2">
                <div className="flex items-center gap-1.5 text-accent text-xs font-semibold uppercase tracking-wider font-mono">
                  <Sparkles className="w-3.5 h-3.5" />
                  Executive Summary (TL;DR)
                </div>
                <div className="text-xs text-text/90 leading-relaxed font-sans">
                  <SafeMarkdown content={tldrContent} />
                </div>
              </div>
            )}

            {/* Briefing Markdown Content */}
            <div className="p-6 md:p-8 rounded-xl bg-surface/80 backdrop-blur-md border border-border shadow-sm leading-relaxed text-xs text-text font-sans">
              <SafeMarkdown content={selectedBriefing.markdown} />
            </div>

            {/* Verified Sources Index */}
            {Array.isArray(selectedBriefing.sources) && selectedBriefing.sources.length > 0 && (
              <div className="p-5 rounded-xl bg-surface/70 backdrop-blur-md border border-border shadow-sm flex flex-col gap-3">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="w-4 h-4 text-success" />
                  <h3 className="text-xs font-semibold text-text uppercase tracking-wider">
                    Anti-Hallucination Source Index ({selectedBriefing.sources.length} Verified)
                  </h3>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
                  {selectedBriefing.sources.map((src: any, idx: number) => {
                    const url = typeof src === 'string' ? src : src.url || '';
                    const title = typeof src === 'object' ? src.title || url : url;
                    return (
                      <a
                        key={idx}
                        href={url}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center justify-between p-2.5 rounded-lg bg-raised/40 hover:bg-raised/70 border border-border/50 text-text transition-colors group"
                      >
                        <span className="truncate pr-2 text-muted group-hover:text-text font-mono text-[11px]">
                          {title}
                        </span>
                        <ExternalLink className="w-3.5 h-3.5 text-muted group-hover:text-accent shrink-0" />
                      </a>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="h-full flex flex-col items-center justify-center text-center text-muted">
            <Newspaper className="w-12 h-12 mb-3 text-border" />
            <p className="text-sm">Select a briefing or generate a new one.</p>
          </div>
        )}
      </main>
    </div>
  );
};
