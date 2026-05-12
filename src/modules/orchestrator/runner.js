export function createRunner({ getTaskRunService }) {
  return {
    enqueue(taskRunId, mode) {
      setTimeout(() => {
        const taskRunService = getTaskRunService();
        taskRunService.markDispatching(taskRunId);
      }, 0);

      setTimeout(() => {
        const taskRunService = getTaskRunService();
        taskRunService.startRun(taskRunId);
      }, 5);

      setTimeout(() => {
        const taskRunService = getTaskRunService();
        if (mode === 'fail') {
          taskRunService.failRun(taskRunId, 'Stub agent simulated a failure.');
          return;
        }

        taskRunService.completeRun(taskRunId, 'Stub agent completed the task successfully.');
      }, 10);
    },
  };
}
