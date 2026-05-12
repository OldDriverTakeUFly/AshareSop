import { AppError } from '../../shared/errors.js';
import { createId } from '../../shared/ids.js';

export function createOrchestratorService({ taskService, agentService, taskRunService, auditService }) {
  function triggerTaskRun({ taskId, triggeredBy = 'api-user', stubOutcome = 'succeed' }) {
    const task = taskService.getTask(taskId);
    const correlationId = createId();
    taskRunService.ensureTriggerAllowed(taskId);
    const selection = agentService.selectAgentForTask(task);
    const selectedAgent = selection?.agent ?? null;

    if (!selectedAgent) {
      auditService.append({
        entityType: 'task',
        entityId: taskId,
        eventType: 'task_run.assignment_rejected',
        actorType: 'system',
        actorId: 'orchestrator',
        correlationId,
        payload: {
          taskId,
          preferredAgentId: task.preferredAgentId ?? null,
          requiredCapabilities: task.requiredCapabilities ?? [],
          reason: 'no_eligible_agent',
        },
      });

      throw new AppError(409, 'no eligible agent available');
    }

    const taskRun = taskRunService.triggerRun({
      taskId,
      triggeredBy,
      stubOutcome,
      agentId: selectedAgent.id,
    });

    auditService.append({
      entityType: 'task_run',
      entityId: taskRun.id,
      eventType: 'task_run.assigned',
      actorType: 'system',
      actorId: 'orchestrator',
      correlationId: taskRun.correlationId,
      payload: {
        taskId,
        taskRunId: taskRun.id,
        agentId: selectedAgent.id,
        preferredAgentId: task.preferredAgentId ?? null,
        requiredCapabilities: task.requiredCapabilities ?? [],
        selectionReason: selection.selectionReason,
      },
    });

    taskRunService.dispatchQueuedRun({
      taskId,
      taskRunId: taskRun.id,
      stubOutcome,
    });

    return taskRun;
  }

  function triggerParallelTaskRuns({ taskId, triggeredBy = 'api-user', stubOutcome = 'succeed' }) {
    const parentTask = taskService.getTask(taskId);
    if (!parentTask.isSplittable) {
      throw new AppError(409, 'task is not splittable');
    }

    const childTasks = taskService.listSubtasks(taskId);
    if (childTasks.length === 0) {
      throw new AppError(409, 'task has no subtasks');
    }

    const readyChildTasks = childTasks.filter((childTask) => childTask.status === 'ready');
    if (readyChildTasks.length === 0) {
      throw new AppError(409, 'no ready subtasks available');
    }

    const reservedLoads = new Map();
    const selections = [];

    for (const childTask of readyChildTasks) {
      taskRunService.ensureTriggerAllowed(childTask.id);
      const selection = agentService.selectAgentForTask(childTask, { reservedLoads });
      if (!selection?.agent) {
        auditService.append({
          entityType: 'task',
          entityId: taskId,
          eventType: 'task.parallel_dispatch_rejected',
          actorType: 'system',
          actorId: 'orchestrator',
          correlationId: taskId,
          payload: {
            taskId,
            blockedChildTaskId: childTask.id,
            reason: 'no_eligible_agent',
          },
        });

        throw new AppError(409, 'no eligible agent available for parallel dispatch');
      }

      reservedLoads.set(selection.agent.id, (reservedLoads.get(selection.agent.id) ?? 0) + 1);
      selections.push({ childTask, selection });
    }

    auditService.append({
      entityType: 'task',
      entityId: taskId,
      eventType: 'task.parallel_dispatch_requested',
      actorType: 'user',
      actorId: triggeredBy,
      correlationId: taskId,
      payload: {
        taskId,
        subtaskIds: readyChildTasks.map((childTask) => childTask.id),
      },
    });

    const taskRuns = selections.map(({ childTask, selection }) => {
      const taskRun = taskRunService.triggerRun({
        taskId: childTask.id,
        triggeredBy,
        stubOutcome,
        agentId: selection.agent.id,
      });

      auditService.append({
        entityType: 'task_run',
        entityId: taskRun.id,
        eventType: 'task_run.assigned',
        actorType: 'system',
        actorId: 'orchestrator',
        correlationId: taskRun.correlationId,
        payload: {
          taskId: childTask.id,
          taskRunId: taskRun.id,
          agentId: selection.agent.id,
          preferredAgentId: childTask.preferredAgentId ?? null,
          requiredCapabilities: childTask.requiredCapabilities ?? [],
          selectionReason: selection.selectionReason,
          parentTaskId: taskId,
        },
      });

      taskRunService.dispatchQueuedRun({
        taskId: childTask.id,
        taskRunId: taskRun.id,
        stubOutcome,
      });

      return taskRun;
    });

    taskService.syncTaskState(taskId, 'system', 'orchestrator');
    return taskRuns;
  }

  return {
    triggerTaskRun,
    triggerParallelTaskRuns,
  };
}
