import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { TasksPage } from '../pages/TasksPage';
import { api } from '../services/api';
import { Task } from '../types';

vi.mock('../services/api', () => ({
  api: {
    getTasks: vi.fn(),
    createTask: vi.fn(),
    cancelTask: vi.fn(),
    resolveApproval: vi.fn(),
  },
}));

vi.mock('../services/websocket', () => ({
  wsService: {
    subscribe: vi.fn(() => vi.fn()),
  },
}));

describe('TasksPage Component (§F7.5)', () => {
  const mockTasks: Task[] = [
    {
      id: 'task_1',
      title: 'Vulnerability scan',
      prompt: 'Check open ports',
      lane: 'local',
      priority: 'high',
      status: 'running',
      progress: 0.65,
      created_at: new Date().toISOString(),
    },
    {
      id: 'task_2',
      title: 'Disk backup',
      prompt: 'Backup user directory',
      lane: 'local',
      priority: 'urgent',
      status: 'waiting_approval',
      progress: 0.2,
      approval_id: 101,
      created_at: new Date().toISOString(),
    },
    {
      id: 'task_3',
      title: 'Fetch latest threat intel',
      prompt: 'Read RSS feed',
      lane: 'cloud',
      priority: 'normal',
      status: 'done',
      progress: 1.0,
      created_at: new Date().toISOString(),
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getTasks).mockResolvedValue(mockTasks);
  });

  it('renders tasks table with titles, lane, and progress', async () => {
    render(<TasksPage />);

    await waitFor(() => {
      expect(screen.getByText('Vulnerability scan')).toBeInTheDocument();
      expect(screen.getByText('Disk backup')).toBeInTheDocument();
      expect(screen.getByText('Fetch latest threat intel')).toBeInTheDocument();
    });

    expect(screen.getByText('65%')).toBeInTheDocument();
    expect(screen.getByText('20%')).toBeInTheDocument();
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  it('filters by tab status', async () => {
    render(<TasksPage />);

    await waitFor(() => {
      expect(screen.getByText('Vulnerability scan')).toBeInTheDocument();
    });

    // Click Waiting Approval tab
    const waitingTab = screen.getByRole('button', { name: /waiting approval/i });
    fireEvent.click(waitingTab);

    expect(screen.getByText('Disk backup')).toBeInTheDocument();
    expect(screen.queryByText('Vulnerability scan')).not.toBeInTheDocument();
  });

  it('cancels active task on cancel button click', async () => {
    vi.mocked(api.cancelTask).mockResolvedValue({ ok: true, status: 'cancelled' });
    render(<TasksPage />);

    await waitFor(() => {
      expect(screen.getByText('Vulnerability scan')).toBeInTheDocument();
    });

    const cancelBtn = screen.getByRole('button', { name: /cancel/i });
    fireEvent.click(cancelBtn);

    expect(api.cancelTask).toHaveBeenCalledWith('task_1');
  });

  it('approves parked task on approve button click', async () => {
    vi.mocked(api.resolveApproval).mockResolvedValue({ ok: true });
    render(<TasksPage />);

    await waitFor(() => {
      expect(screen.getByText('Disk backup')).toBeInTheDocument();
    });

    const approveBtn = screen.getByRole('button', { name: /approve/i });
    fireEvent.click(approveBtn);

    expect(api.resolveApproval).toHaveBeenCalledWith(101, 'approve');
  });
});
