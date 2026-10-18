import React, { useEffect, useState } from 'react';
import {
  Settings,
  Shield,
  Palette,
  Bell,
  Download,
  Check,
} from 'lucide-react';
import { api } from '../services/api';

export const SettingsPage: React.FC = () => {
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const [saved, setSaved] = useState(false);

  // Editable settings
  const [theme, setTheme] = useState('dark');
  const [accentColor, setAccentColor] = useState('#7c9cff');
  const [desktopNotifs, setDesktopNotifs] = useState(true);
  const [uiNotifs, setUiNotifs] = useState(true);

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      const data = await api.getSettings();
      setSettings(data);
      if (typeof data.theme === 'string') setTheme(data.theme);
      if (typeof data.accent_color === 'string') setAccentColor(data.accent_color);
      if (data.notifications_desktop !== undefined) setDesktopNotifs(!!data.notifications_desktop);
      if (data.notifications_ui !== undefined) setUiNotifs(!!data.notifications_ui);
    } catch (err) {
      console.error('Failed to load settings:', err);
    }
  };

  const handleSave = async () => {
    try {
      await api.updateSettings({
        theme,
        accent_color: accentColor,
        notifications_desktop: desktopNotifs,
        notifications_ui: uiNotifs,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      console.error('Failed to update settings:', err);
    }
  };

  const handleExportData = () => {
    const dataStr = JSON.stringify(settings, null, 2);
    const blob = new Blob([dataStr], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `nox-export-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div
      className="flex-1 flex flex-col p-6 md:p-8 bg-background overflow-y-auto max-w-4xl mx-auto"
      tabIndex={0}
      aria-label="Settings panel"
      role="region"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-8 pb-4 border-b border-border/70">
        <div>
          <h1 className="text-xl font-bold text-text flex items-center gap-2">
            <Settings className="w-5 h-5 text-accent" />
            Daemon & UI Preferences
          </h1>
          <p className="text-xs text-muted mt-1">
            Configure display, notifications, and review system safety boundaries.
          </p>
        </div>

        <button
          onClick={handleSave}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-accent text-black font-semibold text-xs hover:bg-accent/90 transition-colors shadow-sm"
        >
          {saved ? <Check className="w-4 h-4" /> : null}
          {saved ? 'Saved' : 'Save Preferences'}
        </button>
      </div>

      <div className="space-y-6 text-xs">
        {/* Section 1: Appearance & Accent */}
        <div className="p-5 rounded-xl bg-surface border border-border shadow-sm flex flex-col gap-4">
          <div className="flex items-center gap-2 border-b border-border/50 pb-2">
            <Palette className="w-4 h-4 text-accent" />
            <h2 className="text-sm font-semibold text-text">Theme & Appearance</h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label htmlFor="theme-mode-select" className="block text-muted font-medium mb-1">Theme Mode</label>
              <select
                id="theme-mode-select"
                aria-label="Theme Mode"
                value={theme}
                onChange={(e) => setTheme(e.target.value)}
                className="w-full bg-background border border-border rounded-lg p-2 text-text focus:outline-none focus:border-accent"
              >
                <option value="dark">Dark (Near-black #0a0a0b)</option>
                <option value="system">Follow System</option>
              </select>
            </div>

            <div>
              <label htmlFor="accent-color-hex" className="block text-muted font-medium mb-1">Accent Color</label>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  aria-label="Accent color picker"
                  value={accentColor}
                  onChange={(e) => setAccentColor(e.target.value)}
                  className="w-10 h-8 rounded border border-border bg-background cursor-pointer"
                />
                <input
                  id="accent-color-hex"
                  type="text"
                  aria-label="Accent color hex code"
                  value={accentColor}
                  onChange={(e) => setAccentColor(e.target.value)}
                  className="flex-1 bg-background border border-border rounded-lg p-2 font-mono text-text focus:outline-none focus:border-accent"
                />
              </div>
            </div>
          </div>
        </div>

        {/* Section 2: Notifications */}
        <div className="p-5 rounded-xl bg-surface border border-border shadow-sm flex flex-col gap-4">
          <div className="flex items-center gap-2 border-b border-border/50 pb-2">
            <Bell className="w-4 h-4 text-accent" />
            <h2 className="text-sm font-semibold text-text">Notification Channels</h2>
          </div>

          <div className="space-y-3">
            <label className="flex items-center gap-3 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={desktopNotifs}
                onChange={(e) => setDesktopNotifs(e.target.checked)}
                className="rounded border-border text-accent focus:ring-accent bg-background"
              />
              <div>
                <div className="font-semibold text-text">Desktop Notifications (notify-send)</div>
                <div className="text-[11px] text-muted">Send alerts for briefings, fired reminders, and approval cards.</div>
              </div>
            </label>

            <label className="flex items-center gap-3 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={uiNotifs}
                onChange={(e) => setUiNotifs(e.target.checked)}
                className="rounded border-border text-accent focus:ring-accent bg-background"
              />
              <div>
                <div className="font-semibold text-text">In-App Notification Banners</div>
                <div className="text-[11px] text-muted">Show real-time toast alerts inside the browser UI.</div>
              </div>
            </label>
          </div>
        </div>

        {/* Section 3: Read-Only Safety Boundaries (Section 9) */}
        <div className="p-5 rounded-xl bg-surface border border-border shadow-sm flex flex-col gap-4">
          <div className="flex items-center justify-between border-b border-border/50 pb-2">
            <div className="flex items-center gap-2">
              <Shield className="w-4 h-4 text-warning" />
              <h2 className="text-sm font-semibold text-text">Safety Boundaries & Sandbox Rules</h2>
            </div>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-warning/10 text-warning border border-warning/20 font-bold uppercase">
              Read-Only Security Lock
            </span>
          </div>

          <p className="text-xs text-muted leading-relaxed">
            Per the NoX safety specification (Section 6.5 & 9), safety settings cannot be edited
            or relaxed from the Web UI or via model conversations. They can only be modified by the
            owner directly editing <code className="text-accent bg-raised px-1 rounded">~/.config/nox/config.toml</code>.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <span className="text-muted font-semibold block mb-1">Allowed File Roots:</span>
              <pre className="p-2 rounded bg-background border border-border font-mono text-[11px] text-text">
                ['~']
              </pre>
            </div>

            <div>
              <span className="text-muted font-semibold block mb-1">Denied System Paths:</span>
              <pre className="p-2 rounded bg-background border border-border font-mono text-[11px] text-text max-h-24 overflow-y-auto">
{`~/.ssh
~/.gnupg
~/.config/nox/secrets.env
~/.mozilla
~/.config/chromium`}
              </pre>
            </div>
          </div>
        </div>

        {/* Section 4: Data Management */}
        <div className="p-5 rounded-xl bg-surface border border-border shadow-sm flex flex-col gap-4">
          <div className="flex items-center gap-2 border-b border-border/50 pb-2">
            <Download className="w-4 h-4 text-accent" />
            <h2 className="text-sm font-semibold text-text">Data & Exports</h2>
          </div>

          <div className="flex items-center justify-between">
            <div>
              <div className="font-semibold text-text">Export Configuration & State</div>
              <div className="text-[11px] text-muted">
                Download a JSON snapshot of your non-sensitive settings.
              </div>
            </div>
            <button
              onClick={handleExportData}
              className="px-3 py-1.5 rounded-lg bg-raised hover:bg-border text-text border border-border text-xs font-medium transition-colors"
            >
              Export JSON
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
