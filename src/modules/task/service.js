import { AppError } from '../../shared/errors.js';
import { createId } from '../../shared/ids.js';
import { nowIso } from '../../shared/time.js';
import { deriveTaskStatus } from './status.js';

export function createTaskService({ taskRepository, taskRunRepository, auditService }) {
  function createTask({
    title,
    description = '',
    createdBy = 'api-user',
    requiresApproval = false,
    preferredAgentId = null,
    requiredCapabilities = [],
    parentTaskId = null,
    isSplittable = false,
  }) {
    const normalizedTitle = typeof title === 'string' ? title.trim() : '';

    if (!normalizedTitle) {
      throw new AppError(400, 'title is required');
    }

    if (parentTaskId) {
      const parentTask = taskRepository.getById(parentTaskId);
      if (!parentTask) {
        throw new AppError(404, 'parent task not found');
      }
    }

    if (Boolean(isSplittable) && Boolean(requiresApproval)) {
      throw new AppError(400, 'splittable parent task approval is not supported');
    }

    const timestamp = nowIso();
    const task = {
      id: createId(),
      title: normalizedTitle,
      description,
      status: 'ready',
      createdBy,
      requiresApproval: Boolean(requiresApproval),
      preferredAgentId: preferredAgentId || null,
      requiredCapabilities: normalizeCapabilities(requiredCapabilities),
      parentTaskId: parentTaskId || null,
      isSplittable: Boolean(isSplittable),
      latestRunId: null,
      createdAt: timestamp,
      updatedAt: timestamp,
    };

    taskRepository.create(task);
    auditService.append({
      entityType: 'task',
      entityId: task.id,
      eventType: 'task.created',
      actorType: 'user',
      actorId: createdBy,
      correlationId: task.id,
      payload: {
        taskId: task.id,
        title: task.title,
        status: task.status,
        requiresApproval: task.requiresApproval,
        preferredAgentId: task.preferredAgentId,
        requiredCapabilities: task.requiredCapabilities,
        parentTaskId: task.parentTaskId,
        isSplittable: task.isSplittable,
      },
    });

    if (task.parentTaskId) {
      syncTaskState(task.parentTaskId, 'system', 'task-service');
    }

    return task;
  }

  function createSubtask(parentTaskId, input) {
    const parentTask = getTask(parentTaskId);
    if (!parentTask.isSplittable) {
      throw new AppError(409, 'task is not splittable');
    }

    if (parentTask.requiresApproval) {
      throw new AppError(409, 'parent task approval is not supported for subtasks');
    }

    return createTask({
      ...input,
        parentTaskId,
        isSplittable: false,
    });
  }

  function listTasks() {
    return taskRepository.list();
  }

  function listSubtasks(parentTaskId) {
    getTask(parentTaskId);
    return taskRepository.listByParentTaskId(parentTaskId);
  }

  function getTask(taskId) {
    const task = taskRepository.getById(taskId);

    if (!task) {
      throw new AppError(404, 'task not found');
    }

    return task;
  }

  function syncTaskState(taskId, actorType = 'system', actorId = 'system') {
    const task = getTask(taskId);
    const childTasks = taskRepository.listByParentTaskId(taskId);
    const latestRun = task.latestRunId ? taskRunRepository.getById(task.latestRunId) : null;
    const nextStatus = deriveTaskStatus(latestRun, childTasks);

    if (task.status === nextStatus) {
      if (task.parentTaskId) {
        syncTaskState(task.parentTaskId, actorType, actorId);
      }
      return task;
    }

    const updatedTask = {
      ...task,
      status: nextStatus,
      updatedAt: nowIso(),
    };

    taskRepository.update(updatedTask);
    auditService.append({
      entityType: 'task',
      entityId: updatedTask.id,
      eventType: 'task.status_changed',
      actorType,
      actorId,
      correlationId: latestRun?.correlationId ?? updatedTask.id,
      payload: {
        taskId: updatedTask.id,
        latestRunId: updatedTask.latestRunId,
        status: updatedTask.status,
      },
    });

    if (updatedTask.parentTaskId) {
      syncTaskState(updatedTask.parentTaskId, actorType, actorId);
    }

    return updatedTask;
  }

  function assignLatestRun(taskId, runId, actorType = 'system', actorId = 'system') {
    const task = getTask(taskId);
    const updatedTask = {
      ...task,
      latestRunId: runId,
      updatedAt: nowIso(),
    };

    taskRepository.update(updatedTask);
    return syncTaskState(taskId, actorType, actorId);
  }

  return {
    createTask,
    createSubtask,
    listTasks,
    listSubtasks,
    getTask,
    syncTaskState,
    assignLatestRun,
    hasSubtasks(taskId) {
      getTask(taskId);
      return taskRepository.hasChildren(taskId);
    },
    listDescendantTaskIds(taskId) {
      getTask(taskId);
      const descendantTaskIds = [];
      const queue = taskRepository.listByParentTaskId(taskId).map((task) => task.id);

      while (queue.length > 0) {
        const currentTaskId = queue.shift();
        descendantTaskIds.push(currentTaskId);
        for (const childTask of taskRepository.listByParentTaskId(currentTaskId)) {
          queue.push(childTask.id);
        }
      }

      return descendantTaskIds;
    },
  };
}

function normalizeCapabilities(capabilities) {
  if (!Array.isArray(capabilities)) {
    return [];
  }

  return [...new Set(capabilities.map((value) => String(value).trim()).filter(Boolean))];
}
