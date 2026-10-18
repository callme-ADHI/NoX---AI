/**
 * WebSocket manager with auto-reconnect, keepalive, deduplication, event replay, and ordered dispatch.
 */

import { WebSocketEvent } from '../types';

export type EventCallback = (event: WebSocketEvent) => void;
export type StatusCallback = (connected: boolean) => void;

export class WebSocketService {
  private ws: WebSocket | null = null;
  private subscribers: Set<EventCallback> = new Set();
  private statusSubscribers: Set<StatusCallback> = new Set();
  private reconnectTimeout: number | null = null;
  private pingInterval: number | null = null;
  private backoff = 1000;
  private isExplicitClose = false;
  public isConnected = false;

  // Replay and ordering structures
  private eventHistory: WebSocketEvent[] = [];
  private seenEventIds: Set<string> = new Set();
  private readonly maxHistory = 200;

  public connect(customUrl?: string): void {
    this.isExplicitClose = false;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    let url = customUrl;
    if (!url) {
      const protocol = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = typeof window !== 'undefined' ? window.location.host : '127.0.0.1:7777';
      let token = '';
      if (typeof window !== 'undefined') {
        const params = new URLSearchParams(window.location.search);
        token = params.get('t') || params.get('token') || sessionStorage.getItem('nox_token') || '';
        if (token) {
          sessionStorage.setItem('nox_token', token);
        }
      }
      const tokenQuery = token ? `?t=${encodeURIComponent(token)}` : '';
      url = `${protocol}//${host}/ws${tokenQuery}`;
    }

    try {
      this.ws = new WebSocket(url);
    } catch (err) {
      console.warn('WebSocket connection init failed:', err);
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.isConnected = true;
      this.backoff = 1000;
      this.notifyStatus(true);
      this.startKeepalive();
    };

    this.ws.onmessage = (e) => {
      try {
        const msg = typeof e.data === 'string' ? JSON.parse(e.data) : e.data;
        if (msg.type === 'pong') {
          return;
        }
        this.processIncomingEvent(msg as WebSocketEvent);
      } catch (err) {
        console.debug('Failed to parse WebSocket message:', err);
      }
    };

    this.ws.onclose = () => {
      this.cleanup();
      this.notifyStatus(false);
      if (!this.isExplicitClose) {
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = (err) => {
      console.debug('WebSocket error observed:', err);
      this.ws?.close();
    };
  }

  /**
   * Process and dispatch event with deduplication and replay tracking.
   */
  public processIncomingEvent(event: WebSocketEvent): boolean {
    if (event.id) {
      if (this.seenEventIds.has(event.id)) {
        return false; // deduplicate
      }
      this.seenEventIds.add(event.id);
      if (this.seenEventIds.size > 1000) {
        // Prune oldest half of seen IDs
        const ids = Array.from(this.seenEventIds);
        this.seenEventIds = new Set(ids.slice(500));
      }
    }

    this.eventHistory.push(event);
    if (this.eventHistory.length > this.maxHistory) {
      this.eventHistory.shift();
    }

    this.notifySubscribers(event);
    return true;
  }

  /**
   * Replay stored events from history to a callback or return them ordered.
   */
  public replay(sinceTs?: string): WebSocketEvent[] {
    if (!sinceTs) {
      return [...this.eventHistory];
    }
    return this.eventHistory.filter((e) => !e.ts || e.ts > sinceTs);
  }

  public getHistory(): WebSocketEvent[] {
    return [...this.eventHistory];
  }

  public clearHistory(): void {
    this.eventHistory = [];
    this.seenEventIds.clear();
  }

  public disconnect(): void {
    this.isExplicitClose = true;
    this.cleanup();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.notifyStatus(false);
  }

  public subscribe(cb: EventCallback): () => void {
    this.subscribers.add(cb);
    return () => {
      this.subscribers.delete(cb);
    };
  }

  public onStatusChange(cb: StatusCallback): () => void {
    this.statusSubscribers.add(cb);
    cb(this.isConnected);
    return () => {
      this.statusSubscribers.delete(cb);
    };
  }

  private startKeepalive(): void {
    if (this.pingInterval) clearInterval(this.pingInterval);
    if (typeof window !== 'undefined') {
      this.pingInterval = window.setInterval(() => {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, 25000);
    }
  }

  private cleanup(): void {
    this.isConnected = false;
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimeout) clearTimeout(this.reconnectTimeout);
    if (typeof window !== 'undefined') {
      this.reconnectTimeout = window.setTimeout(() => {
        this.connect();
      }, this.backoff);
      this.backoff = Math.min(this.backoff * 1.5, 15000);
    }
  }

  private notifySubscribers(event: WebSocketEvent): void {
    for (const sub of this.subscribers) {
      try {
        sub(event);
      } catch (err) {
        console.error('Subscriber error handling WebSocket event:', err);
      }
    }
  }

  private notifyStatus(status: boolean): void {
    for (const sub of this.statusSubscribers) {
      try {
        sub(status);
      } catch (err) {
        console.error('Status subscriber error:', err);
      }
    }
  }
}

export const wsService = new WebSocketService();
