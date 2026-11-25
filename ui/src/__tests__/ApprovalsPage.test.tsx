import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ApprovalsPage } from '../pages/ApprovalsPage';
import { api } from '../services/api';
import { ScopedApproval } from '../types';

vi.mock('../services/api', () => ({
  api: {
    getPendingApprovals: vi.fn(),
    resolveScopedApproval: vi.fn(),
  },
}));

describe('ApprovalsPage Component', () => {
  const mockApprovals: ScopedApproval[] = [
    {
      id: 'appr_write_88',
      action: 'file_write',
      scope: 'fs:write',
      payload: { path: '/home/boss/secrets.txt', content_length: 120 },
      created_at: '2026-11-22T09:00:00Z',
      expires_at: '2026-11-22T09:15:00Z',
      status: 'pending',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders pending approval cards with action, scope, and payload', async () => {
    vi.mocked(api.getPendingApprovals).mockResolvedValue(mockApprovals);

    render(<ApprovalsPage />);

    expect(screen.getByRole('heading', { name: /Human Approvals Center/i })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('file_write')).toBeInTheDocument();
      expect(screen.getByText('Scope: fs:write')).toBeInTheDocument();
      expect(screen.getByText(/ID: appr_write_88/i)).toBeInTheDocument();
      expect(screen.getByText(/"path": "\/home\/boss\/secrets\.txt"/i)).toBeInTheDocument();
    });
  });

  it('renders empty state when no pending approvals exist', async () => {
    vi.mocked(api.getPendingApprovals).mockResolvedValue([]);

    render(<ApprovalsPage />);

    await waitFor(() => {
      expect(screen.getByText(/No pending approvals\. All protected actions are cleared or idle\./i)).toBeInTheDocument();
    });
  });

  it('resolves approval with approve decision and displays generated token', async () => {
    vi.mocked(api.getPendingApprovals).mockResolvedValue(mockApprovals);
    vi.mocked(api.resolveScopedApproval).mockResolvedValue({
      ok: true,
      status: 'approved',
      token: 'tok_sec_12345678abcdef',
    });

    render(<ApprovalsPage />);

    await waitFor(() => {
      expect(screen.getByText('file_write')).toBeInTheDocument();
    });

    const approveBtn = screen.getByRole('button', { name: /Approve & Issue Token/i });
    fireEvent.click(approveBtn);

    await waitFor(() => {
      expect(api.resolveScopedApproval).toHaveBeenCalledWith('appr_write_88', 'approve');
      expect(screen.getByText(/Approval appr_write_88 granted/i)).toBeInTheDocument();
      expect(screen.getByText(/Token: tok_sec_12345678abcdef/i)).toBeInTheDocument();
    });
  });

  it('resolves approval with deny decision and displays rejection banner', async () => {
    vi.mocked(api.getPendingApprovals).mockResolvedValue(mockApprovals);
    vi.mocked(api.resolveScopedApproval).mockResolvedValue({
      ok: true,
      status: 'denied',
    });

    render(<ApprovalsPage />);

    await waitFor(() => {
      expect(screen.getByText('file_write')).toBeInTheDocument();
    });

    const denyBtn = screen.getByRole('button', { name: /Deny Action/i });
    fireEvent.click(denyBtn);

    await waitFor(() => {
      expect(api.resolveScopedApproval).toHaveBeenCalledWith('appr_write_88', 'deny');
      expect(screen.getByText(/Approval appr_write_88 denied\. Action blocked\./i)).toBeInTheDocument();
    });
  });
});
