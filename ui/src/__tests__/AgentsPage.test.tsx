import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AgentsPage } from '../pages/AgentsPage';
import { api } from '../services/api';
import { AgentInfo, PresetInfo } from '../types';

vi.mock('../services/api', () => ({
  api: {
    getAgents: vi.fn(),
    getPresets: vi.fn(),
    toggleAgent: vi.fn(),
    applyPreset: vi.fn(),
    runAgent: vi.fn(),
  },
}));

describe('AgentsPage Component', () => {
  const mockPresets: PresetInfo[] = [
    {
      name: 'balanced',
      description: 'Default balanced profile with local-first priority',
      active_agents: ['triage_agent'],
      active_connectors: ['gmail'],
      is_active: true,
    },
    {
      name: 'power_researcher',
      description: 'High throughput intelligence analysis',
      active_agents: ['triage_agent', 'researcher'],
      active_connectors: ['gmail', 'telegram'],
      is_active: false,
    },
  ];

  const mockAgents: AgentInfo[] = [
    {
      id: 'agent_1',
      name: 'triage_agent',
      mode: 'on_demand',
      enabled: true,
      budget_tokens: 50000,
      budget_cost_usd: 0.15,
      status: 'idle',
      prompt_template: 'Analyze incoming notifications and prioritize.',
      last_run_at: '2026-11-22T09:00:00Z',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getPresets).mockResolvedValue(mockPresets);
    vi.mocked(api.getAgents).mockResolvedValue(mockAgents);
  });

  it('renders presets banner and agent cards with budget information', async () => {
    render(<AgentsPage />);

    expect(screen.getByRole('heading', { name: /Autonomous Agents & Presets/i })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('balanced')).toBeInTheDocument();
      expect(screen.getByText('power_researcher')).toBeInTheDocument();
      expect(screen.getByText('triage_agent')).toBeInTheDocument();
      expect(screen.getByText(/50,000 tokens \(\$0.15\)/i)).toBeInTheDocument();
    });
  });

  it('applies an inactive preset on click and refreshes data', async () => {
    vi.mocked(api.applyPreset).mockResolvedValue({ ok: true, active_preset: 'power_researcher' });

    render(<AgentsPage />);

    await waitFor(() => {
      expect(screen.getByText('power_researcher')).toBeInTheDocument();
    });

    const presetBtn = screen.getByRole('button', { name: /power_researcher/i });
    fireEvent.click(presetBtn);

    await waitFor(() => {
      expect(api.applyPreset).toHaveBeenCalledWith('power_researcher');
    });
  });

  it('toggles agent enabled status when toggle icon is clicked', async () => {
    vi.mocked(api.toggleAgent).mockResolvedValue({ ok: true, name: 'triage_agent', enabled: false });

    render(<AgentsPage />);

    await waitFor(() => {
      expect(screen.getByText('triage_agent')).toBeInTheDocument();
    });

    const toggleBtn = screen.getByTitle('Disable agent');
    fireEvent.click(toggleBtn);

    await waitFor(() => {
      expect(api.toggleAgent).toHaveBeenCalledWith('triage_agent');
    });
  });

  it('opens execute agent modal and runs agent with prompt input', async () => {
    vi.mocked(api.runAgent).mockResolvedValue({
      status: 'success',
      run_id: 'subrun_42',
      tokens_used: 1250,
    });

    render(<AgentsPage />);

    await waitFor(() => {
      expect(screen.getByText('triage_agent')).toBeInTheDocument();
    });

    const runBtn = screen.getByRole('button', { name: /Run On-Demand/i });
    fireEvent.click(runBtn);

    expect(screen.getByRole('heading', { name: /Execute Agent:/i })).toBeInTheDocument();

    const textarea = screen.getByPlaceholderText(/Enter prompt or leave empty/i);
    fireEvent.change(textarea, { target: { value: 'Scan inbox for urgent alerts' } });

    const executeBtn = screen.getByRole('button', { name: 'Execute' });
    fireEvent.click(executeBtn);

    await waitFor(() => {
      expect(api.runAgent).toHaveBeenCalledWith('triage_agent', 'Scan inbox for urgent alerts');
      expect(screen.getByText(/subrun_42/i)).toBeInTheDocument();
    });
  });
});
