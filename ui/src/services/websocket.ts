/**
 * WebSocket manager with auto-reconnect, keepalive, and event subscription per Section 6.1.
 */

import { WebSocketEvent } from '../types';

export type EventCallback = (event: WebSocketEvent) => void;
export type StatusCallback = (connected: boolean) => void;

class WebSocketService {
  private ws: WebSocket | null = null;
  private subscribers: Set<EventCallback> = new Set();
  private statusSubscribers: Set<StatusCallback> = new Set();
  private reconnectTimeout: number | null = null;
  private pingInterval: number | null = null;
  private backoff = 1000;
  private isExplicitClose = false;
  public isConnected = false;

  public connect(): void {
    this.isExplicitClose = false;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const url = `${protocol}//${host}/ws`;

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
        const msg = JSON.parse(e.data);
        if (msg.type === 'pong') {
          return;
        }
        this.notifySubscribers(msg as WebSocketEvent);
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
    this.pingInterval = window.setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 25000);
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
    this.reconnectTimeout = window.setTimeout(() => {
      this.connect();
    }, this.backoff);
    this.backoff = Math.min(this.backoff * 1.5, 15000);
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
