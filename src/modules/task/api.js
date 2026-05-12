export function registerTaskRoutes(app, { taskService, taskRunService, auditService, orchestratorService }) {
  app.post('/tasks', async (request, response, next) => {
    try {
      const task = taskService.createTask(request.body ?? {});
      response.status(201).json({ task });
    } catch (error) {
      next(error);
    }
  });

  app.get('/tasks', async (_request, response, next) => {
    try {
      response.json({ tasks: taskService.listTasks() });
    } catch (error) {
      next(error);
    }
  });

  app.post('/tasks/:taskId/runs', async (request, response, next) => {
    try {
      const taskRun = orchestratorService.triggerTaskRun({
        taskId: request.params.taskId,
        triggeredBy: request.body?.triggeredBy,
        stubOutcome: request.header('x-stub-run-outcome') ?? 'succeed',
      });
      response.status(201).json({ taskRun });
    } catch (error) {
      next(error);
    }
  });

  app.post('/tasks/:taskId/subtasks', async (request, response, next) => {
    try {
      const task = taskService.createSubtask(request.params.taskId, request.body ?? {});
      response.status(201).json({ task });
    } catch (error) {
      next(error);
    }
  });

  app.get('/tasks/:taskId/subtasks', async (request, response, next) => {
    try {
      response.json({ tasks: taskService.listSubtasks(request.params.taskId) });
    } catch (error) {
      next(error);
    }
  });

  app.post('/tasks/:taskId/parallel-runs', async (request, response, next) => {
    try {
      const taskRuns = orchestratorService.triggerParallelTaskRuns({
        taskId: request.params.taskId,
        triggeredBy: request.body?.triggeredBy,
        stubOutcome: request.header('x-stub-run-outcome') ?? 'succeed',
      });
      response.status(201).json({ taskRuns });
    } catch (error) {
      next(error);
    }
  });

  app.get('/tasks/:taskId/runs', async (request, response, next) => {
    try {
      response.json({ taskRuns: taskRunService.listRunsForTask(request.params.taskId) });
    } catch (error) {
      next(error);
    }
  });

  app.get('/tasks/:taskId/audit-events', async (request, response, next) => {
    try {
      const descendantTaskIds = request.query.scope === 'tree'
        ? taskService.listDescendantTaskIds(request.params.taskId)
        : [];
      response.json({ auditEvents: auditService.listForTask(request.params.taskId, { descendantTaskIds }) });
    } catch (error) {
      next(error);
    }
  });

  app.get('/tasks/:taskId', async (request, response, next) => {
    try {
      response.json({ task: taskService.getTask(request.params.taskId) });
    } catch (error) {
      next(error);
    }
  });
}
