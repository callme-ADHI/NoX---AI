import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { DeleteConfirmModal } from '../components/DeleteConfirmModal';
import { UndoToast } from '../components/UndoToast';
import { NotificationCenter } from '../components/NotificationCenter';
import { JobEditor } from '../components/JobEditor';
import { api } from '../services/api';
import { Job, JobPlanData } from '../types';

vi.mock('../services/api', () => ({
  api: {
    getJobPlan: vi.fn(),
    generateJobPlan: vi.fn(),
    approveJobPlan: vi.fn(),
    getNotifications: vi.fn(),
    clearNotifications: vi.fn(),
    markNotificationRead: vi.fn(),
  },
}));

vi.mock('../services/websocket', () => ({
  wsService: {
    subscribe: vi.fn(() => vi.fn()),
  },
}));

describe('DeleteConfirmModal Component', () => {
  it('renders confirmation title, message, and triggers confirm on click', () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();

    render(
      <DeleteConfirmModal
        isOpen={true}
        title="Delete Job"
        message="Are you sure you want to delete this job?"
        onConfirm={onConfirm}
        onCancel={onCancel}
      />
    );

    expect(screen.getByText('Delete Job')).toBeInTheDocument();
    expect(screen.getByText(/Are you sure you want to delete this job\?/i)).toBeInTheDocument();

    const deleteBtn = screen.getByRole('button', { name: 'Delete' });
    fireEvent.click(deleteBtn);
    expect(onConfirm).toHaveBeenCalledTimes(1);

    const cancelBtn = screen.getByRole('button', { name: 'Cancel' });
    fireEvent.click(cancelBtn);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

describe('UndoToast Component', () => {
  it('renders message, countdown, and triggers onUndo', () => {
    const onUndo = vi.fn();
    const onDismiss = vi.fn();

    render(
      <UndoToast
        id="toast-1"
        message="Job deleted."
        durationSeconds={10}
        onUndo={onUndo}
        onDismiss={onDismiss}
      />
    );

    expect(screen.getByText('Job deleted.')).toBeInTheDocument();
    expect(screen.getByText('(10s)')).toBeInTheDocument();

    const undoBtn = screen.getByRole('button', { name: /undo/i });
    fireEvent.click(undoBtn);
    expect(onUndo).toHaveBeenCalledTimes(1);
  });
});

describe('NotificationCenter Component', () => {
  const mockNotifications = [
    {
      id: 1,
      job_id: 'backup_job',
      title: 'Daily Backup Completed',
      message: '3/3 folders pushed to GitHub successfully.',
      level: 'success',
      read: 0,
      created_at: new Date().toISOString(),
    },
    {
      id: 2,
      job_id: null,
      title: 'Large File Warning',
      message: 'A file over 50MB was excluded.',
      level: 'warning',
      read: 1,
      created_at: new Date().toISOString(),
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getNotifications).mockResolvedValue(mockNotifications);
  });

  it('renders notifications and allows marking all as read', async () => {
    const onClose = vi.fn();
    render(<NotificationCenter isOpen={true} onClose={onClose} />);

    await waitFor(() => {
      expect(screen.getByText('Daily Backup Completed')).toBeInTheDocument();
      expect(screen.getByText('Large File Warning')).toBeInTheDocument();
    });

    const markAllBtn = screen.getByRole('button', { name: /mark all read/i });
    fireEvent.click(markAllBtn);

    await waitFor(() => {
      expect(api.clearNotifications).toHaveBeenCalled();
    });
  });
});

describe('JobEditor Plan Tab & Approval', () => {
  const sampleJob: Job = {
    id: 'backup_job',
    name: 'Daily Backup',
    description: 'Pushes 3 folders to GitHub',
    schedule_type: 'cron',
    schedule_expr: '0 20 * * *',
    timezone: 'Asia/Kolkata',
    instruction: 'Push ~/Documents/Study to GitHub',
    config: {},
    enabled: true,
    version: 1,
  };

  const samplePlan: JobPlanData = {
    version: 1,
    plan_json: {
      steps: [
        {
          kind: 'git.preflight',
          folder: '~/Documents/Study',
          params: { branch: 'main' },
          timeout_seconds: 30,
        },
        {
          kind: 'git.push',
          folder: '~/Documents/Study',
          params: { branch: 'main', remote: 'origin' },
          timeout_seconds: 120,
        },
      ],
      folders: ['~/Documents/Study'],
    },
    source: 'cloud',
    approved_at: null,
    approved_by: null,
    created_at: new Date().toISOString(),
    folder_summaries: [
      {
        folder: '~/Documents/Study',
        file_count: 42,
        total_size_bytes: 1048576,
        status: 'clean',
      },
    ],
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getJobPlan).mockResolvedValue(samplePlan);
    vi.mocked(api.approveJobPlan).mockResolvedValue({ ok: true, version: 1 });
  });

  it('navigates to Execution Plan tab, displays steps and approves plan', async () => {
    const onSave = vi.fn();
    render(<JobEditor job={sampleJob} onSave={onSave} />);

    // Click Execution Plan tab
    const planTabBtn = screen.getByRole('button', { name: /execution plan/i });
    fireEvent.click(planTabBtn);

    await waitFor(() => {
      expect(screen.getByText('Plan v1')).toBeInTheDocument();
      expect(screen.getByText('Draft (Approval Required)')).toBeInTheDocument();
      expect(screen.getByText('git.preflight')).toBeInTheDocument();
      expect(screen.getByText('git.push')).toBeInTheDocument();
      expect(screen.getByText('42 files')).toBeInTheDocument();
    });

    // Click Approve Plan
    const approveBtn = screen.getByRole('button', { name: /approve plan/i });
    fireEvent.click(approveBtn);

    await waitFor(() => {
      expect(api.approveJobPlan).toHaveBeenCalledWith('backup_job', 1);
    });
  });
});
