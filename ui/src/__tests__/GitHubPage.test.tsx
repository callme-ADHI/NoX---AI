import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { GitHubPage } from '../pages/GitHubPage';
import { api } from '../services/api';
import { GitHubRepo, GitHubRun } from '../types';

vi.mock('../services/api', () => ({
  api: {
    getGitHubRepos: vi.fn(),
    addGitHubRepo: vi.fn(),
    removeGitHubRepo: vi.fn(),
    getGitHubRuns: vi.fn(),
  },
}));

describe('GitHubPage Component', () => {
  const mockRepos: GitHubRepo[] = [
    {
      repo_name: 'callme-ADHI/NoX---AI',
      is_private: true,
      watch_actions: true,
      watch_prs: true,
      created_at: '2026-11-20T00:00:00Z',
    },
  ];

  const mockRuns: GitHubRun[] = [
    {
      id: 'run_1001',
      repo_name: 'callme-ADHI/NoX---AI',
      workflow_name: 'CI Workflow',
      head_branch: 'session-n',
      head_sha: '47fbd6d1234567890',
      status: 'completed',
      conclusion: 'failure',
      failure_step: 'Run Pytest Suite',
      triage_summary: 'Root cause: ModuleNotFoundError: No module named pytest in worker environment.',
      html_url: 'https://github.com/callme-ADHI/NoX---AI/actions/runs/1001',
      run_started_at: '2026-11-22T08:00:00Z',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getGitHubRepos).mockResolvedValue(mockRepos);
    vi.mocked(api.getGitHubRuns).mockResolvedValue(mockRuns);
  });

  it('renders watched repositories and action runs with failure step', async () => {
    render(<GitHubPage />);

    expect(screen.getByRole('heading', { name: /GitHub & Actions Intelligence/i })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getAllByText('callme-ADHI/NoX---AI').length).toBeGreaterThan(0);
      expect(screen.getByText('CI Workflow')).toBeInTheDocument();
      expect(screen.getByText(/Failed at step: Run Pytest Suite/i)).toBeInTheDocument();
    });
  });

  it('expands run details and reveals automated triage summary on click', async () => {
    render(<GitHubPage />);

    await waitFor(() => {
      expect(screen.getByText('CI Workflow')).toBeInTheDocument();
    });

    const runCard = screen.getByText('CI Workflow');
    fireEvent.click(runCard);

    await waitFor(() => {
      expect(screen.getByText(/Automated Actions Triage Summary/i)).toBeInTheDocument();
      expect(screen.getByText(/ModuleNotFoundError: No module named pytest/i)).toBeInTheDocument();
    });
  });

  it('submits add repo modal and calls api.addGitHubRepo', async () => {
    vi.mocked(api.addGitHubRepo).mockResolvedValue({ ok: true, repo_name: 'octocat/Hello-World' });

    render(<GitHubPage />);

    const watchBtn = screen.getByRole('button', { name: /Watch Repo/i });
    fireEvent.click(watchBtn);

    expect(screen.getByRole('heading', { name: 'Watch GitHub Repository' })).toBeInTheDocument();

    const input = screen.getByPlaceholderText(/e\.g\. callme-ADHI/i);
    fireEvent.change(input, { target: { value: 'octocat/Hello-World' } });

    const submitBtn = screen.getByRole('button', { name: 'Watch Repository' });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(api.addGitHubRepo).toHaveBeenCalledWith('octocat/Hello-World', false, true, true);
    });
  });

  it('opens delete modal and calls api.removeGitHubRepo on confirm', async () => {
    vi.mocked(api.removeGitHubRepo).mockResolvedValue({ ok: true });

    render(<GitHubPage />);

    await waitFor(() => {
      expect(screen.getAllByText('callme-ADHI/NoX---AI').length).toBeGreaterThan(0);
    });

    const deleteBtn = screen.getByTitle(/Stop watching repository/i);
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(screen.getByText(/Stop watching callme-ADHI\/NoX---AI\?/i)).toBeInTheDocument();
    });

    const confirmBtn = screen.getAllByRole('button', { name: 'Remove' }).slice(-1)[0];
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(api.removeGitHubRepo).toHaveBeenCalledWith('callme-ADHI', 'NoX---AI');
    });
  });
});
