import React, { useEffect, useRef, useState } from 'react';
import {
  Send,
  Square,
  Plus,
  Search,
  Cloud,
  Trash2,
  Sparkles,
  Info,
  Copy,
  Check,
  Lock,
  Unlock,
  X,
  ListTodo,
} from 'lucide-react';
import {
  ApprovalRequest,
  Conversation,
  Message,
  SubtaskNode,
  SystemStatus,
  Task,
  TaskPlan,
  ToolCallItem,
  WebSocketEvent,
} from '../types';
import { api } from '../services/api';
import { wsService } from '../services/websocket';
import { LiveTaskPlan } from '../components/LiveTaskPlan';
import { ApprovalCard } from '../components/ApprovalCard';
import { ToolCallCard } from '../components/ToolCallCard';
import { SafeMarkdown } from '../components/SafeMarkdown';
import { RouteChip } from '../components/RouteChip';
import { TraceDrawer } from '../components/TraceDrawer';
import { CloudStatusIndicator } from '../components/CloudStatusIndicator';

export const ChatPage: React.FC = () => {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputMessage, setInputMessage] = useState('');
  const [allowCloud, setAllowCloud] = useState(true);
  const [isPrivateMode, setIsPrivateMode] = useState(false);
  const [searchFilter, setSearchFilter] = useState('');
  const [isRunning, setIsRunning] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);

  // Streaming, execution and trace state
  const [currentPlan, setCurrentPlan] = useState<TaskPlan | null>(null);
  const [toolCalls, setToolCalls] = useState<ToolCallItem[]>([]);
  const [pendingApprovals, setPendingApprovals] = useState<ApprovalRequest[]>([]);
  const [streamingText, setStreamingText] = useState('');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [selectedTraceMessage, setSelectedTraceMessage] = useState<Message | null>(null);

  // Background tasks & system status
  const [activeTasks, setActiveTasks] = useState<Task[]>([]);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Load conversations, status and active tasks on mount
  useEffect(() => {
    loadConversations(true);
    loadStatusAndTasks();
  }, []);

  const loadStatusAndTasks = async () => {
    try {
      const [sData, tData] = await Promise.all([
        api.getStatus().catch(() => null),
        api.getTasks().catch(() => [] as Task[]),
      ]);
      if (sData) setSystemStatus(sData);
      setActiveTasks(tData.filter((t) => ['queued', 'running'].includes(t.status)));
    } catch (err) {
      console.error('Failed to load status and tasks:', err);
    }
  };

  // Subscribe to live WebSocket events
  useEffect(() => {
    const unsubscribe = wsService.subscribe((event: WebSocketEvent) => {
      handleWsEvent(event);
    });
    return unsubscribe;
  }, [isPrivateMode, allowCloud, activeConvId, currentPlan]);

  // Scroll to bottom when messages or streaming text change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingText, currentPlan, pendingApprovals, toolCalls]);

  const loadConversations = async (autoSelect = false) => {
    try {
      const data = await api.getConversations();
      setConversations(data);
      if (autoSelect && data.length > 0 && !activeConvId) {
        const savedConv = sessionStorage.getItem('nox_active_conv');
        const target = (savedConv ? data.find((c) => c.id === savedConv) : null) || data[0];
        if (target) {
          selectConversation(target.id);
        }
      }
    } catch (err) {
      console.error('Failed to load conversations:', err);
    }
  };

  const selectConversation = async (convId: string) => {
    setActiveConvId(convId);
    sessionStorage.setItem('nox_active_conv', convId);
    try {
      const conv = await api.getConversation(convId);
      setMessages(conv.messages || []);
      setStreamingText('');
      setCurrentPlan(null);
      setToolCalls([]);
      setPendingApprovals([]);
    } catch (err) {
      console.error('Failed to load conversation details:', err);
    }
  };

  const handleWsEvent = (event: WebSocketEvent) => {
    const { type, data, run_id } = event;

    if (type.startsWith('task_')) {
      loadStatusAndTasks();
    }

    switch (type) {
      case 'run_started':
        setIsRunning(true);
        if (run_id) setActiveRunId(run_id);
        break;

      case 'token':
        if (typeof data.delta === 'string') {
          setStreamingText((prev) => prev + data.delta);
        }
        break;

      case 'plan_created': {
        const rawNodes = (data.nodes || data.subtasks) as unknown as SubtaskNode[] | undefined;
        if (Array.isArray(rawNodes)) {
          setCurrentPlan({
            run_id: run_id || '',
            goal: (data.goal as string) || 'Autonomous task breakdown',
            nodes: rawNodes,
            overall_progress: 0,
            started_at: Date.now(),
          });
        }
        break;
      }

      case 'node_started':
        if (currentPlan && typeof data.node_id === 'string') {
          setCurrentPlan((prev) => {
            if (!prev) return null;
            return {
              ...prev,
              nodes: prev.nodes.map((n) =>
                n.id === data.node_id ? { ...n, status: 'running' } : n
              ),
            };
          });
        }
        break;

      case 'node_done':
        if (currentPlan && typeof data.node_id === 'string') {
          setCurrentPlan((prev) => {
            if (!prev) return null;
            return {
              ...prev,
              nodes: prev.nodes.map((n) =>
                n.id === data.node_id
                  ? {
                      ...n,
                      status: 'done',
                      summary: data.summary as string,
                      sources: data.sources as string[],
                      duration_ms: data.duration_ms as number,
                    }
                  : n
              ),
            };
          });
        }
        break;

      case 'node_failed':
        if (currentPlan && typeof data.node_id === 'string') {
          setCurrentPlan((prev) => {
            if (!prev) return null;
            return {
              ...prev,
              nodes: prev.nodes.map((n) =>
                n.id === data.node_id ? { ...n, status: 'failed' } : n
              ),
            };
          });
        }
        break;

      case 'tool_call':
        setToolCalls((prev) => [
          ...prev,
          {
            id: (data.id as string) || `tool_${Date.now()}`,
            name: data.name as string,
            arguments: (data.arguments as Record<string, unknown>) || {},
            tier: data.tier as ToolCallItem['tier'],
          },
        ]);
        break;

      case 'tool_result':
        setToolCalls((prev) =>
          prev.map((t) =>
            t.name === data.name || t.id === data.id
              ? {
                  ...t,
                  result_summary: data.summary as string,
                  duration_ms: data.duration_ms as number,
                  ok: data.ok as boolean,
                }
              : t
          )
        );
        break;

      case 'approval_required':
        setPendingApprovals((prev) => [
          ...prev,
          {
            id: Number(data.approval_id),
            run_id: run_id,
            tool_name: data.tool as string,
            arguments: (data.args as Record<string, unknown>) || {},
            reason: (data.reason as string) || 'Tool execution approval requested',
            tier: (data.tier as 'write' | 'dangerous') || 'write',
            untrusted_warning: !!data.untrusted_warning,
            status: 'pending',
          },
        ]);
        break;

      case 'approval_resolved':
        setPendingApprovals((prev) =>
          prev.filter((a) => a.id !== Number(data.approval_id))
        );
        break;

      case 'message_done':
        if (typeof data.text === 'string') {
          const effectiveCloud = isPrivateMode ? false : (typeof data.is_cloud === 'boolean' ? data.is_cloud : allowCloud);
          const newMsg: Message = {
            id: `msg_${Date.now()}`,
            conversation_id: activeConvId || '',
            role: 'assistant',
            content: data.text,
            is_cloud: effectiveCloud,
            model_name: (data.model_name as string) || (effectiveCloud ? 'nemotron-super-49b' : 'qwen2.5:14b'),
            lane: (data.lane as string) || (effectiveCloud ? 'cloud' : 'front_desk'),
            latency_ms: typeof data.latency_ms === 'number' ? data.latency_ms : undefined,
            trace: (data.trace as Record<string, unknown>) || undefined,
          };
          setMessages((prev) => [...prev, newMsg]);
          setStreamingText('');
        }
        break;

      case 'run_done':
      case 'run_failed':
      case 'run_cancelled':
        setIsRunning(false);
        setActiveRunId(null);
        if (activeConvId) {
          loadConversations();
        }
        break;
    }
  };

  const handleSend = async () => {
    if (!inputMessage.trim() || isRunning) return;

    const trimmed = inputMessage.trim();
    setInputMessage('');

    // Handle slash commands per Section 9 and Phase F7
    if (trimmed.startsWith('/')) {
      const parts = trimmed.split(' ');
      const cmd = parts[0].toLowerCase();
      if (cmd === '/clear') {
        setMessages([]);
        return;
      }
      if (cmd === '/cloud') {
        if (parts[1] === 'on') {
          setIsPrivateMode(false);
          setAllowCloud(true);
        } else if (parts[1] === 'off') {
          setAllowCloud(false);
        }
        return;
      }
      if (cmd === '/local') {
        const nextPrivate = !isPrivateMode;
        setIsPrivateMode(nextPrivate);
        if (nextPrivate) setAllowCloud(false);
        const noticeMsg: Message = {
          id: `local_${Date.now()}`,
          conversation_id: activeConvId || '',
          role: 'assistant',
          content: nextPrivate
            ? '**Private Mode Active**: Zero cloud egress guaranteed (§F2). All future turns, memory operations, and tools will execute 100% locally on your machine.'
            : '**Private Mode Disabled**: Cloud models may be used if cloud requests are permitted.',
          is_cloud: false,
          model_name: 'System',
          lane: 'system',
        };
        setMessages((prev) => [...prev, noticeMsg]);
        return;
      }
    }

    // Add optimistic user message
    const userMsg: Message = {
      id: `msg_user_${Date.now()}`,
      conversation_id: activeConvId || '',
      role: 'user',
      content: trimmed,
    };
    setMessages((prev) => [...prev, userMsg]);
    setIsRunning(true);
    setActiveRunId(null);
    setStreamingText('');

    const effectiveCloud = isPrivateMode ? false : allowCloud;
    try {
      const resp = await api.startChat(trimmed, activeConvId || undefined, effectiveCloud);
      setActiveRunId(resp.run_id);
      sessionStorage.setItem('nox_active_conv', resp.conversation_id);
      if (!activeConvId) {
        setActiveConvId(resp.conversation_id);
        loadConversations();
      }
    } catch (err) {
      console.error('Failed to start chat run:', err);
      setIsRunning(false);
    }
  };

  const handleStop = async () => {
    if (!activeRunId) return;
    try {
      await api.cancelRun(activeRunId);
    } catch (err) {
      console.error('Cancel run error:', err);
    }
  };

  const handleCancelActiveTask = async (taskId: string) => {
    try {
      await api.cancelTask(taskId);
      loadStatusAndTasks();
    } catch (err) {
      console.error('Cancel task error:', err);
    }
  };

  const handleNewChat = () => {
    sessionStorage.removeItem('nox_active_conv');
    setActiveConvId(null);
    setActiveRunId(null);
    setIsRunning(false);
    setMessages([]);
    setStreamingText('');
    setCurrentPlan(null);
    setToolCalls([]);
    setPendingApprovals([]);
    textareaRef.current?.focus();
  };

  const handleDeleteConversation = async (e: React.MouseEvent, convId: string) => {
    e.stopPropagation();
    try {
      await api.deleteConversation(convId);
      if (activeConvId === convId) {
        handleNewChat();
      }
      loadConversations();
    } catch (err) {
      console.error('Delete conversation error:', err);
    }
  };

  const handleClearAllConversations = async () => {
    if (!window.confirm('Are you sure you want to delete all conversations?')) {
      return;
    }
    try {
      await api.deleteAllConversations();
      handleNewChat();
      loadConversations();
    } catch (err) {
      console.error('Clear all conversations error:', err);
    }
  };

  const handleApprovalDecision = async (id: number, decision: 'approve' | 'deny') => {
    try {
      await api.resolveApproval(id, decision);
      setPendingApprovals((prev) => prev.filter((a) => a.id !== id));
    } catch (err) {
      console.error('Resolve approval error:', err);
    }
  };

  const handleCopyMessage = (id: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const filteredConversations = conversations.filter((c) =>
    c.title.toLowerCase().includes(searchFilter.toLowerCase())
  );

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Left Sidebar: Conversations */}
      <aside className="w-64 bg-surface border-r border-border flex flex-col shrink-0 hidden md:flex">
        {/* New Chat Button */}
        <div className="p-3 border-b border-border">
          <button
            onClick={handleNewChat}
            className="w-full flex items-center justify-center gap-2 py-2 px-3 rounded-lg bg-raised hover:bg-border text-text font-medium text-xs transition-colors border border-border"
          >
            <Plus className="w-4 h-4 text-accent" />
            New Conversation
          </button>
        </div>

        {/* Filter input */}
        <div className="p-2 border-b border-border/60">
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-muted absolute left-2.5 top-2.5" />
            <input
              type="text"
              placeholder="Filter chats..."
              value={searchFilter}
              onChange={(e) => setSearchFilter(e.target.value)}
              className="w-full bg-raised rounded-md pl-8 pr-3 py-1.5 text-xs text-text placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>
        </div>

        {/* Conversation list */}
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {filteredConversations.length === 0 ? (
            <div className="p-4 text-center text-xs text-muted">
              No conversations yet
            </div>
          ) : (
            filteredConversations.map((c) => {
              const isActive = activeConvId === c.id;
              return (
                <div
                  key={c.id}
                  onClick={() => selectConversation(c.id)}
                  className={`group flex items-center justify-between px-3 py-2 rounded-lg cursor-pointer text-xs transition-colors ${
                    isActive
                      ? 'bg-raised text-accent font-medium border border-border'
                      : 'text-muted hover:text-text hover:bg-raised/40'
                  }`}
                >
                  <span className="truncate flex-1 pr-2">{c.title || 'Untitled'}</span>
                  <button
                    onClick={(e) => handleDeleteConversation(e, c.id)}
                    className="opacity-70 hover:opacity-100 p-1 text-muted hover:text-danger rounded transition-all"
                    title="Delete conversation"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              );
            })
          )}
        </div>

        {/* Clear All Chats Footer */}
        {conversations.length > 0 && (
          <div className="p-2 border-t border-border/60">
            <button
              onClick={handleClearAllConversations}
              className="w-full flex items-center justify-center gap-1.5 py-1.5 px-2 rounded-md hover:bg-danger/10 text-muted hover:text-danger text-[11px] font-medium transition-colors border border-transparent hover:border-danger/30"
              title="Delete all conversations"
            >
              <Trash2 className="w-3.5 h-3.5" />
              Clear All Chats
            </button>
          </div>
        )}
      </aside>

      {/* Main Chat Area */}
      <main className="flex-1 flex flex-col bg-background overflow-hidden relative">
        {/* Top Header Bar: Active Tasks, Private Mode, Cloud Health (§F7) */}
        <header className="px-4 py-2 border-b border-border bg-surface/50 flex items-center justify-between gap-3 shrink-0">
          {/* Active Tasks Chips (§F7.5) */}
          <div className="flex items-center gap-2 overflow-x-auto py-0.5 max-w-lg scrollbar-none">
            {activeTasks.length === 0 ? (
              <span className="text-[11px] text-muted flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-success/70" />
                No active background tasks
              </span>
            ) : (
              activeTasks.map((t) => (
                <div
                  key={t.id}
                  className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-mono bg-raised border border-border text-text shrink-0 animate-pulse"
                >
                  <ListTodo className="w-3 h-3 text-accent" />
                  <span className="font-semibold truncate max-w-[100px]">{t.title}</span>
                  <span className="text-muted">{Math.round(t.progress * 100)}%</span>
                  <button
                    onClick={() => handleCancelActiveTask(t.id)}
                    className="p-0.5 hover:text-danger rounded"
                    title="Cancel background task"
                  >
                    <X className="w-2.5 h-2.5" />
                  </button>
                </div>
              ))
            )}
          </div>

          {/* Controls: Delete Chat, Private Mode Toggle and Cloud Status (§F7.3, §F7.4) */}
          <div className="flex items-center gap-2.5">
            {/* Delete Active Chat Button */}
            {activeConvId && (
              <button
                type="button"
                onClick={(e) => handleDeleteConversation(e, activeConvId)}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs text-muted hover:text-danger hover:bg-danger/10 border border-border transition-colors"
                title="Delete this conversation"
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span className="hidden sm:inline font-mono text-[11px]">Delete Chat</span>
              </button>
            )}

            {/* Private Mode Toggle */}
            <button
              type="button"
              onClick={() => {
                const nextPrivate = !isPrivateMode;
                setIsPrivateMode(nextPrivate);
                if (nextPrivate) setAllowCloud(false);
              }}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold transition-all border ${
                isPrivateMode
                  ? 'bg-amber-500/20 text-amber-400 border-amber-500/40 shadow-sm'
                  : 'bg-surface hover:bg-raised text-muted hover:text-text border-border'
              }`}
              title="Private Mode (zero cloud egress guaranteed per §F2)"
            >
              {isPrivateMode ? <Lock className="w-3.5 h-3.5" /> : <Unlock className="w-3.5 h-3.5" />}
              <span className="hidden sm:inline font-mono text-[11px]">
                {isPrivateMode ? 'Private Mode' : 'Standard Mode'}
              </span>
            </button>

            {/* Cloud Status Indicator */}
            <CloudStatusIndicator
              cloudHealth={systemStatus?.cloud_health}
              isCloudEnabled={!isPrivateMode && allowCloud}
              onToggleCloud={(en) => {
                if (en && isPrivateMode) setIsPrivateMode(false);
                setAllowCloud(en);
              }}
            />
          </div>
        </header>

        {/* Messages scroll area */}
        <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-4">
          {messages.length === 0 && !streamingText && (
            <div className="h-full flex flex-col items-center justify-center text-center p-8 select-none max-w-lg mx-auto">
              <div className="w-12 h-12 rounded-2xl bg-accent/10 border border-accent/20 flex items-center justify-center text-accent mb-4 shadow-sm">
                <Sparkles className="w-6 h-6" />
              </div>
              <h2 className="text-base font-semibold text-text mb-1">
                Welcome to NoX Agent
              </h2>
              <p className="text-xs text-muted mb-6 leading-relaxed">
                Autonomous, privacy-first cybersecurity assistant running locally
                on Arch Linux. Select a prompt or ask anything.
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full text-left">
                {[
                  'Produce morning briefing for today',
                  'Search CTF competitions for students in Kerala',
                  'What are the latest CVEs in Linux kernel?',
                  'Check system health and active services',
                ].map((sample, i) => (
                  <button
                    key={i}
                    onClick={() => {
                      setInputMessage(sample);
                      textareaRef.current?.focus();
                    }}
                    className="p-3 rounded-lg bg-surface hover:bg-raised border border-border text-xs text-text hover:border-accent/40 transition-colors"
                  >
                    {sample}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Render historical messages */}
          {messages.map((msg) => {
            const isUser = msg.role === 'user';
            return (
              <div
                key={msg.id}
                className={`flex flex-col ${isUser ? 'items-end' : 'items-start'} max-w-3xl ${
                  isUser ? 'ml-auto' : 'mr-auto'
                } w-full`}
              >
                {/* Route chip on assistant messages (§F7.1) */}
                {!isUser && (
                  <div className="flex items-center gap-2 mb-1.5 pl-1">
                    <RouteChip
                      lane={msg.lane || (msg.is_cloud ? 'cloud' : 'front_desk')}
                      modelName={msg.model_name}
                      isCloud={msg.is_cloud}
                      latencyMs={msg.latency_ms}
                      onOpenTrace={() => setSelectedTraceMessage(msg)}
                    />
                  </div>
                )}

                {/* Message Bubble */}
                <div
                  className={`p-4 rounded-xl text-xs leading-relaxed relative group ${
                    isUser
                      ? 'bg-accent/15 text-text border border-accent/30 rounded-br-none'
                      : 'bg-surface text-text border border-border rounded-bl-none shadow-sm w-full'
                  }`}
                >
                  <SafeMarkdown
                    content={msg.content}
                    className="selection:bg-accent/20"
                  />

                  {/* Copy button */}
                  <button
                    onClick={() => handleCopyMessage(msg.id, msg.content)}
                    className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 p-1 rounded bg-raised border border-border text-muted hover:text-text transition-all"
                    title="Copy message"
                  >
                    {copiedId === msg.id ? (
                      <Check className="w-3 h-3 text-success" />
                    ) : (
                      <Copy className="w-3 h-3" />
                    )}
                  </button>
                </div>
              </div>
            );
          })}

          {/* Live Task Plan panel */}
          {currentPlan && (
            <div className="max-w-3xl mr-auto w-full">
              <LiveTaskPlan plan={currentPlan} />
            </div>
          )}

          {/* Pending Approval Cards (§F7.6) */}
          {pendingApprovals.map((approval) => (
            <div key={approval.id} className="max-w-3xl mr-auto w-full">
              <ApprovalCard
                approval={approval}
                onDecision={handleApprovalDecision}
              />
            </div>
          ))}

          {/* Tool call cards */}
          {toolCalls.length > 0 && (
            <div className="max-w-3xl mr-auto w-full space-y-1">
              {toolCalls.map((t) => (
                <ToolCallCard key={t.id} tool={t} />
              ))}
            </div>
          )}

          {/* Streaming assistant message */}
          {streamingText && (
            <div className="flex flex-col items-start max-w-3xl mr-auto w-full">
              <div className="p-4 rounded-xl text-xs leading-relaxed bg-surface text-text border border-accent/40 rounded-bl-none shadow-sm w-full">
                <div className="whitespace-pre-wrap font-sans">
                  {streamingText}
                  <span className="inline-block w-1.5 h-3.5 bg-accent ml-1 animate-pulse align-middle" />
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input Bar */}
        <div className="p-3 md:p-4 bg-surface border-t border-border shrink-0">
          <div className="max-w-3xl mx-auto flex flex-col gap-2">
            {/* Input Options bar */}
            <div className="flex items-center justify-between text-xs text-muted px-1">
              <div className="flex items-center gap-3">
                <label className={`flex items-center gap-1.5 cursor-pointer select-none ${isPrivateMode ? 'opacity-50 cursor-not-allowed' : ''}`}>
                  <input
                    type="checkbox"
                    disabled={isPrivateMode}
                    checked={!isPrivateMode && allowCloud}
                    onChange={(e) => setAllowCloud(e.target.checked)}
                    className="rounded border-border text-accent focus:ring-accent bg-background"
                  />
                  <span className="text-[11px] flex items-center gap-1">
                    <Cloud className="w-3.5 h-3.5" />
                    Allow cloud for this request
                  </span>
                </label>
                <div
                  className="cursor-help text-muted hover:text-text"
                  title="Enables heavy cloud model for deep planning. Memory injection is disabled for cloud requests per §6.2. Type /local to lock local."
                >
                  <Info className="w-3.5 h-3.5" />
                </div>
              </div>

              {isRunning && (
                <button
                  onClick={handleStop}
                  className="flex items-center gap-1 text-[11px] text-danger hover:underline font-medium"
                >
                  <Square className="w-3 h-3 fill-current" />
                  Stop generation
                </button>
              )}
            </div>

            {/* Input textarea box with Private Mode visual glow (§F7.4) */}
            <div className={`flex items-end gap-2 bg-background border rounded-xl p-2 transition-all ${
              isPrivateMode
                ? 'border-amber-500/60 ring-1 ring-amber-500/30 shadow-[0_0_12px_rgba(245,158,11,0.12)]'
                : 'border-border focus-within:border-accent'
            }`}>
              <textarea
                ref={textareaRef}
                rows={1}
                value={inputMessage}
                onChange={(e) => setInputMessage(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                placeholder={isPrivateMode ? "[Private Mode Active] Ask NoX (100% local CPU/Ollama)..." : "Ask NoX a question, /local to lock privacy, /deep..."}
                aria-label="Message NoX"
                className="flex-1 bg-transparent text-xs text-text placeholder:text-muted resize-none focus:outline-none max-h-32 min-h-[36px] py-1.5 px-2 font-sans"
              />

              <button
                disabled={!inputMessage.trim() || isRunning}
                onClick={handleSend}
                aria-label="Send message"
                className={`p-2 rounded-lg transition-colors shrink-0 ${
                  inputMessage.trim() && !isRunning
                    ? 'bg-accent text-black hover:bg-accent/90 shadow-sm'
                    : 'bg-raised text-muted cursor-not-allowed opacity-50'
                }`}
                title="Send message (Enter)"
              >
                <Send className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      </main>

      {/* Slide-out Trace Drawer (§F7.2) */}
      <TraceDrawer
        isOpen={!!selectedTraceMessage}
        onClose={() => setSelectedTraceMessage(null)}
        messageId={selectedTraceMessage?.id}
        lane={selectedTraceMessage?.lane}
        modelName={selectedTraceMessage?.model_name}
        isCloud={selectedTraceMessage?.is_cloud}
        latencyMs={selectedTraceMessage?.latency_ms}
        trace={selectedTraceMessage?.trace}
      />
    </div>
  );
};
