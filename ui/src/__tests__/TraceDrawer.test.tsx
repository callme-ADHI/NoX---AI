import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TraceDrawer } from '../components/TraceDrawer';

describe('TraceDrawer Component (§F7.2)', () => {
  it('does not render when isOpen is false', () => {
    const { container } = render(
      <TraceDrawer isOpen={false} onClose={vi.fn()} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders routing summary, intent analysis, and tools when open', () => {
    const onClose = vi.fn();
    const mockTrace = {
      intent: {
        intent: 'deep_research',
        reply_mode: 'background',
      },
      tools: [
        { name: 'web_search', arguments: { query: 'cybersecurity events' } },
      ],
      taint_flags: 0,
    };

    render(
      <TraceDrawer
        isOpen={true}
        onClose={onClose}
        lane="front_desk"
        modelName="qwen2.5:14b"
        isCloud={false}
        latencyMs={240}
        trace={mockTrace}
      />
    );

    expect(screen.getByText('Execution & Routing Trace')).toBeInTheDocument();
    expect(screen.getByText('deep_research')).toBeInTheDocument();
    expect(screen.getByText('background')).toBeInTheDocument();
    expect(screen.getByText('web_search')).toBeInTheDocument();
    expect(screen.getByText(/Clean — no untrusted inputs/i)).toBeInTheDocument();

    const closeBtn = screen.getByTitle('Close drawer (Esc)');
    fireEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
