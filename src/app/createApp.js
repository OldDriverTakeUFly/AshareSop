import path from 'node:path';
import { fileURLToPath } from 'node:url';
import express from 'express';

import { createState } from './state.js';
import { isAppError } from '../shared/errors.js';
import { createTaskRepository } from '../modules/task/repository.js';
import { createTaskRunRepository } from '../modules/task-run/repository.js';
import { createAuditRepository } from '../modules/audit/repository.js';
import { createAuditService } from '../modules/audit/service.js';
import { createAnalyticsService } from '../modules/analytics/service.js';
import { createAgentRepository } from '../modules/agent/repository.js';
import { createAgentService } from '../modules/agent/service.js';
import { createTaskService } from '../modules/task/service.js';
import { createTaskRunService } from '../modules/task-run/service.js';
import { createApprovalRepository } from '../modules/approval/repository.js';
import { createApprovalService } from '../modules/approval/service.js';
import { createOrchestratorService } from '../modules/orchestrator/service.js';
import { createRunner } from '../modules/orchestrator/runner.js';
import { registerAgentRoutes } from '../modules/agent/api.js';
import { registerAnalyticsRoutes } from '../modules/analytics/api.js';
import { registerTaskRoutes } from '../modules/task/api.js';
import { registerApprovalRoutes } from '../modules/approval/api.js';

export function createApp() {
  const app = express();
  const state = createState();
  const publicDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../public');

  const taskRepository = createTaskRepository(state);
  const taskRunRepository = createTaskRunRepository(state);
  const approvalRepository = createApprovalRepository(state);
  const agentRepository = createAgentRepository(state);
  const auditRepository = createAuditRepository(state);

  const auditService = createAuditService({ auditRepository });
  const analyticsService = createAnalyticsService({
    taskRepository,
    taskRunRepository,
    approvalRepository,
    agentRepository,
    auditRepository,
  });
  const agentService = createAgentService({ agentRepository, taskRunRepository, auditService });
  const taskService = createTaskService({ taskRepository, taskRunRepository, auditService });
  const approvalService = createApprovalService({
    approvalRepository,
    taskRepository,
    taskRunRepository,
    taskService,
    auditService,
  });

  let taskRunService;
  const runner = createRunner({ getTaskRunService: () => taskRunService });

  taskRunService = createTaskRunService({
    taskRepository,
    taskRunRepository,
    taskService,
    auditService,
    runner,
    approvalService,
  });

  const orchestratorService = createOrchestratorService({
    taskService,
    agentService,
    taskRunService,
    auditService,
  });

  app.use(express.json());
  app.use(express.static(publicDir));

  app.get('/health', (_request, response) => {
    response.json({ ok: true });
  });

  registerAgentRoutes(app, { agentService });
  registerAnalyticsRoutes(app, { analyticsService });
  registerTaskRoutes(app, { taskService, taskRunService, auditService, orchestratorService });
  registerApprovalRoutes(app, { approvalService });

  app.use((error, _request, response, _next) => {
    if (isAppError(error)) {
      response.status(error.statusCode).json({ error: error.message });
      return;
    }

    response.status(500).json({ error: 'internal server error' });
  });

  return app;
}
