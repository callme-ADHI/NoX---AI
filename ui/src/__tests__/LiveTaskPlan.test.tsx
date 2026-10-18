
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LiveTaskPlan } from '../components/LiveTaskPlan';
import { TaskPlan } from '../types';

describe('LiveTaskPlan Component', () => {
  const samplePlan: TaskPlan = {
    run_id: 'run_test_123',
    goal: 'Gather Kerala tech news and summarize opportunities',
    overall_progress: 33,
    started_at: 1700000000,
    nodes: [
      {
        id: 'node_1',
        title: 'Fetch startup feeds',
        objective: 'Query RSS and search recent announcements',
        kind: 'fetch',
        depends_on: [],
        status: 'done',
        summary: 'Found 14 relevant articles',
        sources: ['https://example.com/news1', 'https://example.com/news2'],
        duration_ms: 1250,
      },
      {
        id: 'node_2',
        title: 'Filter opportunities',
        objective: 'Extract eligibility criteria and deadlines',
        kind: 'research',
        depends_on: ['node_1'],
        status: 'running',
      },
      {
        id: 'node_3',
        title: 'Synthesize report',
        objective: 'Format markdown with verified citations',
        kind: 'compute',
        depends_on: ['node_2'],
        status: 'pending',
      },
    ],
  };

  it('renders plan goal, node count, and progress bar', () => {
    render(<LiveTaskPlan plan={samplePlan} />);

    expect(screen.getByText(/Autonomous Task Plan/i)).toBeInTheDocument();
    expect(screen.getByText(/Goal: Gather Kerala tech news/i)).toBeInTheDocument();
    expect(screen.getByText(/DAG: 1\/3 completed/i)).toBeInTheDocument();
    expect(screen.getByText('33%')).toBeInTheDocument();
  });

  it('renders all nodes with titles, kinds, and status icons', () => {
    render(<LiveTaskPlan plan={samplePlan} />);

    expect(screen.getByText('Fetch startup feeds')).toBeInTheDocument();
    expect(screen.getByText('Filter opportunities')).toBeInTheDocument();
    expect(screen.getByText('Synthesize report')).toBeInTheDocument();

    expect(screen.getByText('fetch')).toBeInTheDocument();
    expect(screen.getByText('research')).toBeInTheDocument();
    expect(screen.getByText('compute')).toBeInTheDocument();

    expect(screen.getByText('1.3s')).toBeInTheDocument();
  });

  it('expands a node to reveal objective, summary, dependencies, and sources', () => {
    render(<LiveTaskPlan plan={samplePlan} />);

    // Initially details are not shown
    expect(screen.queryByText(/Found 14 relevant articles/i)).not.toBeInTheDocument();

    // Click node 1 to expand
    fireEvent.click(screen.getByText('Fetch startup feeds'));

    expect(screen.getByText(/Query RSS and search recent announcements/i)).toBeInTheDocument();
    expect(screen.getByText(/Found 14 relevant articles/i)).toBeInTheDocument();
    expect(screen.getByText(/Sources Verified:/i)).toBeInTheDocument();
    expect(screen.getByText('https://example.com/news1')).toBeInTheDocument();
  });

  it('reflects live updates when node statuses transition to done', () => {
    const { rerender } = render(<LiveTaskPlan plan={samplePlan} />);

    expect(screen.getByText('33%')).toBeInTheDocument();

    // Simulate node 2 finishing
    const updatedPlan: TaskPlan = {
      ...samplePlan,
      overall_progress: 67,
      nodes: [
        samplePlan.nodes[0],
        {
          ...samplePlan.nodes[1],
          status: 'done',
          summary: 'Filtered down to 4 events',
          duration_ms: 800,
        },
        samplePlan.nodes[2],
      ],
    };

    rerender(<LiveTaskPlan plan={updatedPlan} />);

    expect(screen.getByText('67%')).toBeInTheDocument();
    expect(screen.getByText(/DAG: 2\/3 completed/i)).toBeInTheDocument();
  });

  it('renders historical plan with failed and skipped nodes', () => {
    const historicalPlan: TaskPlan = {
      run_id: 'run_hist_456',
      goal: 'Autonomous backup scan',
      overall_progress: 50,
      started_at: 1690000000,
      finished: true,
      nodes: [
        {
          id: 'n1',
          title: 'Check disk',
          objective: 'Inspect /home',
          kind: 'action',
          depends_on: [],
          status: 'failed',
          summary: 'Permission denied',
        },
        {
          id: 'n2',
          title: 'Copy files',
          objective: 'Rsync files',
          kind: 'action',
          depends_on: ['n1'],
          status: 'skipped',
        },
      ],
    };

    render(<LiveTaskPlan plan={historicalPlan} />);

    expect(screen.getByText('Check disk')).toBeInTheDocument();
    expect(screen.getByText('Copy files')).toBeInTheDocument();
    expect(screen.getByText('⊘')).toBeInTheDocument();
  });
});
