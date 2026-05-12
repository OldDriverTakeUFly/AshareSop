import { AppError } from '../../shared/errors.js';
import { createId } from '../../shared/ids.js';
import { nowIso } from '../../shared/time.js';

export function createApprovalService({
  approvalRepository,
  taskRepository,
  taskRunRepository,
  taskService,
  auditService,
}) {
  function createFinalResultApproval({ taskId, taskRunId, requestedBy = 'system' }) {
    requireTask(taskId);
    const taskRun = requireTaskRun(taskRunId);

    const existing = approvalRepository.getPendingByRunId(taskRunId);
    if (existing) {
      return existing;
    }

    const approval = {
      id: createId(),
      taskId,
      taskRunId,
      type: 'final_result_acceptance',
      status: 'pending',
      requestedBy,
      requestedAt: nowIso(),
      decidedBy: null,
      decidedAt: null,
      decision: null,
      comment: null,
    };

    approvalRepository.create(approval);

    const waitingRun = {
      ...taskRun,
      status: 'waiting_approval',
      finishedAt: taskRun.finishedAt ?? nowIso(),
    };
    taskRunRepository.update(waitingRun);

    auditService.append({
      entityType: 'task_run',
      entityId: waitingRun.id,
      eventType: 'task_run.waiting_approval',
      actorType: 'system',
      actorId: requestedBy,
      correlationId: waitingRun.correlationId,
      payload: {
        taskId,
        taskRunId: waitingRun.id,
        status: waitingRun.status,
      },
    });

    auditService.append({
      entityType: 'approval',
      entityId: approval.id,
      eventType: 'approval.requested',
      actorType: 'system',
      actorId: requestedBy,
      correlationId: waitingRun.correlationId,
      payload: {
        taskId,
        taskRunId: waitingRun.id,
        approvalId: approval.id,
        status: approval.status,
        type: approval.type,
      },
    });

    taskService.syncTaskState(taskId, 'system', requestedBy);
    return approval;
  }

  function listApprovalsForTask(taskId) {
    requireTask(taskId);
    return approvalRepository.listByTaskId(taskId);
  }

  function listApprovals({ status } = {}) {
    return approvalRepository.listByStatus(status);
  }

  function getApproval(approvalId) {
    const approval = approvalRepository.getById(approvalId);
    if (!approval) {
      throw new AppError(404, 'approval not found');
    }
    return approval;
  }

  function approve(approvalId, { decidedBy = 'approver', comment = null } = {}) {
    const approval = getApproval(approvalId);
    if (approval.status !== 'pending') {
      throw new AppError(409, 'approval is not pending');
    }

    const taskRun = requireTaskRun(approval.taskRunId);
    const updatedApproval = approvalRepository.update({
      ...approval,
      status: 'approved',
      decision: 'approved',
      decidedBy,
      decidedAt: nowIso(),
      comment,
    });

    taskRunRepository.update({
      ...taskRun,
      status: 'succeeded',
      finishedAt: taskRun.finishedAt ?? nowIso(),
    });

    auditService.append({
      entityType: 'task_run',
      entityId: taskRun.id,
      eventType: 'task_run.succeeded',
      actorType: 'user',
      actorId: decidedBy,
      correlationId: taskRun.correlationId,
      payload: {
        taskId: updatedApproval.taskId,
        taskRunId: taskRun.id,
        status: 'succeeded',
        approvalId: updatedApproval.id,
        reason: 'approval_approved',
      },
    });

    auditService.append({
      entityType: 'approval',
      entityId: updatedApproval.id,
      eventType: 'approval.approved',
      actorType: 'user',
      actorId: decidedBy,
      correlationId: taskRun.correlationId,
      payload: {
        taskId: updatedApproval.taskId,
        taskRunId: updatedApproval.taskRunId,
        approvalId: updatedApproval.id,
        status: updatedApproval.status,
        comment,
      },
    });

    taskService.syncTaskState(updatedApproval.taskId, 'user', decidedBy);
    return updatedApproval;
  }

  function reject(approvalId, { decidedBy = 'approver', comment = null } = {}) {
    const approval = getApproval(approvalId);
    if (approval.status !== 'pending') {
      throw new AppError(409, 'approval is not pending');
    }

    const taskRun = requireTaskRun(approval.taskRunId);
    const updatedApproval = approvalRepository.update({
      ...approval,
      status: 'rejected',
      decision: 'rejected',
      decidedBy,
      decidedAt: nowIso(),
      comment,
    });

    taskRunRepository.update({
      ...taskRun,
      status: 'cancelled',
      finishedAt: taskRun.finishedAt ?? nowIso(),
    });

    auditService.append({
      entityType: 'task_run',
      entityId: taskRun.id,
      eventType: 'task_run.cancelled',
      actorType: 'user',
      actorId: decidedBy,
      correlationId: taskRun.correlationId,
      payload: {
        taskId: updatedApproval.taskId,
        taskRunId: taskRun.id,
        status: 'cancelled',
        reason: 'approval_rejected',
      },
    });

    auditService.append({
      entityType: 'approval',
      entityId: updatedApproval.id,
      eventType: 'approval.rejected',
      actorType: 'user',
      actorId: decidedBy,
      correlationId: taskRun.correlationId,
      payload: {
        taskId: updatedApproval.taskId,
        taskRunId: updatedApproval.taskRunId,
        approvalId: updatedApproval.id,
        status: updatedApproval.status,
        comment,
      },
    });

    taskService.syncTaskState(updatedApproval.taskId, 'user', decidedBy);
    return updatedApproval;
  }

  function requireTask(taskId) {
    const task = taskRepository.getById(taskId);
    if (!task) {
      throw new AppError(404, 'task not found');
    }
    return task;
  }

  function requireTaskRun(taskRunId) {
    const taskRun = taskRunRepository.getById(taskRunId);
    if (!taskRun) {
      throw new AppError(404, 'task run not found');
    }
    return taskRun;
  }

  return {
    createFinalResultApproval,
    listApprovalsForTask,
    listApprovals,
    getApproval,
    approve,
    reject,
  };
}
