import React, { useEffect, useState } from 'react';
import {
  Wrench,
  RefreshCw,
  Plus,
  ToggleLeft,
  ToggleRight,
  ShieldAlert,
  Code2,
  AlertTriangle,
  X,
} from 'lucide-react';
import { api } from '../services/api';
import { SkillInfo } from '../types';

export const SkillsPage: React.FC = () => {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [togglingSkill, setTogglingSkill] = useState<string | null>(null);

  // Install Skill Modal
  const [isInstallModalOpen, setIsInstallModalOpen] = useState<boolean>(false);
  const [installSource, setInstallSource] = useState<string>('');
  const [forceInstall, setForceInstall] = useState<boolean>(false);
  const [installLoading, setInstallLoading] = useState<boolean>(false);
  const [installResult, setInstallResult] = useState<Record<string, unknown> | null>(null);

  const loadSkills = async () => {
    try {
      setLoading(true);
      const data = await api.getSkills();
      setSkills(data);
    } catch (err) {
      console.error('Failed to load skills:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSkills();
  }, []);

  const handleToggle = async (skill: SkillInfo) => {
    try {
      setTogglingSkill(skill.name);
      if (skill.enabled) {
        await api.disableSkill(skill.name);
      } else {
        await api.enableSkill(skill.name);
      }
      setSkills((prev) =>
        prev.map((s) => (s.name === skill.name ? { ...s, enabled: !s.enabled } : s))
      );
    } catch (err) {
      console.error('Failed to toggle skill:', err);
    } finally {
      setTogglingSkill(null);
    }
  };

  const handleInstallSkill = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!installSource.trim()) return;
    try {
      setInstallLoading(true);
      setInstallResult(null);
      const res = await api.installSkill(installSource.trim(), forceInstall);
      setInstallResult(res);
      await loadSkills();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setInstallResult({ error: msg });
    } finally {
      setInstallLoading(false);
    }
  };

  return (
    <div
      className="flex-1 flex flex-col p-6 md:p-8 bg-background overflow-y-auto max-w-5xl mx-auto w-full"
      tabIndex={0}
      aria-label="Skills catalog panel"
      role="region"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-8 pb-4 border-b border-border/70">
        <div>
          <h1 className="text-xl font-bold text-text flex items-center gap-2">
            <Wrench className="w-5 h-5 text-accent" />
            Skills & Tool Integrations
          </h1>
          <p className="text-xs text-muted mt-1">
            Agent capabilities catalog with AST/shell security inspection and staged untrusted import.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              setIsInstallModalOpen(true);
              setInstallSource('');
              setForceInstall(false);
              setInstallResult(null);
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent text-black font-semibold text-xs hover:bg-accent/90 transition-colors shadow-sm"
          >
            <Plus className="w-3.5 h-3.5" />
            Install Skill
          </button>
          <button
            onClick={loadSkills}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-raised hover:bg-border text-muted hover:text-text border border-border text-xs transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Skills Grid */}
      {loading && skills.length === 0 ? (
        <div className="p-8 text-center text-muted text-xs flex items-center justify-center gap-2">
          <RefreshCw className="w-4 h-4 animate-spin text-accent" />
          Loading skills catalog...
        </div>
      ) : skills.length === 0 ? (
        <div className="p-12 text-center rounded-xl bg-surface/50 border border-border text-muted text-xs">
          No skills registered in the catalog.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
          {skills.map((skill) => {
            const isToggling = togglingSkill === skill.name;

            return (
              <div
                key={skill.name}
                className={`p-5 rounded-xl border shadow-glass flex flex-col justify-between transition-all ${
                  skill.enabled
                    ? 'bg-surface border-border hover:border-white/20'
                    : 'bg-surface/40 border-border/50 opacity-75'
                }`}
              >
                <div>
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <div className="p-2.5 rounded-xl bg-white/[0.05] border border-white/10 text-accent">
                        <Code2 className="w-5 h-5" />
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <h3 className="text-sm font-bold text-text font-mono">
                            {skill.name}
                          </h3>
                          <span className="text-[10px] font-mono text-muted">
                            v{skill.version}
                          </span>
                        </div>
                        <p className="text-xs text-muted mt-1 leading-relaxed">
                          {skill.description}
                        </p>
                      </div>
                    </div>

                    <button
                      onClick={() => handleToggle(skill)}
                      disabled={isToggling}
                      className="p-1 text-muted hover:text-text transition-colors disabled:opacity-50"
                      title={skill.enabled ? 'Disable skill' : 'Enable skill'}
                    >
                      {skill.enabled ? (
                        <ToggleRight className="w-6 h-6 text-accent" />
                      ) : (
                        <ToggleLeft className="w-6 h-6 text-muted" />
                      )}
                    </button>
                  </div>

                  <div className="space-y-1.5 py-2 text-xs border-t border-border/40 text-muted">
                    <div className="flex justify-between items-center">
                      <span>Script Execution:</span>
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-mono uppercase font-semibold ${
                          skill.allow_scripts
                            ? 'bg-warning/15 text-warning border border-warning/30'
                            : 'bg-success/15 text-success border border-success/30'
                        }`}
                      >
                        {skill.allow_scripts ? 'Allowed (Guarded)' : 'No Scripts'}
                      </span>
                    </div>

                    {skill.scripts && skill.scripts.length > 0 && (
                      <div className="mt-2">
                        <span className="text-[10px] font-mono text-muted block mb-1">
                          Declared Executables:
                        </span>
                        <div className="flex flex-wrap gap-1">
                          {skill.scripts.map((sc) => (
                            <span
                              key={sc}
                              className="px-2 py-0.5 rounded text-[10px] font-mono bg-black/40 border border-border/60 text-text/80"
                            >
                              {sc}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Install Skill Modal */}
      {isInstallModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 animate-in fade-in">
          <form
            onSubmit={handleInstallSkill}
            className="w-full max-w-lg bg-surface border border-border rounded-xl p-5 shadow-2xl flex flex-col gap-4 text-xs animate-in zoom-in-95"
          >
            <div className="flex items-center justify-between border-b border-border/70 pb-3">
              <div className="flex items-center gap-2">
                <ShieldAlert className="w-4 h-4 text-accent" />
                <h3 className="text-sm font-bold text-text">Install Untrusted Skill</h3>
              </div>
              <button
                type="button"
                onClick={() => setIsInstallModalOpen(false)}
                className="p-1 rounded text-muted hover:text-text hover:bg-raised transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-3 rounded-lg bg-warning/10 border border-warning/30 text-warning text-xs flex items-start gap-2.5">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
              <div className="leading-relaxed">
                Untrusted skills are staged, static-scanned via Python AST and shell regexes, and installed disabled by default.
              </div>
            </div>

            <div>
              <label className="text-[11px] text-muted block mb-1">
                Source (Directory Path or Package URL):
              </label>
              <input
                type="text"
                placeholder="e.g. ./community_skills/my_tool"
                value={installSource}
                onChange={(e) => setInstallSource(e.target.value)}
                className="w-full bg-raised border border-border rounded-lg px-3 py-2 text-xs text-text focus:outline-none focus:border-accent font-mono"
                autoFocus
              />
            </div>

            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={forceInstall}
                onChange={(e) => setForceInstall(e.target.checked)}
                className="rounded border-border bg-surface text-accent focus:ring-0"
              />
              <span className="text-[11px] text-text">
                Override static security warnings (force staging)
              </span>
            </label>

            {installResult && (
              <div className="p-3 rounded-lg bg-black/40 border border-border/60 text-xs text-text/90 font-mono max-h-40 overflow-y-auto whitespace-pre-wrap">
                {JSON.stringify(installResult, null, 2)}
              </div>
            )}

            <div className="flex items-center justify-between pt-3 border-t border-border">
              <span className="text-[11px] text-muted">
                Requires manual enable after installation.
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setIsInstallModalOpen(false)}
                  className="px-3 py-1.5 rounded-lg bg-raised hover:bg-border text-muted hover:text-text border border-border text-xs transition-colors"
                >
                  Close
                </button>
                <button
                  type="submit"
                  disabled={installLoading || !installSource.trim()}
                  className="px-3.5 py-1.5 rounded-lg bg-accent text-black font-semibold text-xs hover:bg-accent/90 transition-colors disabled:opacity-50"
                >
                  {installLoading ? 'Scanning & Installing...' : 'Install Skill'}
                </button>
              </div>
            </div>
          </form>
        </div>
      )}
    </div>
  );
};
