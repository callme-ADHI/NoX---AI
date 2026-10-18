
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { ApprovalCard } from '../components/ApprovalCard';
import { ApprovalRequest } from '../types';

describe('ApprovalCard Component', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const baseWriteApproval: ApprovalRequest = {
    id: 42,
    tool_name: 'write_file',
    arguments: { path: '/home/boss/test.txt', content: 'hello' },
    reason: 'Create a test file',
    tier: 'write',
    status: 'pending',
  };

  const baseDangerousApproval: ApprovalRequest = {
    id: 99,
    tool_name: 'run_shell',
    arguments: { command: 'rm -rf /tmp/test' },
    reason: 'Clean test folder',
    tier: 'dangerous',
    status: 'pending',
  };

  it('renders write-tier approval card with tool name, args and countdown', () => {
    const onDecision = vi.fn();
    render(<ApprovalCard approval={baseWriteApproval} onDecision={onDecision} />);

    expect(screen.getByText('Action Approval Required')).toBeInTheDocument();
    expect(screen.getByText('write tier')).toBeInTheDocument();
    expect(screen.getByText('write_file')).toBeInTheDocument();
    expect(screen.getByText('Create a test file')).toBeInTheDocument();
    expect(screen.getByText(/5:00/)).toBeInTheDocument();
  });

  it('allows immediate approval for write tier', () => {
    const onDecision = vi.fn();
    render(<ApprovalCard approval={baseWriteApproval} onDecision={onDecision} />);

    const approveBtn = screen.getByRole('button', { name: /approve action/i });
    expect(approveBtn).not.toBeDisabled();
    fireEvent.click(approveBtn);

    expect(onDecision).toHaveBeenCalledWith(42, 'approve');
  });

  it('allows denial for any tier', () => {
    const onDecision = vi.fn();
    render(<ApprovalCard approval={baseWriteApproval} onDecision={onDecision} />);

    const denyBtn = screen.getByRole('button', { name: /deny/i });
    fireEvent.click(denyBtn);

    expect(onDecision).toHaveBeenCalledWith(42, 'deny');
  });

  it('requires typed "confirm" for dangerous tier before approve is enabled', () => {
    const onDecision = vi.fn();
    render(<ApprovalCard approval={baseDangerousApproval} onDecision={onDecision} />);

    expect(screen.getByText('dangerous tier')).toBeInTheDocument();
    const approveBtn = screen.getByRole('button', { name: /approve action/i });
    expect(approveBtn).toBeDisabled();

    // Type something other than confirm
    const input = screen.getByPlaceholderText(/Type 'confirm'.../i);
    fireEvent.change(input, { target: { value: 'yes' } });
    expect(approveBtn).toBeDisabled();

    // Type 'confirm'
    fireEvent.change(input, { target: { value: 'confirm' } });
    expect(approveBtn).not.toBeDisabled();

    fireEvent.click(approveBtn);
    expect(onDecision).toHaveBeenCalledWith(99, 'approve');
  });

  it('automatically denies on timeout when countdown expires', () => {
    const onDecision = vi.fn();
    render(<ApprovalCard approval={baseWriteApproval} onDecision={onDecision} />);

    // Fast-forward 301 seconds
    act(() => {
      vi.advanceTimersByTime(301000);
    });

    expect(onDecision).toHaveBeenCalledWith(42, 'deny');
  });

  it('displays untrusted content warning when flag is set', () => {
    const onDecision = vi.fn();
    const untrustedApproval: ApprovalRequest = {
      ...baseWriteApproval,
      untrusted_warning: true,
    };
    render(<ApprovalCard approval={untrustedApproval} onDecision={onDecision} />);

    expect(screen.getByText(/Security Warning:/i)).toBeInTheDocument();
    expect(
      screen.getByText(/previously inspected untrusted external web content/i)
    ).toBeInTheDocument();
  });
});
