const ACTIVE_RUN_STATUSES = new Set(['queued', 'dispatching', 'running']);

export function isActiveRunStatus(status) {
  return ACTIVE_RUN_STATUSES.has(status);
}

export function deriveTaskStatus(latestRun, childTasks = []) {
  if (childTasks.length > 0) {
    return deriveParentTaskStatus(childTasks);
  }

  return deriveLeafTaskStatus(latestRun);
}

export function deriveLeafTaskStatus(latestRun) {
  if (!latestRun) {
    return 'ready';
  }

  switch (latestRun.status) {
    case 'queued':
    case 'dispatching':
    case 'running':
      return 'running';
    case 'waiting_approval':
      return 'waiting_approval';
    case 'succeeded':
      return 'completed';
    case 'failed':
      return 'failed';
    case 'cancelled':
      return 'ready';
    default:
      throw new Error(`Unsupported task run status: ${latestRun.status}`);
  }
}

export function deriveParentTaskStatus(childTasks) {
  if (childTasks.length === 0) {
    return 'ready';
  }

  const childStatuses = childTasks.map((childTask) => childTask.status);

  if (childStatuses.includes('running')) {
    return 'running';
  }

  if (childStatuses.includes('waiting_approval')) {
    return 'waiting_approval';
  }

  if (childStatuses.every((status) => status === 'completed')) {
    return 'completed';
  }

  if (childStatuses.includes('failed')) {
    return 'failed';
  }

  return 'ready';
}
