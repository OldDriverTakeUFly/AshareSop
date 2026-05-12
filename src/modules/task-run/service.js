import { AppError } from '../../shared/errors.js';
import { createId } from '../../shared/ids.js';
import { nowIso } from '../../shared/time.js';

export function createTaskRunService({
  taskRepository,
  taskRunRepository,
  taskService,
  auditService,
  runner,
  approvalService,
}) {
  function listRunsForTask(taskId) {
    const task = taskRepository.getById(taskId);
    if (!task) {
      throw new AppError(404, 'task not found');
    }

    return taskRunRepository.listByTaskId(taskId);
  }

  function ensureTriggerAllowed(taskId) {
    const task = taskRepository.getById(taskId);
    if (!task) {
      throw new AppError(404, 'task not found');
    }

    if (taskRepository.hasChildren(taskId)) {
      throw new AppError(409, 'parent task cannot run directly');
    }

    if (task.status === 'waiting_approval') {
      throw new AppError(409, 'task is waiting for approval');
    }

    const activeRun = taskRunRepository.getActiveByTaskId(taskId);
    if (activeRun) {
      throw new AppError(409, 'task already has an active run');
    }

    return task;
  }

  function triggerRun({ taskId, triggeredBy = 'api-user', agentId }) {
    ensureTriggerAllowed(taskId);

    const timestamp = nowIso();
    const correlationId = createId();
    const taskRun = {
      id: createId(),
      taskId,
      attempt: taskRunRepository.countByTaskId(taskId) + 1,
      status: 'queued',
      triggeredBy,
      agentId,
      queuedAt: timestamp,
      startedAt: null,
      finishedAt: null,
      outputSummary: null,
      failureReason: null,
      correlationId,
    };

    taskRunRepository.create(taskRun);
    auditService.append({
      entityType: 'task_run',
      entityId: taskRun.id,
      eventType: 'task_run.queued',
      actorType: 'user',
      actorId: triggeredBy,
      correlationId,
      payload: {
        taskId,
        taskRunId: taskRun.id,
        attempt: taskRun.attempt,
        status: taskRun.status,
        agentId: taskRun.agentId,
      },
    });

    return taskRun;
  }

  function dispatchQueuedRun({ taskId, taskRunId, stubOutcome = 'succeed' }) {
    const normalizedOutcome = stubOutcome === 'fail' ? 'fail' : 'succeed';
    taskService.assignLatestRun(taskId, taskRunId, 'system', 'orchestrator');
    runner.enqueue(taskRunId, normalizedOutcome);
  }

  function markDispatching(taskRunId) {
    const taskRun = requireRun(taskRunId);
    if (taskRun.status !== 'queued') {
      return taskRun;
    }

    return saveRun({
      ...taskRun,
      status: 'dispatching',
    });
  }

  function startRun(taskRunId) {
    const taskRun = requireRun(taskRunId);
    if (!['queued', 'dispatching'].includes(taskRun.status)) {
      return taskRun;
    }

    const startedRun = saveRun({
      ...taskRun,
      status: 'running',
      startedAt: taskRun.startedAt ?? nowIso(),
    });

    auditService.append({
      entityType: 'task_run',
      entityId: startedRun.id,
      eventType: 'task_run.started',
      actorType: 'system',
      actorId: startedRun.agentId,
      correlationId: startedRun.correlationId,
      payload: {
        taskId: startedRun.taskId,
        taskRunId: startedRun.id,
        status: startedRun.status,
        agentId: startedRun.agentId,
      },
    });

    taskService.syncTaskState(startedRun.taskId, 'system', startedRun.agentId);
    return startedRun;
  }

  function completeRun(taskRunId, outputSummary) {
    const taskRun = requireRun(taskRunId);
    if (['succeeded', 'waiting_approval', 'failed', 'cancelled'].includes(taskRun.status)) {
      return taskRun;
    }

    const task = taskRepository.getById(taskRun.taskId);
    if (!task) {
      throw new AppError(404, 'task not found');
    }

    if (task.requiresApproval) {
      const approvalRun = saveRun({
        ...taskRun,
        status: 'waiting_approval',
        finishedAt: nowIso(),
        outputSummary,
        failureReason: null,
      });

      approvalService.createFinalResultApproval({
        taskId: approvalRun.taskId,
        taskRunId: approvalRun.id,
        requestedBy: approvalRun.agentId,
      });

      return approvalRun;
    }

    const completedRun = saveRun({
      ...taskRun,
      status: 'succeeded',
      finishedAt: nowIso(),
      outputSummary,
      failureReason: null,
    });

    auditService.append({
      entityType: 'task_run',
      entityId: completedRun.id,
      eventType: 'task_run.succeeded',
      actorType: 'system',
      actorId: completedRun.agentId,
      correlationId: completedRun.correlationId,
      payload: {
        taskId: completedRun.taskId,
        taskRunId: completedRun.id,
        status: completedRun.status,
        outputSummary: completedRun.outputSummary,
      },
    });

    taskService.syncTaskState(completedRun.taskId, 'system', completedRun.agentId);
    return completedRun;
  }

  function failRun(taskRunId, failureReason) {
    const taskRun = requireRun(taskRunId);
    if (['failed', 'succeeded', 'waiting_approval', 'cancelled'].includes(taskRun.status)) {
      return taskRun;
    }

    const failedRun = saveRun({
      ...taskRun,
      status: 'failed',
      finishedAt: nowIso(),
      outputSummary: null,
      failureReason,
    });

    auditService.append({
      entityType: 'task_run',
      entityId: failedRun.id,
      eventType: 'task_run.failed',
      actorType: 'system',
      actorId: failedRun.agentId,
      correlationId: failedRun.correlationId,
      payload: {
        taskId: failedRun.taskId,
        taskRunId: failedRun.id,
        status: failedRun.status,
        failureReason: failedRun.failureReason,
      },
    });

    taskService.syncTaskState(failedRun.taskId, 'system', failedRun.agentId);
    return failedRun;
  }

  function requireRun(taskRunId) {
    const taskRun = taskRunRepository.getById(taskRunId);
    if (!taskRun) {
      throw new AppError(404, 'task run not found');
    }
    return taskRun;
  }

  function saveRun(taskRun) {
    return taskRunRepository.update(taskRun);
  }

  return {
    ensureTriggerAllowed,
    triggerRun,
    dispatchQueuedRun,
    listRunsForTask,
    markDispatching,
    startRun,
    completeRun,
    failRun,
  };
}
