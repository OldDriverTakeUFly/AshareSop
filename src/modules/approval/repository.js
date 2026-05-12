export function createApprovalRepository(state) {
  function getApprovalIds(taskId) {
    return state.approvalsByTaskId.get(taskId) ?? [];
  }

  function getApprovalsForTask(taskId) {
    return getApprovalIds(taskId)
      .map((approvalId) => state.approvals.get(approvalId))
      .filter(Boolean)
      .sort((left, right) => left.requestedAt.localeCompare(right.requestedAt));
  }

  return {
    create(approval) {
      state.approvals.set(approval.id, approval);
      const current = getApprovalIds(approval.taskId);
      state.approvalsByTaskId.set(approval.taskId, [...current, approval.id]);
      return approval;
    },
    update(approval) {
      state.approvals.set(approval.id, approval);
      return approval;
    },
    getById(approvalId) {
      return state.approvals.get(approvalId) ?? null;
    },
    list() {
      return Array.from(state.approvals.values()).sort((left, right) => left.requestedAt.localeCompare(right.requestedAt));
    },
    listByTaskId(taskId) {
      return getApprovalsForTask(taskId);
    },
    listByStatus(status) {
      return Array.from(state.approvals.values())
        .filter((approval) => (status ? approval.status === status : true))
        .sort((left, right) => left.requestedAt.localeCompare(right.requestedAt));
    },
    getPendingByRunId(taskRunId) {
      return Array.from(state.approvals.values()).find((approval) => {
        return approval.taskRunId === taskRunId && approval.status === 'pending';
      }) ?? null;
    },
  };
}
