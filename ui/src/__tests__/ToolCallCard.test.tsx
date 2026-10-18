
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ToolCallCard, redactSecrets } from '../components/ToolCallCard';
import { ToolCallItem } from '../types';

describe('ToolCallCard and Redaction', () => {
  it('redactSecrets function correctly masks API keys, tokens, and passwords', () => {
    const rawArgs = {
      api_key: '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',
      password: 'SuperSecretPassword123!',
      token: 'ghp_abcdefghijklmnopqrstuvwxyz123456',
      normal_arg: 'safe_value',
      nested: {
        authorization: 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz',
        auth_header: 'Bearer secret_token_value',
      },
      text: 'My key is sk-1234567890abcdef and nvapi-abcde12345',
    };

    const redacted = redactSecrets(rawArgs) as Record<string, unknown>;
    const nested = redacted.nested as Record<string, unknown>;

    expect(redacted.api_key).toBe('[REDACTED]');
    expect(redacted.password).toBe('[REDACTED]');
    expect(redacted.token).toBe('[REDACTED]');
    expect(redacted.normal_arg).toBe('safe_value');
    expect(nested.authorization).toBe('[REDACTED]');
    expect(nested.auth_header as string).toContain('Bearer [REDACTED]');
    expect(redacted.text as string).toContain('[REDACTED_API_KEY]');
    expect(redacted.text as string).not.toContain('sk-1234567890abcdef');
    expect(redacted.text as string).not.toContain('nvapi-abcde12345');
  });

  it('renders tool card and redacts sensitive data in displayed arguments', () => {
    const sensitiveTool: ToolCallItem = {
      id: 'tool_1',
      name: 'fetch_api',
      tier: 'read',
      arguments: {
        endpoint: 'https://api.nvidia.com/v1/chat',
        api_key: 'nvapi-REAL_SECRET_KEY_HERE_123',
        token: 'secret_jwt_token',
      },
      result_summary: 'Success: fetched status with authorization Bearer secret123',
      duration_ms: 120,
      ok: true,
      expanded: true,
    };

    render(<ToolCallCard tool={sensitiveTool} />);

    expect(screen.getByText('fetch_api')).toBeInTheDocument();
    expect(screen.getByText('read')).toBeInTheDocument();
    expect(screen.getByText('120ms')).toBeInTheDocument();

    // The rendered arguments pre-block must contain [REDACTED] and NOT the raw secret
    const argsBlock = screen.getByText(/"api_key": "\[REDACTED\]"/i);
    expect(argsBlock).toBeInTheDocument();
    expect(screen.queryByText(/REAL_SECRET_KEY_HERE_123/)).not.toBeInTheDocument();

    // The result output must also be redacted
    expect(screen.getByText(/Bearer \[REDACTED\]/)).toBeInTheDocument();
  });

  it('copies redacted content to clipboard when copy button is clicked', () => {
    const writeTextMock = vi.fn();
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    });

    const sensitiveTool: ToolCallItem = {
      id: 'tool_2',
      name: 'database_query',
      arguments: {
        query: 'SELECT * FROM users',
        password: 'mypassword',
      },
      expanded: true,
    };

    render(<ToolCallCard tool={sensitiveTool} />);

    const copyBtn = screen.getByRole('button', { name: /copy/i });
    fireEvent.click(copyBtn);

    expect(writeTextMock).toHaveBeenCalled();
    const copiedText = writeTextMock.mock.calls[0][0];
    expect(copiedText).toContain('"password": "[REDACTED]"');
    expect(copiedText).not.toContain('mypassword');
  });
});
