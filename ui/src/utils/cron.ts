/**
 * Cron validation and human readable preview helper.
 */

export interface CronValidationResult {
  isValid: boolean;
  error?: string;
  preview?: string;
}

export function describeCron(cronExpr: string): CronValidationResult {
  if (!cronExpr || !cronExpr.trim()) {
    return { isValid: false, error: 'Schedule expression is required' };
  }

  const parts = cronExpr.trim().split(/\s+/);
  if (parts.length !== 5) {
    return {
      isValid: false,
      error: `Expected 5 fields (min hour day month weekday), got ${parts.length}`,
    };
  }

  const [min, hour, dom, mon, dow] = parts;

  // Basic field validation
  const validFieldRegex = /^(\*|\d+([,-]\d+)*|\*\/\d+)$/;
  for (const p of parts) {
    if (!validFieldRegex.test(p)) {
      return { isValid: false, error: `Invalid syntax in field: "${p}"` };
    }
  }

  // Common pattern translations
  if (min === '0' && hour === '9' && dom === '*' && mon === '*' && dow === '*') {
    return { isValid: true, preview: 'Every day at 09:00 (Daily Morning)' };
  }
  if (min === '0' && hour === '18' && dom === '*' && mon === '*' && dow === '*') {
    return { isValid: true, preview: 'Every day at 18:00 (Daily Evening)' };
  }
  if (min.startsWith('*/')) {
    const step = min.slice(2);
    return { isValid: true, preview: `Every ${step} minutes` };
  }
  if (min === '0' && hour === '*' && dom === '*' && mon === '*' && dow === '*') {
    return { isValid: true, preview: 'Every hour at minute 0' };
  }
  if (min === '0' && hour === '9' && dom === '*' && mon === '*' && (dow === '1' || dow === 'mon')) {
    return { isValid: true, preview: 'Every Monday at 09:00' };
  }
  if (min === '0' && hour === '9' && dom === '*' && mon === '*' && dow === '1-5') {
    return { isValid: true, preview: 'Monday through Friday at 09:00 (Weekdays)' };
  }

  // Fallback description
  return {
    isValid: true,
    preview: `Runs at minute ${min}, hour ${hour}, day-of-month ${dom}, month ${mon}, weekday ${dow}`,
  };
}
