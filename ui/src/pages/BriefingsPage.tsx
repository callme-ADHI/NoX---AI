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
  X,
  ExternalLink,
  Sparkles,
  ShieldCheck,
  Send,
  FolderOpen,
  Trophy,
  Lightbulb,
  Clock,
  Layers,
  FileText,
} from 'lucide-react';
import { Briefing } from '../types';
import { api } from '../services/api';
import { SafeMarkdown } from '../components/SafeMarkdown';
import { isSafeHttpUrl } from '../utils/sanitizer';

const FEEDBACK_TAGS = [
  'Actionable',
  'Too generic',
  'Irrelevant topic',
  'High priority',
  'Already knew this',
  'Great sources',
];

interface BriefingItem {
  id: string;
  briefing_id: string;
  topic_id: string;
  category: string;
  title: string;
  summary: string;
  source_name: string;
  url: string;
  deadline?: string;
  prize?: string;
  created_at: string;
}

export const BriefingsPage: React.FC = () => {
  const [briefings, setBriefings] = useState<Briefing[]>([]);
  const [selectedBriefing, setSelectedBriefing] = useState<Briefing | null>(null);
  const [items, setItems] = useState<BriefingItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [activeRun, setActiveRun] = useState<{
    run_id: string;
    slot: string;
    rev: number;
    state: string;
    progress: number;
    topics_completed: number;
    topics_total: number;
  } | null>(null);
  const [nextSchedule, setNextSchedule] = useState<{
    morning: string;
    evening: string;
  }>({ morning: '04:00', evening: '18:00' });
  const [copied, setCopied] = useState(false);
  const [vaultOpening, setVaultOpening] = useState(false);
  const [vaultOpened, setVaultOpened] = useState(false);
  const [viewMode, setViewMode] = useState<'cards' | 'markdown'>('cards');

  // Granular feedback state
  const [showFeedbackModal, setShowFeedbackModal] = useState(false);
  const [feedbackRating, setFeedbackRating] = useState<'like' | 'dislike'>('like');
  const [targetTopicId, setTargetTopicId] = useState<string | undefined>();
  const [targetItemId, setTargetItemId] = useState<string | undefined>();
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [feedbackReason, setFeedbackReason] = useState('');
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false);

  useEffect(() => {
    loadBriefings();
    checkStatus();
  }, []);

  useEffect(() => {
    if (selectedBriefing) {
      loadItems(selectedBriefing.id);
    } else {
      setItems([]);
    }
  }, [selectedBriefing]);

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

  const loadItems = async (id: number) => {
    setItemsLoading(true);
    try {
      const itms = await api.getBriefingItems(id);
      setItems(itms as BriefingItem[]);
    } catch (err) {
      console.debug('Failed to load structured briefing items:', err);
      setItems([]);
    } finally {
      setItemsLoading(false);
    }
  };

  const checkStatus = async () => {
    try {
      const s = await api.getBriefingStatus();
      if (s.active && s.active_run) {
        setRunning(true);
        setActiveRun(s.active_run);
      } else {
        if (running) {
          setRunning(false);
          setActiveRun(null);
          loadBriefings();
        }
      }
      if (s.next_morning || s.next_evening) {
        setNextSchedule({
          morning: s.next_morning
            ? new Date(s.next_morning).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            : '04:00',
          evening: s.next_evening
            ? new Date(s.next_evening).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            : '18:00',
        });
      }
    } catch (err) {
      console.debug('Failed to check briefing status:', err);
    }
  };

  useEffect(() => {
    let interval: ReturnType<typeof setInterval> | null = null;
    if (running) {
      interval = setInterval(async () => {
        try {
          const s = await api.getBriefingStatus();
          if (s.active && s.active_run) {
            setActiveRun(s.active_run);
          } else {
            setRunning(false);
            setActiveRun(null);
            loadBriefings();
          }
        } catch (err) {
          console.debug('Polling briefing status error:', err);
        }
      }, 1500);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [running]);

  const handleRunBriefing = async () => {
    setRunning(true);
    try {
      const res = await api.runBriefing();
      if (res.run_id) {
        setActiveRun({
          run_id: res.run_id,
          slot: res.slot,
          rev: res.rev,
          state: 'running',
          progress: 0,
          topics_completed: 0,
          topics_total: 12,
        });
      }
    } catch (err) {
      console.error('Failed to trigger briefing:', err);
      setRunning(false);
    }
  };

  const handleCancelBriefing = async () => {
    if (!activeRun) return;
    try {
      await api.cancelBriefingRun(activeRun.run_id);
      setRunning(false);
      setActiveRun(null);
      await loadBriefings();
    } catch (err) {
      console.error('Failed to cancel briefing:', err);
    }
  };

  const openFeedback = (
    rating: 'like' | 'dislike',
    topicId?: string,
    itemId?: string
  ) => {
    setFeedbackRating(rating);
    setTargetTopicId(topicId);
    setTargetItemId(itemId);
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
        topic_id: targetTopicId,
        item_id: targetItemId,
        tags: selectedTags,
        reason_text: feedbackReason || undefined,
      });

      if (!targetItemId) {
        const numericRating = feedbackRating === 'like' ? 1 : -1;
        await api.submitBriefingFeedback(selectedBriefing.id, numericRating, feedbackReason || undefined);
        setBriefings((prev) =>
          prev.map((b) => (b.id === selectedBriefing.id ? { ...b, feedback: numericRating } : b))
        );
        setSelectedBriefing((prev) =>
          prev ? { ...prev, feedback: numericRating } : null
        );
      }

      setFeedbackSubmitted(true);
      setTimeout(() => {
        setFeedbackSubmitted(false);
        setShowFeedbackModal(false);
      }, 1400);
    } catch (err) {
      console.error('Failed to submit feedback:', err);
    }
  };

  const handleOpenVault = async () => {
    setVaultOpening(true);
    try {
      await api.openNotesVault();
      setVaultOpened(true);
      setTimeout(() => setVaultOpened(false), 2500);
    } catch (err) {
      console.error('Failed to open Notes Vault:', err);
    } finally {
      setVaultOpening(false);
    }
  };

  // Helper to extract TL;DR from markdown
  const extractTldr = (md: string) => {
    const match = md.match(/##\s+Executive (?:Summary|Radar) \(TL;DR\)\s*\n+([\s\S]*?)(?=\n##|\Z)/i);
    return match ? match[1].trim() : null;
  };

  const tldrContent = selectedBriefing ? extractTldr(selectedBriefing.markdown) : null;

  // Segment items into Categories
  const opportunities = items.filter(
    (it) => ['opportunities', 'hackathons', 'competitions', 'grants'].includes(it.category?.toLowerCase())
  );
  const ideas = items.filter(
    (it) => ['ideas', 'startup_ideas'].includes(it.category?.toLowerCase())
  );
  const top3 = items.slice(0, 3);

  // Group rest by Topic
  const topicsMap = new Map<string, BriefingItem[]>();
  items.forEach((it) => {
    const t = it.topic_id || 'General';
    const list = topicsMap.get(t) || [];
    list.push(it);
    topicsMap.set(t, list);
  });

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

          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-1.5">
              <button
                data-testid="run-briefing-button"
                aria-label={running ? 'Running briefing' : 'Run briefing'}
                onClick={handleRunBriefing}
                disabled={running}
                className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold border transition-all shadow-sm ${
                  running
                    ? 'bg-accent/20 text-accent border-accent/40 cursor-wait'
                    : 'bg-accent text-black hover:bg-accent/90 border-transparent active:scale-[0.98]'
                }`}
              >
                {running ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    <span>
                      {activeRun && activeRun.topics_total > 0
                        ? `Running (${activeRun.topics_completed}/${activeRun.topics_total})...`
                        : 'Running briefing...'}
                    </span>
                  </>
                ) : (
                  <>
                    <Play className="w-3.5 h-3.5 fill-current" />
                    <span>Run briefing</span>
                  </>
                )}
              </button>

              {running && activeRun && (
                <button
                  data-testid="cancel-briefing-button"
                  aria-label="Cancel briefing run"
                  onClick={handleCancelBriefing}
                  title="Cancel briefing run"
                  className="p-2 rounded-lg bg-red-500/10 text-red-400 border border-red-500/30 hover:bg-red-500/20 transition-colors"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>

            {running && activeRun && (
              <div className="w-full bg-raised rounded-full h-1.5 overflow-hidden border border-border">
                <div
                  className="bg-accent h-full transition-all duration-300"
                  style={{ width: `${Math.max(5, Math.min(100, (activeRun.progress || 0) * 100))}%` }}
                />
              </div>
            )}

            <div className="flex items-center justify-between text-[10px] text-muted font-mono px-0.5">
              <span>Auto schedule:</span>
              <span>{nextSchedule.morning} & {nextSchedule.evening} IST</span>
            </div>
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
      <main className="flex-1 overflow-y-auto p-6 md:p-8 flex flex-col max-w-5xl mx-auto">
        {selectedBriefing ? (
          <div className="flex flex-col gap-6">
            {/* Briefing Header Card */}
            <div className="p-6 rounded-xl bg-surface/80 backdrop-blur-md border border-border shadow-sm flex flex-col gap-4">
              <div className="flex items-start justify-between flex-wrap gap-4">
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
                    {selectedBriefing.title || 'Autonomous Technical Briefing'}
                  </h1>
                </div>

                {/* Actions & Feedback controls */}
                <div className="flex items-center gap-2 flex-wrap">
                  <div className="flex items-center rounded-lg border border-border bg-raised p-0.5">
                    <button
                      onClick={() => setViewMode('cards')}
                      className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs transition-colors ${
                        viewMode === 'cards' ? 'bg-surface text-text shadow-sm' : 'text-muted hover:text-text'
                      }`}
                      title="Structured Cards View"
                    >
                      <Layers className="w-3.5 h-3.5" />
                      <span>Cards</span>
                    </button>
                    <button
                      onClick={() => setViewMode('markdown')}
                      className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs transition-colors ${
                        viewMode === 'markdown' ? 'bg-surface text-text shadow-sm' : 'text-muted hover:text-text'
                      }`}
                      title="Full Markdown Document View"
                    >
                      <FileText className="w-3.5 h-3.5" />
                      <span>Doc</span>
                    </button>
                  </div>

                  <button
                    onClick={handleOpenVault}
                    disabled={vaultOpening}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border bg-raised border-border text-muted hover:text-text text-xs transition-colors"
                    title="Open in Obsidian Notes Vault"
                  >
                    {vaultOpened ? (
                      <Check className="w-3.5 h-3.5 text-success" />
                    ) : (
                      <FolderOpen className="w-3.5 h-3.5 text-accent" />
                    )}
                    <span>{vaultOpened ? 'Opened Vault' : 'Open in Vault'}</span>
                  </button>

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

              {/* Granular Feedback Drawer */}
              {showFeedbackModal && (
                <div className="mt-3 p-4 rounded-lg bg-raised/60 border border-border flex flex-col gap-3">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-text flex items-center gap-1.5">
                      <Sparkles className="w-3.5 h-3.5 text-accent" />
                      {feedbackRating === 'like' ? 'What worked well?' : 'Why was this not relevant?'}
                      {targetTopicId && <span className="text-accent">({targetTopicId})</span>}
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

            {/* View Mode: Structured Cards */}
            {viewMode === 'cards' ? (
              <div className="flex flex-col gap-6">
                {itemsLoading && (
                  <div className="text-center py-3 text-xs text-muted flex items-center justify-center gap-2">
                    <Loader2 className="w-3.5 h-3.5 animate-spin text-accent" />
                    <span>Indexing structured cards...</span>
                  </div>
                )}
                {/* 1. Executive TL;DR Card */}
                {tldrContent && (
                  <div className="p-5 rounded-xl bg-accent/5 border border-accent/25 shadow-sm flex flex-col gap-2">
                    <div className="flex items-center gap-1.5 text-accent text-xs font-semibold uppercase tracking-wider font-mono">
                      <Sparkles className="w-3.5 h-3.5" />
                      Executive Radar (TL;DR)
                    </div>
                    <div className="text-xs text-text/90 leading-relaxed font-sans">
                      <SafeMarkdown content={tldrContent} />
                    </div>
                  </div>
                )}

                {/* 2. Top 3 Highlights Cards */}
                {top3.length > 0 && (
                  <div className="flex flex-col gap-3">
                    <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-text font-mono">
                      <Sparkles className="w-4 h-4 text-accent" />
                      Top 3 Highlights
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                      {top3.map((it, idx) => (
                        <div
                          key={it.id || idx}
                          className="p-4 rounded-xl bg-surface/90 border border-border shadow-sm flex flex-col justify-between hover:border-accent/40 transition-colors"
                        >
                          <div className="flex flex-col gap-2">
                            <div className="flex items-center justify-between">
                              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-raised text-accent">
                                #{idx + 1} • {it.source_name || 'Source'}
                              </span>
                              <div className="flex items-center gap-1">
                                <button
                                  onClick={() => openFeedback('like', it.topic_id, it.id)}
                                  className="p-1 rounded text-muted hover:text-success"
                                  title="Like item"
                                  aria-label="Like item"
                                >
                                  <ThumbsUp className="w-3 h-3" />
                                </button>
                                <button
                                  onClick={() => openFeedback('dislike', it.topic_id, it.id)}
                                  className="p-1 rounded text-muted hover:text-danger"
                                  title="Dislike item"
                                  aria-label="Dislike item"
                                >
                                  <ThumbsDown className="w-3 h-3" />
                                </button>
                              </div>
                            </div>
                            <h3 className="text-xs font-semibold text-text leading-snug line-clamp-2">
                              {it.title}
                            </h3>
                            <p className="text-[11px] text-muted line-clamp-3 leading-relaxed">
                              {it.summary}
                            </p>
                          </div>

                          <div className="mt-3 pt-3 border-t border-border/50 flex items-center justify-between">
                            {isSafeHttpUrl(it.url) ? (
                              <a
                                href={it.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1 text-[11px] font-medium text-accent hover:underline"
                              >
                                <span>Read more</span>
                                <ExternalLink className="w-3 h-3" />
                              </a>
                            ) : (
                              <span className="text-[11px] text-muted">Internal report</span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* 3. Opportunities Board with Deadline Chips */}
                {opportunities.length > 0 && (
                  <div className="p-5 rounded-xl bg-surface/80 border border-border shadow-sm flex flex-col gap-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-text font-mono">
                        <Trophy className="w-4 h-4 text-warning" />
                        Active Opportunities & Competitions ({opportunities.length})
                      </div>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      {opportunities.map((op, idx) => (
                        <div
                          key={op.id || idx}
                          className="p-3.5 rounded-lg bg-raised/40 border border-border/60 flex flex-col justify-between gap-2"
                        >
                          <div>
                            <div className="flex items-center justify-between gap-2 mb-1.5">
                              <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-warning/15 text-warning border border-warning/30 flex items-center gap-1">
                                <Clock className="w-3 h-3" />
                                {op.deadline || 'Closing Soon'}
                              </span>
                              {op.prize && (
                                <span className="text-[10px] font-mono text-success">
                                  Prize: {op.prize}
                                </span>
                              )}
                            </div>
                            <h4 className="text-xs font-semibold text-text line-clamp-2">{op.title}</h4>
                            <p className="text-[11px] text-muted line-clamp-2 mt-1">{op.summary}</p>
                          </div>

                          <div className="flex items-center justify-between pt-2 border-t border-border/40">
                            <span className="text-[10px] text-muted">{op.source_name}</span>
                            {isSafeHttpUrl(op.url) && (
                              <a
                                href={op.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1 text-[11px] font-medium text-warning hover:underline"
                              >
                                <span>Apply / Register</span>
                                <ExternalLink className="w-3 h-3" />
                              </a>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* 4. Startup Ideas (Labelled Generated) */}
                {ideas.length > 0 && (
                  <div className="p-5 rounded-xl bg-surface/80 border border-border shadow-sm flex flex-col gap-3">
                    <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-text font-mono">
                      <Lightbulb className="w-4 h-4 text-accent" />
                      Startup Ideas & Problems (Generated)
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      {ideas.map((id_item, idx) => (
                        <div
                          key={id_item.id || idx}
                          className="p-3.5 rounded-lg bg-raised/30 border border-border/60 flex flex-col gap-1.5"
                        >
                          <span className="text-[10px] font-mono text-muted uppercase">Concept Analysis</span>
                          <h4 className="text-xs font-semibold text-text">{id_item.title}</h4>
                          <p className="text-[11px] text-muted leading-relaxed">{id_item.summary}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* 5. Sector Deep Dives by Topic */}
                <div className="flex flex-col gap-4">
                  <div className="text-xs font-semibold uppercase tracking-wider text-text font-mono">
                    Sector & Domain Reports
                  </div>
                  {Array.from(topicsMap.entries()).map(([topic, topicItems]) => (
                    <div
                      key={topic}
                      className="p-5 rounded-xl bg-surface/80 border border-border shadow-sm flex flex-col gap-3"
                    >
                      <div className="flex items-center justify-between border-b border-border/60 pb-2">
                        <h3 className="text-xs font-bold text-text uppercase tracking-wider flex items-center gap-2">
                          <span className="w-1.5 h-1.5 rounded-full bg-accent" />
                          {topic}
                        </h3>
                        <div className="flex items-center gap-1.5">
                          <button
                            onClick={() => openFeedback('like', topic)}
                            className="p-1 rounded text-muted hover:text-success text-[11px] flex items-center gap-1"
                            title="Boost topic relevance"
                          >
                            <ThumbsUp className="w-3 h-3" />
                            <span>Boost</span>
                          </button>
                          <button
                            onClick={() => openFeedback('dislike', topic)}
                            className="p-1 rounded text-muted hover:text-danger text-[11px] flex items-center gap-1"
                            title="Demote topic relevance"
                          >
                            <ThumbsDown className="w-3 h-3" />
                            <span>Demote</span>
                          </button>
                        </div>
                      </div>

                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        {topicItems.map((item, idx) => (
                          <div
                            key={item.id || idx}
                            className="p-3 rounded-lg bg-raised/30 border border-border/40 flex flex-col justify-between gap-2"
                          >
                            <div>
                              <div className="flex items-center justify-between text-[10px] text-muted mb-1 font-mono">
                                <span>{item.source_name || 'Verified Source'}</span>
                                <div className="flex items-center gap-1">
                                  <button
                                    onClick={() => openFeedback('like', item.topic_id, item.id)}
                                    className="hover:text-success"
                                    title="Like topic item"
                                    aria-label="Like topic item"
                                  >
                                    <ThumbsUp className="w-2.5 h-2.5" />
                                  </button>
                                  <button
                                    onClick={() => openFeedback('dislike', item.topic_id, item.id)}
                                    className="hover:text-danger"
                                    title="Dislike topic item"
                                    aria-label="Dislike topic item"
                                  >
                                    <ThumbsDown className="w-2.5 h-2.5" />
                                  </button>
                                </div>
                              </div>
                              <h4 className="text-xs font-semibold text-text leading-snug">{item.title}</h4>
                              <p className="text-[11px] text-muted mt-1 line-clamp-3">{item.summary}</p>
                            </div>

                            {isSafeHttpUrl(item.url) && (
                              <div className="pt-2 border-t border-border/30 flex justify-end">
                                <a
                                  href={item.url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
                                >
                                  <span>Read more</span>
                                  <ExternalLink className="w-3 h-3" />
                                </a>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              /* View Mode: Full Markdown Document */
              <div className="p-6 md:p-8 rounded-xl bg-surface/80 backdrop-blur-md border border-border shadow-sm leading-relaxed text-xs text-text font-sans">
                <SafeMarkdown content={selectedBriefing.markdown} />
              </div>
            )}

            {/* Anti-Hallucination Source Index */}
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
                    const safe = isSafeHttpUrl(url);
                    return safe ? (
                      <a
                        key={idx}
                        href={url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center justify-between p-2.5 rounded-lg bg-raised/40 hover:bg-raised/70 border border-border/50 text-text transition-colors group"
                      >
                        <span className="truncate pr-2 text-muted group-hover:text-text font-mono text-[11px]">
                          {title}
                        </span>
                        <ExternalLink className="w-3.5 h-3.5 text-muted group-hover:text-accent shrink-0" />
                      </a>
                    ) : (
                      <div
                        key={idx}
                        className="flex items-center justify-between p-2.5 rounded-lg bg-raised/30 border border-border/40 text-muted text-[11px] font-mono"
                      >
                        <span className="truncate">{title}</span>
                      </div>
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
