import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AuditPage } from '../pages/AuditPage';
import { api } from '../services/api';
import { AuditChainEvent } from '../types';

vi.mock('../services/api', () => ({
  api: {
    getAuditChain: vi.fn(),
    verifyAuditChain: vi.fn(),
  },
}));

describe('AuditPage Component', () => {
  const mockEvents: AuditChainEvent[] = [
    {
      id: 'audit_1',
      sequence_num: 1,
      timestamp: '2026-11-22T08:00:00Z',
      action: 'tool_execution',
      actor: 'agent:triage',
      decision: 'allow',
      summary: 'Read system disk space stats',
      prev_hash: 'GENESIS_00000000000000000000000000000000',
      current_hash: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
      created_at: '2026-11-22T08:00:00Z',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    });
  });

  it('renders cryptographic audit chain events with hashes and decisions', async () => {
    vi.mocked(api.getAuditChain).mockResolvedValue(mockEvents);

    render(<AuditPage />);

    expect(screen.getByRole('heading', { name: /Tamper-Evident Audit Log/i })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('#1')).toBeInTheDocument();
      expect(screen.getByText('tool_execution')).toBeInTheDocument();
      expect(screen.getByText('allow')).toBeInTheDocument();
      expect(screen.getByText('by agent:triage')).toBeInTheDocument();
      expect(screen.getByText(/Read system disk space stats/i)).toBeInTheDocument();
    });
  });

  it('copies SHA-256 current hash to clipboard', async () => {
    vi.mocked(api.getAuditChain).mockResolvedValue(mockEvents);

    render(<AuditPage />);

    await waitFor(() => {
      expect(screen.getByText('#1')).toBeInTheDocument();
    });

    const copyBtn = screen.getByTitle('Copy full SHA-256 hash');
    fireEvent.click(copyBtn);

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    );
  });

  it('executes chain verification and displays verified integrity banner', async () => {
    vi.mocked(api.getAuditChain).mockResolvedValue(mockEvents);
    vi.mocked(api.verifyAuditChain).mockResolvedValue({
      verified: true,
      total_events: 1,
      message: 'All cryptographic links intact',
    });

    render(<AuditPage />);

    await waitFor(() => {
      expect(screen.getByText('#1')).toBeInTheDocument();
    });

    const verifyBtn = screen.getByRole('button', { name: /Verify Cryptographic Chain/i });
    fireEvent.click(verifyBtn);

    await waitFor(() => {
      expect(api.verifyAuditChain).toHaveBeenCalledTimes(1);
      expect(screen.getByText('Audit Log Integrity Verified')).toBeInTheDocument();
      expect(screen.getByText(/All cryptographic links intact \(1 events verified\)/i)).toBeInTheDocument();
    });
  });

  it('displays violation alert when audit chain verification fails', async () => {
    vi.mocked(api.getAuditChain).mockResolvedValue(mockEvents);
    vi.mocked(api.verifyAuditChain).mockResolvedValue({
      verified: false,
      total_events: 0,
      message: 'Hash mismatch detected at sequence #4',
    });

    render(<AuditPage />);

    await waitFor(() => {
      expect(screen.getByText('#1')).toBeInTheDocument();
    });

    const verifyBtn = screen.getByRole('button', { name: /Verify Cryptographic Chain/i });
    fireEvent.click(verifyBtn);

    await waitFor(() => {
      expect(screen.getByText('Integrity Violation Detected')).toBeInTheDocument();
      expect(screen.getByText(/Hash mismatch detected at sequence #4/i)).toBeInTheDocument();
    });
  });
});
