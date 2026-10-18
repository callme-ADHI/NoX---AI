import React, { useEffect, useState } from 'react';
import {
  Database,
  Search,
  Pin,
  Trash2,
  Edit2,
  Check,
  X,
  Lock,
} from 'lucide-react';
import { Fact, Episode } from '../types';
import { api } from '../services/api';

export const MemoryPage: React.FC = () => {
  const [facts, setFacts] = useState<Fact[]>([]);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [activeCategory, setActiveCategory] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState('');
  const [activeTab, setActiveTab] = useState<'facts' | 'episodes'>('facts');

  useEffect(() => {
    loadMemory();
  }, []);

  const loadMemory = async () => {
    try {
      const [fData, eData] = await Promise.all([
        api.getFacts(),
        api.getEpisodes(),
      ]);
      setFacts(fData);
      setEpisodes(eData);
    } catch (err) {
      console.error('Failed to load memory:', err);
    }
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) {
      loadMemory();
      return;
    }
    try {
      const res = await api.searchMemory(searchQuery);
      setFacts(res.facts);
      setEpisodes(res.episodes);
    } catch (err) {
      console.error('Search memory error:', err);
    }
  };

  const handlePin = async (fact: Fact) => {
    try {
      const newPin = !fact.pinned;
      await api.pinFact(fact.id, newPin);
      setFacts((prev) =>
        prev.map((f) => (f.id === fact.id ? { ...f, pinned: newPin } : f))
      );
    } catch (err) {
      console.error('Pin fact error:', err);
    }
  };

  const handleDelete = async (factId: number) => {
    try {
      await api.deleteFact(factId);
      setFacts((prev) => prev.filter((f) => f.id !== factId));
    } catch (err) {
      console.error('Delete fact error:', err);
    }
  };

  const handleSaveEdit = async (factId: number) => {
    try {
      await api.updateFact(factId, editValue);
      setFacts((prev) =>
        prev.map((f) => (f.id === factId ? { ...f, value: editValue } : f))
      );
      setEditingId(null);
    } catch (err) {
      console.error('Update fact error:', err);
    }
  };

  const categories = [
    'all',
    'profile',
    'preference',
    'project',
    'person',
    'device',
    'other',
  ];

  const filteredFacts = facts.filter((f) => {
    const matchCat = activeCategory === 'all' || f.category === activeCategory;
    const matchQuery =
      !searchQuery ||
      f.key.toLowerCase().includes(searchQuery.toLowerCase()) ||
      f.value.toLowerCase().includes(searchQuery.toLowerCase());
    return matchCat && matchQuery;
  });

  const pinnedCount = facts.filter((f) => f.pinned).length;

  return (
    <div className="flex-1 flex flex-col p-6 md:p-8 bg-background overflow-hidden relative">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-xl font-bold text-text flex items-center gap-2">
            <Database className="w-5 h-5 text-accent" />
            Long-Term Memory Browser
          </h1>
          <p className="text-xs text-muted mt-1 flex items-center gap-1.5">
            <Lock className="w-3.5 h-3.5 text-accent" />
            <span className="font-semibold text-text">Local-Only Guarantee: </span>
            Memory facts and episodes are stored on this machine and never transmitted to the cloud.
          </p>
        </div>

        {/* Stats bar */}
        <div className="flex items-center gap-3">
          <div className="px-3 py-1.5 rounded-lg bg-surface border border-border text-xs flex items-center gap-2">
            <span className="text-muted">Total Facts:</span>
            <span className="font-mono font-bold text-text">{facts.length}</span>
          </div>
          <div className="px-3 py-1.5 rounded-lg bg-surface border border-border text-xs flex items-center gap-2">
            <span className="text-muted">Pinned:</span>
            <span className="font-mono font-bold text-accent">{pinnedCount}</span>
          </div>
          <div className="px-3 py-1.5 rounded-lg bg-surface border border-border text-xs flex items-center gap-2">
            <span className="text-muted">Episodes:</span>
            <span className="font-mono font-bold text-text">{episodes.length}</span>
          </div>
        </div>
      </div>

      {/* Search and Category Filter */}
      <div className="flex flex-col md:flex-row gap-3 mb-4">
        <div className="flex-1 relative">
          <Search className="w-4 h-4 text-muted absolute left-3 top-3" />
          <input
            type="text"
            placeholder="Search semantic facts and episodic memories..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            className="w-full bg-surface border border-border rounded-xl pl-9 pr-4 py-2 text-xs text-text placeholder:text-muted focus:outline-none focus:border-accent"
          />
        </div>

        <div className="flex items-center gap-1 bg-surface p-1 rounded-xl border border-border overflow-x-auto shrink-0">
          <button
            onClick={() => setActiveTab('facts')}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
              activeTab === 'facts'
                ? 'bg-raised text-accent font-semibold shadow-sm'
                : 'text-muted hover:text-text'
            }`}
          >
            Durable Facts ({facts.length})
          </button>
          <button
            onClick={() => setActiveTab('episodes')}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
              activeTab === 'episodes'
                ? 'bg-raised text-accent font-semibold shadow-sm'
                : 'text-muted hover:text-text'
            }`}
          >
            Episodes ({episodes.length})
          </button>
        </div>
      </div>

      {/* Category Pills (for facts) */}
      {activeTab === 'facts' && (
        <div className="flex items-center gap-1.5 mb-4 overflow-x-auto pb-1 text-xs">
          {categories.map((cat) => (
            <button
              key={cat}
              onClick={() => setActiveCategory(cat)}
              className={`px-3 py-1 rounded-full capitalize transition-colors text-[11px] font-medium border ${
                activeCategory === cat
                  ? 'bg-accent/15 text-accent border-accent/40 font-semibold'
                  : 'bg-surface text-muted border-border hover:bg-raised/60 hover:text-text'
              }`}
            >
              {cat}
            </button>
          ))}
        </div>
      )}

      {/* Content Area */}
      <div className="flex-1 bg-surface border border-border rounded-xl overflow-hidden shadow-sm flex flex-col">
        {activeTab === 'facts' ? (
          <div className="overflow-x-auto flex-1">
            <table className="w-full text-left text-xs">
              <thead className="bg-raised/50 border-b border-border text-muted font-mono uppercase text-[10px]">
                <tr>
                  <th className="py-3 px-4 w-10">Pin</th>
                  <th className="py-3 px-4">Key</th>
                  <th className="py-3 px-4">Value</th>
                  <th className="py-3 px-4">Category</th>
                  <th className="py-3 px-4">Confidence</th>
                  <th className="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {filteredFacts.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="py-8 text-center text-muted text-xs">
                      No facts found matching your criteria.
                    </td>
                  </tr>
                ) : (
                  filteredFacts.map((fact) => {
                    const isEditing = editingId === fact.id;
                    return (
                      <tr key={fact.id} className="hover:bg-raised/30 transition-colors">
                        <td className="py-3 px-4">
                          <button
                            onClick={() => handlePin(fact)}
                            className={`p-1 rounded hover:bg-raised transition-colors ${
                              fact.pinned ? 'text-accent' : 'text-muted/40 hover:text-muted'
                            }`}
                            title={fact.pinned ? 'Unpin fact' : 'Pin fact (never forgets)'}
                          >
                            <Pin className="w-3.5 h-3.5 fill-current" />
                          </button>
                        </td>
                        <td className="py-3 px-4 font-mono font-medium text-text">
                          {fact.key}
                        </td>
                        <td className="py-3 px-4 max-w-md">
                          {isEditing ? (
                            <div className="flex items-center gap-1.5">
                              <input
                                type="text"
                                value={editValue}
                                onChange={(e) => setEditValue(e.target.value)}
                                className="flex-1 bg-background border border-border rounded p-1 text-xs text-text focus:outline-none focus:border-accent"
                              />
                              <button
                                onClick={() => handleSaveEdit(fact.id)}
                                className="p-1 rounded text-success hover:bg-raised"
                              >
                                <Check className="w-3.5 h-3.5" />
                              </button>
                              <button
                                onClick={() => setEditingId(null)}
                                className="p-1 rounded text-muted hover:bg-raised"
                              >
                                <X className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          ) : (
                            <span className="text-muted leading-relaxed">{fact.value}</span>
                          )}
                        </td>
                        <td className="py-3 px-4">
                          <span className="px-2 py-0.5 rounded text-[10px] font-mono capitalize bg-raised border border-border text-muted">
                            {fact.category}
                          </span>
                        </td>
                        <td className="py-3 px-4 font-mono text-muted text-[11px]">
                          {(fact.confidence * 100).toFixed(0)}%
                        </td>
                        <td className="py-3 px-4 text-right">
                          <div className="flex items-center justify-end gap-1">
                            <button
                              onClick={() => {
                                setEditingId(fact.id);
                                setEditValue(fact.value);
                              }}
                              className="p-1 rounded text-muted hover:text-text hover:bg-raised"
                              title="Edit value"
                            >
                              <Edit2 className="w-3.5 h-3.5" />
                            </button>
                            <button
                              onClick={() => handleDelete(fact.id)}
                              className="p-1 rounded text-muted hover:text-danger hover:bg-raised"
                              title="Delete fact"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto p-4 space-y-2.5">
            {episodes.map((ep) => (
              <div
                key={ep.id}
                className="p-3.5 rounded-lg bg-background border border-border/70 flex flex-col gap-1.5 text-xs"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-mono uppercase bg-accent/10 border border-accent/20 text-accent">
                      {ep.kind}
                    </span>
                    {ep.topic && (
                      <span className="font-semibold text-text">{ep.topic}</span>
                    )}
                  </div>
                  <span className="text-[11px] font-mono text-muted">
                    {new Date(ep.ts).toLocaleString()}
                  </span>
                </div>
                <p className="text-muted leading-relaxed font-sans">{ep.summary}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
