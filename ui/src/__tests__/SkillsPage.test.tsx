import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { SkillsPage } from '../pages/SkillsPage';
import { api } from '../services/api';
import { SkillInfo } from '../types';

vi.mock('../services/api', () => ({
  api: {
    getSkills: vi.fn(),
    enableSkill: vi.fn(),
    disableSkill: vi.fn(),
    installSkill: vi.fn(),
  },
}));

describe('SkillsPage Component', () => {
  const mockSkills: SkillInfo[] = [
    {
      name: 'file_search',
      version: '1.2.0',
      description: 'Fast ripgrep-based local code and text search',
      enabled: true,
      allow_scripts: false,
      scripts: [],
    },
    {
      name: 'python_runner',
      version: '2.0.1',
      description: 'Guarded Python script execution in sandbox',
      enabled: false,
      allow_scripts: true,
      scripts: ['run_py.sh'],
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSkills).mockResolvedValue(mockSkills);
  });

  it('renders skills cards with version, script guard status, and executables', async () => {
    render(<SkillsPage />);

    expect(screen.getByRole('heading', { name: /Skills & Tool Integrations/i })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('file_search')).toBeInTheDocument();
      expect(screen.getByText('v1.2.0')).toBeInTheDocument();
      expect(screen.getByText('No Scripts')).toBeInTheDocument();
      expect(screen.getByText('python_runner')).toBeInTheDocument();
      expect(screen.getByText('v2.0.1')).toBeInTheDocument();
      expect(screen.getByText('Allowed (Guarded)')).toBeInTheDocument();
      expect(screen.getByText('run_py.sh')).toBeInTheDocument();
    });
  });

  it('toggles an enabled skill to disabled and calls api.disableSkill', async () => {
    vi.mocked(api.disableSkill).mockResolvedValue({ ok: true });

    render(<SkillsPage />);

    await waitFor(() => {
      expect(screen.getByText('file_search')).toBeInTheDocument();
    });

    const disableBtn = screen.getByTitle('Disable skill');
    fireEvent.click(disableBtn);

    await waitFor(() => {
      expect(api.disableSkill).toHaveBeenCalledWith('file_search');
    });
  });

  it('toggles a disabled skill to enabled and calls api.enableSkill', async () => {
    vi.mocked(api.enableSkill).mockResolvedValue({ ok: true });

    render(<SkillsPage />);

    await waitFor(() => {
      expect(screen.getByText('python_runner')).toBeInTheDocument();
    });

    const enableBtn = screen.getByTitle('Enable skill');
    fireEvent.click(enableBtn);

    await waitFor(() => {
      expect(api.enableSkill).toHaveBeenCalledWith('python_runner');
    });
  });

  it('submits install untrusted skill modal with security scan staging', async () => {
    vi.mocked(api.installSkill).mockResolvedValue({
      status: 'staged',
      security_warnings: [],
      installed_path: '/home/boss/.local/share/nox/skills/my_tool',
    });

    render(<SkillsPage />);

    const installModalBtn = screen.getAllByRole('button', { name: /Install Skill/i })[0];
    fireEvent.click(installModalBtn);

    expect(screen.getByRole('heading', { name: 'Install Untrusted Skill' })).toBeInTheDocument();

    const input = screen.getByPlaceholderText(/e\.g\. \.\/community_skills/i);
    fireEvent.change(input, { target: { value: './community_skills/my_tool' } });

    const submitBtn = screen.getAllByRole('button', { name: 'Install Skill' }).slice(-1)[0];
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(api.installSkill).toHaveBeenCalledWith('./community_skills/my_tool', false);
      expect(screen.getByText(/"status": "staged"/i)).toBeInTheDocument();
    });
  });
});
