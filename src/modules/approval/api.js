export function registerApprovalRoutes(app, { approvalService }) {
  app.get('/approvals', async (request, response, next) => {
    try {
      response.json({ approvals: approvalService.listApprovals({ status: request.query.status }) });
    } catch (error) {
      next(error);
    }
  });

  app.get('/tasks/:taskId/approvals', async (request, response, next) => {
    try {
      response.json({ approvals: approvalService.listApprovalsForTask(request.params.taskId) });
    } catch (error) {
      next(error);
    }
  });

  app.post('/approvals/:approvalId/approve', async (request, response, next) => {
    try {
      const approval = approvalService.approve(request.params.approvalId, request.body ?? {});
      response.json({ approval });
    } catch (error) {
      next(error);
    }
  });

  app.post('/approvals/:approvalId/reject', async (request, response, next) => {
    try {
      const approval = approvalService.reject(request.params.approvalId, request.body ?? {});
      response.json({ approval });
    } catch (error) {
      next(error);
    }
  });
}
