import { describe, expect, it } from 'vitest';

import { deriveParentTaskStatus, deriveTaskStatus } from '../../src/modules/task/status.js';

describe('deriveTaskStatus', () => {
  it('returns ready when no runs exist', () => {
    expect(deriveTaskStatus(null)).toBe('ready');
  });

  it('returns running for queued, dispatching, and running states', () => {
    expect(deriveTaskStatus({ status: 'queued' })).toBe('running');
    expect(deriveTaskStatus({ status: 'dispatching' })).toBe('running');
    expect(deriveTaskStatus({ status: 'running' })).toBe('running');
  });

  it('returns completed for succeeded runs', () => {
    expect(deriveTaskStatus({ status: 'succeeded' })).toBe('completed');
  });

  it('returns waiting_approval for approval-pending runs', () => {
    expect(deriveTaskStatus({ status: 'waiting_approval' })).toBe('waiting_approval');
  });

  it('returns failed for failed runs', () => {
    expect(deriveTaskStatus({ status: 'failed' })).toBe('failed');
  });

  it('returns ready for cancelled runs', () => {
    expect(deriveTaskStatus({ status: 'cancelled' })).toBe('ready');
  });

  it('throws for unsupported statuses', () => {
    expect(() => deriveTaskStatus({ status: 'mystery' })).toThrow('Unsupported task run status');
  });

  it('derives parent status as running when any child is running', () => {
    expect(deriveParentTaskStatus([{ status: 'running' }, { status: 'completed' }])).toBe('running');
  });

  it('derives parent status as waiting_approval when no child is running and one waits approval', () => {
    expect(deriveParentTaskStatus([{ status: 'waiting_approval' }, { status: 'completed' }])).toBe('waiting_approval');
  });

  it('derives parent status as completed when all children are completed', () => {
    expect(deriveParentTaskStatus([{ status: 'completed' }, { status: 'completed' }])).toBe('completed');
  });

  it('derives parent status as failed when no child is active and one failed', () => {
    expect(deriveParentTaskStatus([{ status: 'failed' }, { status: 'ready' }])).toBe('failed');
  });
});
