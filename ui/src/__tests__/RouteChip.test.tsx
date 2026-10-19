import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RouteChip } from '../components/RouteChip';

describe('RouteChip Component (§F7.1)', () => {
  it('renders local lane with model name and latency', () => {
    const onOpen = vi.fn();
    render(
      <RouteChip
        lane="front_desk"
        modelName="qwen2.5:14b"
        isCloud={false}
        latencyMs={185}
        onOpenTrace={onOpen}
      />
    );

    expect(screen.getByText('Local')).toBeInTheDocument();
    expect(screen.getByText('front desk')).toBeInTheDocument();
    expect(screen.getByText('qwen2.5:14b')).toBeInTheDocument();
    expect(screen.getByText('185ms')).toBeInTheDocument();

    const btn = screen.getByRole('button');
    fireEvent.click(btn);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it('renders cloud badge when isCloud is true', () => {
    render(
      <RouteChip
        lane="cloud"
        modelName="nemotron-super-49b"
        isCloud={true}
        latencyMs={620}
      />
    );

    expect(screen.getByText('Cloud')).toBeInTheDocument();
    expect(screen.getByText('nemotron-super-49b')).toBeInTheDocument();
    expect(screen.getByText('620ms')).toBeInTheDocument();
  });
});
