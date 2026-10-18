import React, { useEffect, useState } from 'react';
import {
  Newspaper,
  ThumbsUp,
  ThumbsDown,
  Play,
  Calendar,
  Loader2,
} from 'lucide-react';
import { Briefing } from '../types';
import { api } from '../services/api';

export const BriefingsPage: React.FC = () => {
  const [briefings, setBriefings] = useState<Briefing[]>([]);
  const [selectedBriefing, setSelectedBriefing] = useState<Briefing | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [feedbackNote, setFeedbackNote] = useState('');

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

  const handleRunNow = async () => {
    setRunning(true);
    try {
      const jobs = await api.getJobs();
      const briefingJob = jobs.find((j) => j.name === 'daily-briefing');
      if (briefingJob) {
        await api.runJob(briefingJob.id);
        setTimeout(loadBriefings, 3000);
      }
    } catch (err) {
      console.error('Failed to trigger daily briefing:', err);
    } finally {
      setRunning(false);
    }
  };

  const handleFeedback = async (id: number, rating: number) => {
    try {
      await api.submitBriefingFeedback(id, rating, feedbackNote || undefined);
      setBriefings((prev) =>
        prev.map((b) => (b.id === id ? { ...b, feedback: rating } : b))
      );
      if (selectedBriefing?.id === id) {
        setSelectedBriefing((prev) => (prev ? { ...prev, feedback: rating } : null));
      }
      setFeedbackNote('');
    } catch (err) {
      console.error('Submit feedback error:', err);
    }
  };

  return (
    <div className="flex-1 flex overflow-hidden bg-background">
      {/* Sidebar: History list */}
      <aside className="w-72 bg-surface border-r border-border flex flex-col shrink-0">
        <div className="p-4 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Newspaper className="w-4 h-4 text-accent" />
            <h2 className="text-sm font-semibold text-text">Briefings Archive</h2>
          </div>
          <button
            onClick={handleRunNow}
            disabled={running}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-accent/10 hover:bg-accent/20 text-accent border border-accent/20 text-xs font-medium transition-colors"
          >
            {running ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Play className="w-3.5 h-3.5 fill-current" />
            )}
            Run Now
          </button>
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
              No briefings delivered yet. Click "Run Now" to generate one.
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
                    <span>{b.created_at ? new Date(b.created_at).toLocaleDateString() : 'Today'}</span>
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
            <div className="p-6 rounded-xl bg-surface border border-border shadow-sm flex flex-col gap-4">
              <div className="flex items-start justify-between">
                <div>
                  <h1 className="text-xl font-bold text-text mb-1">
                    {selectedBriefing.title || 'Cybersecurity & Tech Briefing'}
                  </h1>
                  <span className="text-xs text-muted">
                    Delivered on {new Date(selectedBriefing.created_at).toLocaleString()}
                  </span>
                </div>

                {/* Feedback controls */}
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => handleFeedback(selectedBriefing.id, 1)}
                    className={`p-2 rounded-lg border transition-colors ${
                      selectedBriefing.feedback === 1
                        ? 'bg-success/20 border-success/40 text-success'
                        : 'bg-raised border-border text-muted hover:text-text'
                    }`}
                    title="Helpful briefing"
                  >
                    <ThumbsUp className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => handleFeedback(selectedBriefing.id, -1)}
                    className={`p-2 rounded-lg border transition-colors ${
                      selectedBriefing.feedback === -1
                        ? 'bg-danger/20 border-danger/40 text-danger'
                        : 'bg-raised border-border text-muted hover:text-text'
                    }`}
                    title="Not relevant"
                  >
                    <ThumbsDown className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>

            {/* Briefing Markdown Content */}
            <div className="p-6 rounded-xl bg-surface border border-border leading-relaxed text-xs text-text shadow-sm whitespace-pre-wrap font-sans">
              {selectedBriefing.markdown}
            </div>
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
