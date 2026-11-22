import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MailPage } from '../pages/MailPage';
import { api } from '../services/api';
import { MailItem } from '../types';

vi.mock('../services/api', () => ({
  api: {
    getMail: vi.fn(),
    draftMailReply: vi.fn(),
    deleteMail: vi.fn(),
    restoreMail: vi.fn(),
  },
}));

describe('MailPage Component', () => {
  const mockMails: MailItem[] = [
    {
      id: 'mail_1',
      connector_id: 'gmail',
      sender: 'alice@example.com',
      recipient: 'user@local',
      subject: 'Project Architecture Review',
      snippet: 'Here is the summary of the latest microservice changes.',
      category: 'primary',
      date: '2026-11-22T08:30:00Z',
      is_read: false,
      is_phishing: false,
    },
    {
      id: 'mail_2',
      connector_id: 'imap_fastmail',
      sender: 'security@phish-bank.com',
      recipient: 'user@local',
      subject: 'URGENT: Password Reset Required',
      snippet: 'Click this link immediately to verify your credentials.',
      category: 'phishing',
      date: '2026-11-22T09:00:00Z',
      is_read: false,
      is_phishing: true,
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getMail).mockResolvedValue(mockMails);
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    });
  });

  it('renders email list with phishing detection banner and details', async () => {
    render(<MailPage />);

    expect(screen.getByRole('heading', { name: /Unified Mail & Triage/i })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Project Architecture Review')).toBeInTheDocument();
      expect(screen.getByText('URGENT: Password Reset Required')).toBeInTheDocument();
    });

    expect(screen.getByText(/SUSPECTED PHISHING: Detected suspicious keywords/i)).toBeInTheDocument();
    expect(screen.getByText('alice@example.com')).toBeInTheDocument();
    expect(screen.getByText('via gmail')).toBeInTheDocument();
  });

  it('filters by category tabs and reloads mail', async () => {
    render(<MailPage />);

    await waitFor(() => {
      expect(api.getMail).toHaveBeenCalledWith({ category: undefined, unread_only: false });
    });

    const primaryTab = screen.getByRole('button', { name: 'Primary' });
    fireEvent.click(primaryTab);

    await waitFor(() => {
      expect(api.getMail).toHaveBeenCalledWith({ category: 'primary', unread_only: false });
    });
  });

  it('deletes an email, shows undo toast, and restores email on undo click', async () => {
    vi.mocked(api.deleteMail).mockResolvedValue({ ok: true });
    vi.mocked(api.restoreMail).mockResolvedValue({ ok: true });

    render(<MailPage />);

    await waitFor(() => {
      expect(screen.getByText('Project Architecture Review')).toBeInTheDocument();
    });

    const deleteButtons = screen.getAllByTitle(/Soft Delete Email/i);
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(api.deleteMail).toHaveBeenCalledWith('mail_1');
      expect(screen.getByText(/Deleted "Project Architecture Review"/i)).toBeInTheDocument();
    });

    const undoBtn = screen.getByRole('button', { name: 'Undo' });
    fireEvent.click(undoBtn);

    await waitFor(() => {
      expect(api.restoreMail).toHaveBeenCalledWith('mail_1');
    });
  });

  it('generates an AI draft reply and copies it to clipboard', async () => {
    vi.mocked(api.draftMailReply).mockResolvedValue({
      mail_id: 'mail_1',
      to: 'alice@example.com',
      subject: 'Re: Project Architecture Review',
      draft_body: 'Thanks Alice, I have reviewed the design and approve the plan.',
    });

    render(<MailPage />);

    await waitFor(() => {
      expect(screen.getByText('Project Architecture Review')).toBeInTheDocument();
    });

    const draftBtn = screen.getAllByTitle(/Generate Draft Reply/i)[0];
    fireEvent.click(draftBtn);

    await waitFor(() => {
      expect(api.draftMailReply).toHaveBeenCalledWith('mail_1');
      expect(screen.getByText('AI Draft Reply')).toBeInTheDocument();
      expect(screen.getByText(/Thanks Alice, I have reviewed the design/i)).toBeInTheDocument();
    });

    const copyBtn = screen.getByRole('button', { name: /Copy Draft/i });
    fireEvent.click(copyBtn);

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      'Thanks Alice, I have reviewed the design and approve the plan.'
    );
  });
});
