export function createTaskRunRepository(state) {
  const ACTIVE_RUN_STATUSES = new Set(['queued', 'dispatching', 'running']);

  function getRunsForTask(taskId) {
    const runIds = state.taskRunsByTaskId.get(taskId) ?? [];
    return runIds
      .map((runId) => state.taskRuns.get(runId))
      .filter(Boolean)
      .sort((left, right) => left.attempt - right.attempt);
  }

  return {
    create(taskRun) {
      state.taskRuns.set(taskRun.id, taskRun);
      const current = state.taskRunsByTaskId.get(taskRun.taskId) ?? [];
      state.taskRunsByTaskId.set(taskRun.taskId, [...current, taskRun.id]);
      return taskRun;
    },
    update(taskRun) {
      state.taskRuns.set(taskRun.id, taskRun);
      return taskRun;
    },
    getById(taskRunId) {
      return state.taskRuns.get(taskRunId) ?? null;
    },
    list() {
      return Array.from(state.taskRuns.values()).sort((left, right) => {
        return left.queuedAt.localeCompare(right.queuedAt);
      });
    },
    listByTaskId(taskId) {
      return getRunsForTask(taskId);
    },
    countByTaskId(taskId) {
      return getRunsForTask(taskId).length;
    },
    getActiveByTaskId(taskId) {
      return getRunsForTask(taskId).find((taskRun) => ACTIVE_RUN_STATUSES.has(taskRun.status)) ?? null;
    },
    countActiveByAgentId(agentId) {
      return Array.from(state.taskRuns.values()).filter((taskRun) => {
        return taskRun.agentId === agentId && ACTIVE_RUN_STATUSES.has(taskRun.status);
      }).length;
    },
  };
}
