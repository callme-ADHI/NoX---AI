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
  GraduationCap,
  Globe,
  Home,
  DollarSign,
  Plane,
  ChevronDown,
  ChevronUp,
  Search,
  CheckCircle,
  Trash2,
} from 'lucide-react';
import { Briefing } from '../types';
import { api } from '../services/api';
import { SafeMarkdown } from '../components/SafeMarkdown';
import { isSafeHttpUrl } from '../utils/sanitizer';
import { DeleteConfirmModal } from '../components/DeleteConfirmModal';
import { UndoToast } from '../components/UndoToast';

const FEEDBACK_TAGS = [
  'Actionable',
  'Too generic',
  'Irrelevant topic',
  'High priority',
  'Already knew this',
  'Great sources',
];

const OPPORTUNITY_STATUSES = [
  { value: 'new', label: 'New' },
  { value: 'interested', label: 'Interested' },
  { value: 'applied', label: 'Applied' },
  { value: 'interview', label: 'Interview' },
  { value: 'offer', label: 'Offer' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'dismissed', label: 'Dismissed' },
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
  created_at: string;
  kind?: string;
  organization?: string;
  country?: string;
  mode?: string;
  deadline?: string;
  score?: number;
  opportunity_status?: string;
  benefits?: {
    housing?: string;
    stipend?: string;
    travel?: string;
    abroad?: boolean;
    visa_assistance?: string;
  };
  eligibility?: {
    level?: string;
    target_audience?: string;
    open_to_international?: boolean;
  };
  score_breakdown?: Record<string, number>;
  source_evidence_snippet?: string;
  source_evidence_url?: string;
  prize?: string;
}

function parseDeadline(deadlineStr?: string): { days: number | null; label: string; isUrgent: boolean } {
  if (!deadlineStr) return { days: null, label: 'Rolling / Open', isUrgent: false };
  const lower = deadlineStr.toLowerCase();
  if (lower.includes('rolling') || lower.includes('tbd') || lower.includes('open') || lower.includes('unknown')) {
    return { days: null, label: deadlineStr, isUrgent: false };
  }
  const match = deadlineStr.match(/\d{4}-\d{2}-\d{2}/);
  if (match) {
    const d = new Date(match[0]);
    if (!isNaN(d.getTime())) {
      const now = new Date();
      const diffMs = d.getTime() - now.getTime();
      const days = Math.ceil(diffMs / (1000 * 60 * 60 * 24));
      if (days < 0) {
        return { days, label: `Closed (${Math.abs(days)}d ago)`, isUrgent: false };
      }
      if (days <= 7) {
        return { days, label: `${days}d left`, isUrgent: true };
      }
      return { days, label: `${days}d left`, isUrgent: false };
    }
  }
  return { days: null, label: deadlineStr, isUrgent: false };
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
    topics?: Array<{ id: string; name: string; state: string; items_staged?: number }>;
  } | null>(null);
  const [nextSchedule, setNextSchedule] = useState<{
    morning: string;
    evening: string;
  }>({ morning: '04:00', evening: '18:00' });
  const [copied, setCopied] = useState(false);
  const [vaultOpening, setVaultOpening] = useState(false);
  const [vaultOpened, setVaultOpened] = useState(false);
  const [viewMode, setViewMode] = useState<'cards' | 'markdown'>('cards');
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  // Filtering & Search
  const [filterCategory, setFilterCategory] = useState<
    'all' | 'internship' | 'hackathon' | 'competition' | 'news' | 'ideas'
  >('all');
  const [sortBy, setSortBy] = useState<'score' | 'deadline' | 'newest'>('score');
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedWhy, setExpandedWhy] = useState<Record<string, boolean>>({});

  // Granular feedback state
  const [showFeedbackModal, setShowFeedbackModal] = useState(false);
  const [feedbackRating, setFeedbackRating] = useState<'like' | 'dislike'>('like');
  const [targetTopicId, setTargetTopicId] = useState<string | undefined>();
  const [targetItemId, setTargetItemId] = useState<string | undefined>();
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [feedbackReason, setFeedbackReason] = useState('');
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false);

  // Delete & Undo state
  const [briefingToDelete, setBriefingToDelete] = useState<Briefing | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [undoItem, setUndoItem] = useState<{ id: number; message: string } | null>(null);

  const handleDeleteBriefing = async () => {
    if (!briefingToDelete) return;
    setDeleteLoading(true);
    try {
      await api.deleteBriefing(briefingToDelete.id);
      const title = briefingToDelete.title || 'Briefing';
      const id = briefingToDelete.id;
      setBriefingToDelete(null);
      if (selectedBriefing?.id === id) {
        setSelectedBriefing(null);
      }
      setUndoItem({
        id,
        message: `"${title}" deleted.`,
      });
      loadBriefings();
    } catch (err) {
      console.error('Failed to delete briefing:', err);
    } finally {
      setDeleteLoading(false);
    }
  };

  const handleUndoDelete = async () => {
    if (!undoItem) return;
    try {
      await api.restoreBriefing(undoItem.id);
      setUndoItem(null);
      loadBriefings();
    } catch (err) {
      console.error('Failed to restore briefing:', err);
    }
  };

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
      const activeObj = s.active_run || (s.active && typeof s.active === 'object' ? (s.active as any) : null);
      if (activeObj) {
        setRunning(true);
        setActiveRun(activeObj);
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
          const activeObj = s.active_run || (s.active && typeof s.active === 'object' ? (s.active as any) : null);
          if (activeObj) {
            setActiveRun(activeObj);
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

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;
    if (running) {
      timer = setInterval(() => {
        setElapsedSeconds((prev) => prev + 1);
      }, 1000);
    } else {
      setElapsedSeconds(0);
    }
    return () => {
      if (timer) clearInterval(timer);
    };
  }, [running]);

  const currentStageName = React.useMemo(() => {
    if (!activeRun) return 'Initializing...';
    if (activeRun.topics && activeRun.topics.length > 0) {
      const activeTopic = activeRun.topics.find((t) => t.state === 'collecting' || t.state === 'running');
      if (activeTopic) return activeTopic.name;
    }
    if (activeRun.progress >= 1) return 'Synthesizing digest...';
    if (activeRun.topics_completed > 0) return `Processing (${activeRun.topics_completed}/${activeRun.topics_total})`;
    return 'Collecting topics...';
  }, [activeRun]);

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

  const handleStatusChange = async (canonicalUrl: string, newStatus: string) => {
    setItems((prev) =>
      prev.map((it) => (it.url === canonicalUrl ? { ...it, opportunity_status: newStatus } : it))
    );
    try {
      await api.updateOpportunityStatus(canonicalUrl, newStatus);
    } catch (err) {
      console.error('Failed to update opportunity status:', err);
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

  // Categorization helpers
  const isInternship = (it: BriefingItem) =>
    it.category === 'internship' || it.kind === 'internship' || it.topic_id === 'topic_internships';

  const isHackathon = (it: BriefingItem) =>
    it.category === 'hackathon' || it.kind === 'hackathon' || it.topic_id === 'topic_hackathons';

  const isCompetition = (it: BriefingItem) =>
    ['competitions', 'competition', 'grants', 'ctf'].includes(it.category?.toLowerCase() || '') ||
    it.topic_id === 'topic_competitions';

  const isIdea = (it: BriefingItem) =>
    ['ideas', 'startup_ideas', 'startups'].includes(it.category?.toLowerCase() || '') ||
    it.topic_id === 'topic_startups';

  const isNews = (it: BriefingItem) =>
    !isInternship(it) && !isHackathon(it) && !isCompetition(it) && !isIdea(it);

  // Filter items
  const filteredItems = items.filter((it) => {
    if (filterCategory === 'internship' && !isInternship(it)) return false;
    if (filterCategory === 'hackathon' && !isHackathon(it)) return false;
    if (filterCategory === 'competition' && !isCompetition(it)) return false;
    if (filterCategory === 'ideas' && !isIdea(it)) return false;
    if (filterCategory === 'news' && !isNews(it)) return false;

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const match =
        it.title?.toLowerCase().includes(q) ||
        it.organization?.toLowerCase().includes(q) ||
        it.summary?.toLowerCase().includes(q) ||
        it.country?.toLowerCase().includes(q) ||
        it.source_name?.toLowerCase().includes(q) ||
        it.topic_id?.toLowerCase().includes(q);
      if (!match) return false;
    }
    return true;
  });

  // Sort items
  const sortedItems = [...filteredItems].sort((a, b) => {
    if (sortBy === 'score') {
      return (b.score || 0) - (a.score || 0);
    }
    if (sortBy === 'deadline') {
      const da = parseDeadline(a.deadline).days ?? 9999;
      const db = parseDeadline(b.deadline).days ?? 9999;
      return da - db;
    }
    return (b.created_at || '').localeCompare(a.created_at || '');
  });

  const internships = items.filter(isInternship);
  const hackathons = items.filter(isHackathon);
  const competitions = items.filter(isCompetition);
  const ideas = items.filter(isIdea);
  const newsItems = items.filter(isNews);
  const top3 = items.slice(0, 3);

  // Group news by topic
  const newsTopicsMap = new Map<string, BriefingItem[]>();
  newsItems.forEach((it) => {
    const t = it.topic_id || 'General';
    const list = newsTopicsMap.get(t) || [];
    list.push(it);
    newsTopicsMap.set(t, list);
  });

  const renderOpportunityCard = (op: BriefingItem, idx: number, typeLabel: string, typeIcon: React.ReactNode) => {
    const dl = parseDeadline(op.deadline);
    const key = op.id || op.url || idx;
    const isExpanded = !!expandedWhy[key];

    return (
      <div
        key={key}
        data-testid={`${typeLabel.toLowerCase().replace(/\s+/g, '-')}-card`}
        className="p-4 rounded-xl bg-surface/90 border border-border shadow-sm flex flex-col justify-between gap-3 hover:border-accent/40 transition-colors"
      >
        <div className="flex flex-col gap-2">
          {/* Header row */}
          <div className="flex items-start justify-between gap-2 flex-wrap">
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-accent/15 text-accent border border-accent/30 flex items-center gap-1">
                {typeIcon}
                {typeLabel}
              </span>
              {op.score !== undefined && (
                <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-400/15 text-amber-300 border border-amber-400/30">
                  Score: {op.score}/100
                </span>
              )}
              {op.deadline && (
                <span
                  className={`px-2 py-0.5 rounded text-[10px] font-mono flex items-center gap-1 border ${
                    dl.isUrgent
                      ? 'bg-rose-500/20 text-rose-300 border-rose-500/40 font-bold'
                      : 'bg-raised text-muted border-border'
                  }`}
                >
                  <Clock className="w-2.5 h-2.5" />
                  {dl.label}
                </span>
              )}
            </div>

            <div className="flex items-center gap-2">
              <select
                aria-label="Opportunity tracking status"
                value={op.opportunity_status || 'new'}
                onChange={(e) => handleStatusChange(op.url, e.target.value)}
                className="text-[11px] bg-raised text-text border border-border rounded px-2 py-1 focus:outline-none focus:border-accent capitalize"
              >
                {OPPORTUNITY_STATUSES.map((st) => (
                  <option key={st.value} value={st.value}>
                    Status: {st.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Title & Organization */}
          <div>
            <h4 className="text-sm font-semibold text-text leading-snug">{op.title}</h4>
            <div className="text-[11px] text-muted flex items-center gap-2 mt-0.5 font-medium">
              {op.organization && <span>{op.organization}</span>}
              {op.organization && op.country && <span>•</span>}
              {op.country && <span>{op.country}</span>}
            </div>
          </div>

          {/* Benefit & Eligibility Badges Row */}
          <div className="flex flex-wrap gap-1.5 pt-1">
            {/* Housing Badge */}
            {op.benefits?.housing === 'provided' ? (
              <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 flex items-center gap-1">
                <Home className="w-2.5 h-2.5" />
                Housing: Provided
              </span>
            ) : op.benefits?.housing === 'allowance' ? (
              <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-amber-500/15 text-amber-300 border border-amber-500/30 flex items-center gap-1">
                <Home className="w-2.5 h-2.5" />
                Housing: Allowance
              </span>
            ) : (
              <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-raised text-muted border border-border flex items-center gap-1">
                <Home className="w-2.5 h-2.5" />
                Housing: Unknown
              </span>
            )}

            {/* Stipend Badge */}
            {op.benefits?.stipend && op.benefits.stipend !== 'none' && op.benefits.stipend !== 'unpaid' ? (
              <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 flex items-center gap-1">
                <DollarSign className="w-2.5 h-2.5" />
                Stipend: Paid
              </span>
            ) : (
              <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-rose-500/15 text-rose-300 border border-rose-500/30 flex items-center gap-1">
                <DollarSign className="w-2.5 h-2.5" />
                Stipend: Unpaid
              </span>
            )}

            {/* Travel Badge */}
            {op.benefits?.travel && op.benefits.travel !== 'none' && (
              <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 flex items-center gap-1">
                <Plane className="w-2.5 h-2.5" />
                Travel: Covered
              </span>
            )}

            {/* Abroad / Location Badge */}
            <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-sky-500/15 text-sky-300 border border-sky-500/30 flex items-center gap-1">
              <Globe className="w-2.5 h-2.5" />
              {op.country || 'International'}
            </span>

            {/* Mode Badge */}
            <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-indigo-500/15 text-indigo-300 border border-indigo-500/30 capitalize">
              {op.mode || 'Onsite'}
            </span>

            {/* Eligibility Badge */}
            {op.eligibility?.open_to_international !== false ? (
              <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 flex items-center gap-1">
                <CheckCircle className="w-2.5 h-2.5" />
                Eligible
              </span>
            ) : (
              <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-amber-500/15 text-amber-300 border border-amber-500/30">
                Check Requirements
              </span>
            )}
          </div>

          {/* Summary */}
          <p className="text-[11px] text-muted line-clamp-3 leading-relaxed mt-1">
            {op.summary}
          </p>

          {/* Why Ranked collapsible */}
          {(op.score_breakdown || op.source_evidence_snippet) && (
            <div className="mt-1">
              <button
                onClick={() =>
                  setExpandedWhy((prev) => ({
                    ...prev,
                    [key]: !prev[key],
                  }))
                }
                className="text-[11px] text-accent hover:underline flex items-center gap-1 font-mono"
              >
                {isExpanded ? (
                  <>
                    <ChevronUp className="w-3 h-3" />
                    <span>Hide ranking breakdown</span>
                  </>
                ) : (
                  <>
                    <ChevronDown className="w-3 h-3" />
                    <span>Why ranked ({op.score ?? 0} pts)</span>
                  </>
                )}
              </button>

              {isExpanded && (
                <div className="mt-2 p-2.5 rounded-lg bg-raised/70 border border-border flex flex-col gap-2 text-[10px] font-mono">
                  {op.score_breakdown && (
                    <div className="flex flex-wrap gap-1.5">
                      {Object.entries(op.score_breakdown).map(([k, v]) => (
                        <span
                          key={k}
                          className={`px-1.5 py-0.5 rounded border ${
                            v > 0
                              ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20'
                              : 'bg-rose-500/10 text-rose-300 border-rose-500/20'
                          }`}
                        >
                          {k}: {v > 0 ? `+${v}` : v}
                        </span>
                      ))}
                    </div>
                  )}
                  {op.source_evidence_snippet && (
                    <div className="text-muted italic border-l-2 border-accent pl-2 text-[11px] font-sans">
                      &ldquo;{op.source_evidence_snippet}&rdquo;
                      {op.source_evidence_url && (
                        <a
                          href={op.source_evidence_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="ml-1 text-accent hover:underline inline-flex items-center gap-0.5 not-italic"
                        >
                          [source]
                        </a>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Card Footer */}
        <div className="mt-3 pt-2.5 border-t border-border/50 flex items-center justify-between">
          <span className="text-[10px] text-muted">{op.source_name || 'Verified Program'}</span>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1">
              <button
                onClick={() => openFeedback('like', op.topic_id, op.id)}
                className="p-1 rounded text-muted hover:text-success"
                title="Like item"
                aria-label="Like item"
              >
                <ThumbsUp className="w-3 h-3" />
              </button>
              <button
                onClick={() => openFeedback('dislike', op.topic_id, op.id)}
                className="p-1 rounded text-muted hover:text-danger"
                title="Dislike item"
                aria-label="Dislike item"
              >
                <ThumbsDown className="w-3 h-3" />
              </button>
            </div>

            {isSafeHttpUrl(op.url) && (
              <a
                href={op.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-accent/10 hover:bg-accent/20 text-accent text-xs font-semibold border border-accent/30 transition-colors"
              >
                <span>Apply</span>
                <ExternalLink className="w-3 h-3" />
              </a>
            )}
          </div>
        </div>
      </div>
    );
  };

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
                    <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
                    <span className="truncate max-w-[220px]">
                      {currentStageName} ({elapsedSeconds}s)
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
                    <span className="font-semibold text-text truncate max-w-[150px]">
                      {b.title || 'Briefing Report'}
                    </span>
                    <div className="flex items-center gap-1.5">
                      {b.feedback && (
                        <span className="text-[10px] text-accent">
                          {b.feedback === 1 ? '👍' : '👎'}
                        </span>
                      )}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setBriefingToDelete(b);
                        }}
                        title="Delete briefing"
                        className="p-1 rounded text-muted hover:text-danger hover:bg-raised transition-colors"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
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

      {/* Main Content Area */}
      <main className="flex-1 overflow-y-auto p-6 md:p-8 flex flex-col max-w-5xl mx-auto">
        {selectedBriefing ? (
          <div className="flex flex-col gap-6">
            {/* Header Card */}
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

                  <button
                    onClick={() => setBriefingToDelete(selectedBriefing)}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border bg-raised border-border text-danger hover:bg-danger/10 text-xs transition-colors"
                    title="Delete briefing"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    <span>Delete</span>
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

                {/* Filter and Search Toolbar */}
                <div className="p-3.5 rounded-xl bg-surface/80 border border-border shadow-sm flex flex-col md:flex-row items-center justify-between gap-3">
                  {/* Category Filter Chips */}
                  <div className="flex items-center gap-1.5 flex-wrap w-full md:w-auto">
                    {[
                      { key: 'all', label: 'All' },
                      { key: 'internship', label: 'Funded Internships' },
                      { key: 'hackathon', label: 'Hackathons' },
                      { key: 'competition', label: 'Competitions' },
                      { key: 'news', label: 'Tech News' },
                      { key: 'ideas', label: 'Startups' },
                    ].map((chip) => (
                      <button
                        key={chip.key}
                        onClick={() => setFilterCategory(chip.key as any)}
                        className={`px-2.5 py-1 rounded-lg text-xs font-medium border transition-colors ${
                          filterCategory === chip.key
                            ? 'bg-accent text-black font-semibold border-accent'
                            : 'bg-raised text-muted border-border hover:text-text hover:bg-raised/70'
                        }`}
                      >
                        {chip.label}
                      </button>
                    ))}
                  </div>

                  {/* Search and Sort Toolbar */}
                  <div className="flex items-center gap-2 w-full md:w-auto">
                    <div className="relative flex-1 md:w-48">
                      <Search className="w-3 h-3 absolute left-2.5 top-2.5 text-muted pointer-events-none" />
                      <input
                        type="text"
                        placeholder="Search items..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="w-full bg-raised border border-border rounded-lg pl-7 pr-2.5 py-1 text-xs text-text placeholder-muted focus:outline-none focus:border-accent"
                      />
                    </div>

                    <select
                      aria-label="Sort briefing items"
                      value={sortBy}
                      onChange={(e) => setSortBy(e.target.value as any)}
                      className="bg-raised text-text border border-border rounded-lg px-2 py-1 text-xs focus:outline-none focus:border-accent"
                    >
                      <option value="score">Value Score (high → low)</option>
                      <option value="deadline">Deadline (soonest)</option>
                      <option value="newest">Published (newest)</option>
                    </select>
                  </div>
                </div>

                {/* 1. Executive Radar (TL;DR) Card */}
                {tldrContent && filterCategory === 'all' && !searchQuery && (
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
                {top3.length > 0 && filterCategory === 'all' && !searchQuery && (
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

                {/* Filtered View Mode: When user selects specific filter or searches */}
                {(filterCategory !== 'all' || searchQuery.trim()) ? (
                  <div className="flex flex-col gap-4">
                    <div className="flex items-center justify-between">
                      <h3 className="text-xs font-semibold uppercase tracking-wider text-text font-mono">
                        Filtered Results ({sortedItems.length})
                      </h3>
                    </div>

                    {filterCategory === 'internship' && internships.length === 0 ? (
                      <div className="p-6 rounded-xl bg-surface/80 border border-border/80 text-center flex flex-col items-center gap-2">
                        <GraduationCap className="w-8 h-8 text-accent/60 mb-1" />
                        <h4 className="text-sm font-semibold text-text">No Funded Internships</h4>
                        <p className="text-xs text-muted max-w-md">
                          No new funded internships in this briefing. 11 programs on active watchlist are being monitored. Next check at {nextSchedule.evening || '18:00'}.
                        </p>
                      </div>
                    ) : sortedItems.length === 0 ? (
                      <div className="p-8 rounded-xl bg-surface/60 border border-border text-center text-xs text-muted">
                        No items match your filter criteria.
                      </div>
                    ) : (
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {sortedItems.map((it, idx) => {
                          if (isInternship(it)) {
                            return renderOpportunityCard(it, idx, 'Funded Internship', <GraduationCap className="w-3 h-3" />);
                          }
                          if (isHackathon(it)) {
                            return renderOpportunityCard(it, idx, 'Hackathon', <Trophy className="w-3 h-3 text-warning" />);
                          }
                          if (isCompetition(it)) {
                            return renderOpportunityCard(it, idx, 'Competition', <Trophy className="w-3 h-3 text-accent" />);
                          }
                          // Regular news / startup card
                          return (
                            <div
                              key={it.id || idx}
                              className="p-4 rounded-xl bg-surface/90 border border-border shadow-sm flex flex-col justify-between gap-2 hover:border-accent/40 transition-colors"
                            >
                              <div>
                                <div className="flex items-center justify-between text-[10px] text-muted mb-1 font-mono">
                                  <span>{it.source_name || 'Verified Source'}</span>
                                  <div className="flex items-center gap-1">
                                    <button
                                      onClick={() => openFeedback('like', it.topic_id, it.id)}
                                      className="hover:text-success"
                                      title="Like"
                                    >
                                      <ThumbsUp className="w-2.5 h-2.5" />
                                    </button>
                                    <button
                                      onClick={() => openFeedback('dislike', it.topic_id, it.id)}
                                      className="hover:text-danger"
                                      title="Dislike"
                                    >
                                      <ThumbsDown className="w-2.5 h-2.5" />
                                    </button>
                                  </div>
                                </div>
                                <h4 className="text-xs font-semibold text-text leading-snug">{it.title}</h4>
                                <p className="text-[11px] text-muted mt-1 line-clamp-3">{it.summary}</p>
                              </div>
                              {isSafeHttpUrl(it.url) && (
                                <div className="pt-2 border-t border-border/30 flex justify-end">
                                  <a
                                    href={it.url}
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
                          );
                        })}
                      </div>
                    )}
                  </div>
                ) : (
                  /* Standard Grouped Sections View */
                  <div className="flex flex-col gap-6">
                    {/* 3. Funded Internships Section */}
                    <div className="p-5 rounded-xl bg-surface/80 border border-border shadow-sm flex flex-col gap-4">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-text font-mono">
                          <GraduationCap className="w-4 h-4 text-accent" />
                          Funded Internships & Fellowships ({internships.length})
                        </div>
                      </div>

                      {internships.length === 0 ? (
                        <div className="p-4 rounded-lg bg-raised/40 border border-border/60 text-center flex flex-col items-center gap-1.5 py-6">
                          <GraduationCap className="w-6 h-6 text-accent/60 mb-0.5" />
                          <h4 className="text-xs font-semibold text-text">No new funded internships in this briefing</h4>
                          <p className="text-[11px] text-muted max-w-md">
                            11 programs on active watchlist are being monitored. Next check at {nextSchedule.evening || '18:00'}.
                          </p>
                        </div>
                      ) : (
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                          {internships.map((op, idx) =>
                            renderOpportunityCard(op, idx, 'Funded Internship', <GraduationCap className="w-3 h-3" />)
                          )}
                        </div>
                      )}
                    </div>

                    {/* 4. Hackathons & Ideathons Section */}
                    {hackathons.length > 0 && (
                      <div className="p-5 rounded-xl bg-surface/80 border border-border shadow-sm flex flex-col gap-4">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-text font-mono">
                            <Trophy className="w-4 h-4 text-warning" />
                            Hackathons & Ideathons ({hackathons.length})
                          </div>
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                          {hackathons.map((op, idx) =>
                            renderOpportunityCard(op, idx, 'Hackathon', <Trophy className="w-3 h-3 text-warning" />)
                          )}
                        </div>
                      </div>
                    )}

                    {/* 5. Competitions & Challenges Board (Maintains Opportunities & Leads selector match) */}
                    {competitions.length > 0 && (
                      <div className="p-5 rounded-xl bg-surface/80 border border-border shadow-sm flex flex-col gap-3">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-text font-mono">
                            <Trophy className="w-4 h-4 text-accent" />
                            Active Opportunities & Competitions ({competitions.length})
                          </div>
                          <span className="text-[10px] text-muted font-mono">Opportunities & Leads</span>
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                          {competitions.map((op, idx) =>
                            renderOpportunityCard(op, idx, 'Competition', <Trophy className="w-3 h-3 text-accent" />)
                          )}
                        </div>
                      </div>
                    )}

                    {/* 6. Startup Ideas */}
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

                    {/* 7. Sector Deep Dives by Topic */}
                    <div className="flex flex-col gap-4">
                      <div className="text-xs font-semibold uppercase tracking-wider text-text font-mono">
                        Sector & Domain Reports
                      </div>
                      {Array.from(newsTopicsMap.entries()).map(([topic, topicItems]) => (
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
                )}
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

      {/* Delete Confirmation Modal */}
      <DeleteConfirmModal
        isOpen={!!briefingToDelete}
        title="Delete Intelligence Briefing"
        message={`Are you sure you want to delete briefing "${briefingToDelete?.title || 'Report'}"?`}
        loading={deleteLoading}
        onConfirm={handleDeleteBriefing}
        onCancel={() => setBriefingToDelete(null)}
      />

      {/* Undo Toast */}
      {undoItem && (
        <div className="fixed bottom-6 right-6 z-50">
          <UndoToast
            id={String(undoItem.id)}
            message={undoItem.message}
            onUndo={handleUndoDelete}
            onDismiss={() => setUndoItem(null)}
          />
        </div>
      )}
    </div>
  );
};
