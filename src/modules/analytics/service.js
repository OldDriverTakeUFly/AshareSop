import { nowIso } from '../../shared/time.js';

function createStatusBuckets(statuses) {
  return Object.fromEntries(statuses.map((status) => [status, 0]));
}

function average(values) {
  if (values.length === 0) {
    return 0;
  }

  return Math.round(values.reduce((sum, value) => sum + value, 0) / values.length);
}

function durationMs(start, end) {
  if (!start || !end) {
    return null;
  }

  const diff = Date.parse(end) - Date.parse(start);
  return Number.isFinite(diff) && diff >= 0 ? diff : null;
}

export function createAnalyticsService({
  taskRepository,
  taskRunRepository,
  approvalRepository,
  agentRepository,
  auditRepository,
}) {
  function getDashboardSummary() {
    const tasks = taskRepository.list();
    const rootTasks = tasks.filter((task) => !task.parentTaskId);
    const subtasks = tasks.filter((task) => Boolean(task.parentTaskId));
    const taskRuns = taskRunRepository.list();
    const approvals = approvalRepository.list();
    const agents = agentRepository.list();
    const auditEvents = auditRepository.list();

    const rootTaskStatusCounts = summarizeTaskStatuses(rootTasks);
    const subtaskStatusCounts = summarizeTaskStatuses(subtasks);
    const taskRunSummary = summarizeTaskRuns(taskRuns);
    const approvalSummary = summarizeApprovals(approvals);
    const agentSummary = summarizeAgents(agents, taskRuns);

    return {
      generatedAt: nowIso(),
      tasks: {
        root: rootTaskStatusCounts,
        subtasks: subtaskStatusCounts,
      },
      taskRuns: taskRunSummary,
      approvals: approvalSummary,
      agents: agentSummary.summary,
      agentPerformance: agentSummary.performance,
      pendingApprovals: approvals
        .filter((approval) => approval.status === 'pending')
        .slice(0, 10),
      activeRootTasks: rootTasks
        .filter((task) => ['running', 'waiting_approval'].includes(task.status))
        .slice(0, 10),
      recentFailedRuns: taskRuns
        .filter((taskRun) => taskRun.status === 'failed')
        .sort((left, right) => (right.finishedAt ?? '').localeCompare(left.finishedAt ?? ''))
        .slice(0, 10),
      recentActivity: auditEvents.slice(-10).reverse(),
    };
  }

  return {
    getDashboardSummary,
  };
}

function summarizeTaskStatuses(tasks) {
  const buckets = createStatusBuckets(['ready', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled']);
  for (const task of tasks) {
    if (buckets[task.status] !== undefined) {
      buckets[task.status] += 1;
    }
  }

  return {
    total: tasks.length,
    ready: buckets.ready,
    running: buckets.running,
    waitingApproval: buckets.waiting_approval,
    completed: buckets.completed,
    failed: buckets.failed,
    cancelled: buckets.cancelled,
    completionRate: tasks.length === 0 ? 0 : Number((buckets.completed / tasks.length).toFixed(4)),
  };
}

function summarizeTaskRuns(taskRuns) {
  const buckets = createStatusBuckets(['queued', 'dispatching', 'running', 'waiting_approval', 'succeeded', 'failed', 'cancelled']);
  const executionDurations = [];

  for (const taskRun of taskRuns) {
    if (buckets[taskRun.status] !== undefined) {
      buckets[taskRun.status] += 1;
    }

    const duration = durationMs(taskRun.startedAt, taskRun.finishedAt);
    if (duration !== null) {
      executionDurations.push(duration);
    }
  }

  const terminalTotal = buckets.succeeded + buckets.failed;

  return {
    total: taskRuns.length,
    queued: buckets.queued,
    dispatching: buckets.dispatching,
    running: buckets.running,
    waitingApproval: buckets.waiting_approval,
    succeeded: buckets.succeeded,
    failed: buckets.failed,
    cancelled: buckets.cancelled,
    successRate: terminalTotal === 0 ? 0 : Number((buckets.succeeded / terminalTotal).toFixed(4)),
    averageExecutionMs: average(executionDurations),
  };
}

function summarizeApprovals(approvals) {
  const buckets = createStatusBuckets(['pending', 'approved', 'rejected', 'cancelled']);
  const waits = [];

  for (const approval of approvals) {
    if (buckets[approval.status] !== undefined) {
      buckets[approval.status] += 1;
    }

    const wait = durationMs(approval.requestedAt, approval.decidedAt);
    if (wait !== null) {
      waits.push(wait);
    }
  }

  return {
    total: approvals.length,
    pending: buckets.pending,
    approved: buckets.approved,
    rejected: buckets.rejected,
    cancelled: buckets.cancelled,
    averageWaitMs: average(waits),
  };
}

function summarizeAgents(agents, taskRuns) {
  const statusBuckets = createStatusBuckets(['active', 'paused', 'offline', 'deprecated']);
  const runsByAgentId = new Map();

  for (const taskRun of taskRuns) {
    const current = runsByAgentId.get(taskRun.agentId) ?? [];
    runsByAgentId.set(taskRun.agentId, [...current, taskRun]);
  }

  const performance = agents.map((agent) => {
    statusBuckets[agent.status] += 1;
    const runs = runsByAgentId.get(agent.id) ?? [];
    const succeededRuns = runs.filter((taskRun) => taskRun.status === 'succeeded').length;
    const failedRuns = runs.filter((taskRun) => taskRun.status === 'failed').length;
    const terminalRuns = runs.filter((taskRun) => ['succeeded', 'failed', 'cancelled'].includes(taskRun.status));
    const executionDurations = runs
      .map((taskRun) => durationMs(taskRun.startedAt, taskRun.finishedAt))
      .filter((value) => value !== null);
    const activeRunCount = runs.filter((taskRun) => ['queued', 'dispatching', 'running'].includes(taskRun.status)).length;

    return {
      agentId: agent.id,
      name: agent.name,
      activeRunCount,
      totalRuns: runs.length,
      succeededRuns,
      failedRuns,
      successRate: terminalRuns.length === 0 ? 0 : Number((succeededRuns / terminalRuns.length).toFixed(4)),
      averageExecutionMs: average(executionDurations),
    };
  });

  return {
    summary: {
      total: agents.length,
      ...statusBuckets,
      busy: performance.filter((agent) => agent.activeRunCount > 0).length,
      idle: performance.filter((agent) => agent.activeRunCount === 0).length,
    },
    performance,
  };
}
