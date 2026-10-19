import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CloudStatusIndicator } from '../components/CloudStatusIndicator';
import { CloudHealthInfo } from '../types';

describe('CloudStatusIndicator Component (§F7.3)', () => {
  const mockHealth: CloudHealthInfo = {
    state: 'ACTIVE',
    failures: 0,
    successes: 15,
    circuit_breaker_state: 'CLOSED',
    cooldown_remaining_seconds: 0,
    average_latency_ms: 380,
    total_calls: 15,
  };

  it('renders active status badge when cloud is enabled', () => {
    render(
      <CloudStatusIndicator
        cloudHealth={mockHealth}
        isCloudEnabled={true}
      />
    );

    expect(screen.getByText('Cloud Active')).toBeInTheDocument();
  });

  it('opens popover on click with circuit breaker and rate limit details', () => {
    const onToggle = vi.fn();
    render(
      <CloudStatusIndicator
        cloudHealth={mockHealth}
        isCloudEnabled={true}
        onToggleCloud={onToggle}
      />
    );

    const toggleBtn = screen.getByLabelText('Cloud status popover toggle');
    fireEvent.click(toggleBtn);

    expect(screen.getByText('Cloud Health Monitor')).toBeInTheDocument();
    expect(screen.getByText('30 RPM token bucket')).toBeInTheDocument();
    expect(screen.getByText('10 RPM protected')).toBeInTheDocument();
    expect(screen.getByText('CLOSED')).toBeInTheDocument();

    const disableBtn = screen.getByRole('button', { name: /disable cloud/i });
    fireEvent.click(disableBtn);
    expect(onToggle).toHaveBeenCalledWith(false);
  });
});
