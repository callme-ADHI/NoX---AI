import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConnectorsPage } from '../pages/ConnectorsPage';
import { api } from '../services/api';
import { ConnectorInfo } from '../types';

vi.mock('../services/api', () => ({
  api: {
    getConnectors: vi.fn(),
    syncConnector: vi.fn(),
    disconnectConnector: vi.fn(),
  },
}));

describe('ConnectorsPage Component', () => {
  const mockConnectors: ConnectorInfo[] = [
    {
      name: 'gmail',
      type: 'mail',
      healthy: true,
      status: 'HEALTHY',
      item_count: 42,
      last_sync_at: '2026-11-22T10:00:00Z',
    },
    {
      name: 'imap_fastmail',
      type: 'mail',
      healthy: false,
      status: 'NOT_CONFIGURED',
      item_count: 0,
      last_sync_at: null,
      error: 'Missing IMAP host or password',
    },
    {
      name: 'telegram_feed',
      type: 'social',
      healthy: true,
      status: 'HEALTHY',
      item_count: 15,
      last_sync_at: '2026-11-22T11:00:00Z',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getConnectors).mockResolvedValue(mockConnectors);
  });

  it('renders connectors list with honest statuses and ingested counts', async () => {
    render(<ConnectorsPage />);

    expect(screen.getByRole('heading', { name: /Connectors & Ingestion/i })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('gmail')).toBeInTheDocument();
      expect(screen.getByText('imap_fastmail')).toBeInTheDocument();
      expect(screen.getByText('telegram_feed')).toBeInTheDocument();
    });

    expect(screen.getAllByText('HEALTHY').length).toBe(2);
    expect(screen.getByText('NOT_CONFIGURED')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('Missing IMAP host or password')).toBeInTheDocument();
    expect(screen.getByText(/Need to connect new services\? Pair via CLI/i)).toBeInTheDocument();
  });

  it('triggers sync on "Sync Now" click and displays success banner', async () => {
    vi.mocked(api.syncConnector).mockResolvedValue({
      ok: true,
      connector: 'gmail',
      stats: { synced: 5, new: 2 },
    });

    render(<ConnectorsPage />);

    await waitFor(() => {
      expect(screen.getByText('gmail')).toBeInTheDocument();
    });

    const syncButtons = screen.getAllByRole('button', { name: /Sync Now/i });
    fireEvent.click(syncButtons[0]);

    await waitFor(() => {
      expect(api.syncConnector).toHaveBeenCalledWith('gmail');
      expect(screen.getByText(/Synced gmail successfully/i)).toBeInTheDocument();
    });
  });

  it('opens disconnect modal and executes disconnect on confirm', async () => {
    vi.mocked(api.disconnectConnector).mockResolvedValue({ ok: true });

    render(<ConnectorsPage />);

    await waitFor(() => {
      expect(screen.getByText('gmail')).toBeInTheDocument();
    });

    const disconnectButtons = screen.getAllByRole('button', { name: /Disconnect/i });
    fireEvent.click(disconnectButtons[0]);

    // Modal should now be open
    await waitFor(() => {
      expect(screen.getByText('Disconnect gmail?')).toBeInTheDocument();
    });

    // Find confirm button inside modal
    const modalDisconnectBtn = screen.getAllByRole('button', { name: 'Disconnect' }).slice(-1)[0];
    fireEvent.click(modalDisconnectBtn);

    await waitFor(() => {
      expect(api.disconnectConnector).toHaveBeenCalledWith('gmail');
      expect(screen.getByText(/Disconnected gmail and cleared secrets/i)).toBeInTheDocument();
    });
  });
});
