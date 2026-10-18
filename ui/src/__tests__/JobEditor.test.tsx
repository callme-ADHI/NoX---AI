
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { JobEditor } from '../components/JobEditor';
import { Job, JobVersion } from '../types';

describe('JobEditor Component', () => {
  const sampleJob: Job = {
    id: 'daily-briefing',
    name: 'daily-briefing',
    description: 'Generates daily morning intelligence briefing',
    schedule_type: 'cron',
    schedule_expr: '0 9 * * *',
    timezone: 'Asia/Kolkata',
    instruction: 'Fetch top cybersecurity and tech news and summarize in markdown',
    config: {},
    enabled: true,
    version: 2,
  };

  const sampleVersions: JobVersion[] = [
    {
      id: 1,
      job_id: 'daily-briefing',
      version: 1,
      snapshot_json: '{}',
      changed_by: 'owner',
      change_note: 'Initial schedule at 8am',
      created_at: '2026-10-15T08:00:00Z',
    },
    {
      id: 2,
      job_id: 'daily-briefing',
      version: 2,
      snapshot_json: '{}',
      changed_by: 'agent',
      change_note: 'Moved to 9am per prompt',
      created_at: '2026-10-16T09:00:00Z',
    },
  ];

  it('renders initial job details and schedule preview', () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<JobEditor job={sampleJob} versions={sampleVersions} onSave={onSave} />);

    expect(screen.getByDisplayValue('daily-briefing')).toBeInTheDocument();
    expect(screen.getByDisplayValue('0 9 * * *')).toBeInTheDocument();
    expect(
      screen.getByDisplayValue(/Fetch top cybersecurity and tech news/i)
    ).toBeInTheDocument();

    // Cron preview
    expect(screen.getByText(/Preview: Every day at 09:00/i)).toBeInTheDocument();
  });

  it('shows validation error when cron expression is invalid', () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<JobEditor job={sampleJob} versions={sampleVersions} onSave={onSave} />);

    const cronInput = screen.getByDisplayValue('0 9 * * *');
    fireEvent.change(cronInput, { target: { value: '0 9 *' } }); // only 3 fields

    expect(screen.getByText(/Error: Expected 5 fields/i)).toBeInTheDocument();
    const saveBtn = screen.getByRole('button', { name: /save changes/i });
    expect(saveBtn).toBeDisabled();
  });

  it('updates preview and saves changes when valid', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<JobEditor job={sampleJob} versions={sampleVersions} onSave={onSave} />);

    const cronInput = screen.getByDisplayValue('0 9 * * *');
    fireEvent.change(cronInput, { target: { value: '*/10 * * * *' } });

    expect(screen.getByText(/Preview: Every 10 minutes/i)).toBeInTheDocument();

    const saveBtn = screen.getByRole('button', { name: /save changes/i });
    expect(saveBtn).not.toBeDisabled();
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith({
        name: 'daily-briefing',
        description: 'Generates daily morning intelligence briefing',
        schedule_expr: '*/10 * * * *',
        instruction: 'Fetch top cybersecurity and tech news and summarize in markdown',
      });
    });
  });

  it('renders version history and allows restoring a prior version', () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onRestore = vi.fn().mockResolvedValue(undefined);
    render(
      <JobEditor
        job={sampleJob}
        versions={sampleVersions}
        onSave={onSave}
        onRestore={onRestore}
      />
    );

    // Switch to version history tab
    fireEvent.click(screen.getByRole('button', { name: /version history/i }));

    expect(screen.getByText('v1')).toBeInTheDocument();
    expect(screen.getByText('v2')).toBeInTheDocument();
    expect(screen.getByText(/"Initial schedule at 8am"/i)).toBeInTheDocument();

    // v1 has restore button because current is v2
    const restoreBtn = screen.getByRole('button', { name: /restore/i });
    fireEvent.click(restoreBtn);

    expect(onRestore).toHaveBeenCalledWith(1);
  });
});
