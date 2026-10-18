import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { WebSocketService } from '../services/websocket';
import { WebSocketEvent } from '../types';

describe('WebSocketService', () => {
  let wsService: WebSocketService;

  beforeEach(() => {
    vi.useFakeTimers();
    wsService = new WebSocketService();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('notifies subscribers when events arrive and preserves ordering', () => {
    const received: WebSocketEvent[] = [];
    wsService.subscribe((evt) => {
      received.push(evt);
    });

    const evt1: WebSocketEvent = {
      id: 'e1',
      ts: '2026-10-18T10:00:00Z',
      type: 'token',
      data: { text: 'Hello' },
    };
    const evt2: WebSocketEvent = {
      id: 'e2',
      ts: '2026-10-18T10:00:01Z',
      type: 'token',
      data: { text: ' world' },
    };
    const evt3: WebSocketEvent = {
      id: 'e3',
      ts: '2026-10-18T10:00:02Z',
      type: 'done',
      data: { finish_reason: 'stop' },
    };

    wsService.processIncomingEvent(evt1);
    wsService.processIncomingEvent(evt2);
    wsService.processIncomingEvent(evt3);

    expect(received).toHaveLength(3);
    expect(received[0].id).toBe('e1');
    expect(received[1].id).toBe('e2');
    expect(received[2].id).toBe('e3');
    expect(received.map((e) => e.data.text || '')).toEqual(['Hello', ' world', '']);
  });

  it('deduplicates events with already-seen IDs', () => {
    const received: WebSocketEvent[] = [];
    wsService.subscribe((evt) => {
      received.push(evt);
    });

    const evt: WebSocketEvent = {
      id: 'dup_id_100',
      ts: '2026-10-18T10:00:00Z',
      type: 'status_changed',
      data: { status: 'ready' },
    };

    const firstResult = wsService.processIncomingEvent(evt);
    const secondResult = wsService.processIncomingEvent(evt);

    expect(firstResult).toBe(true);
    expect(secondResult).toBe(false); // Rejected as duplicate
    expect(received).toHaveLength(1);
  });

  it('replays buffered events in chronological order', () => {
    wsService.processIncomingEvent({
      id: 'e10',
      ts: '2026-10-18T10:00:10Z',
      type: 'node_started',
      data: { node_id: 'n1' },
    });
    wsService.processIncomingEvent({
      id: 'e20',
      ts: '2026-10-18T10:00:20Z',
      type: 'node_done',
      data: { node_id: 'n1' },
    });
    wsService.processIncomingEvent({
      id: 'e30',
      ts: '2026-10-18T10:00:30Z',
      type: 'node_started',
      data: { node_id: 'n2' },
    });

    // Full replay
    const allEvents = wsService.replay();
    expect(allEvents.map((e) => e.id)).toEqual(['e10', 'e20', 'e30']);

    // Replay since timestamp
    const filteredEvents = wsService.replay('2026-10-18T10:00:15Z');
    expect(filteredEvents.map((e) => e.id)).toEqual(['e20', 'e30']);
  });

  it('notifies status listeners on connection state changes', () => {
    const statusChanges: boolean[] = [];
    wsService.onStatusChange((status) => {
      statusChanges.push(status);
    });

    expect(statusChanges).toEqual([false]); // Initial state
  });
});
