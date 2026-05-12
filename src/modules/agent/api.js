export function registerAgentRoutes(app, { agentService }) {
  app.post('/agents', async (request, response, next) => {
    try {
      const agent = agentService.createAgent(request.body ?? {});
      response.status(201).json({ agent });
    } catch (error) {
      next(error);
    }
  });

  app.get('/agents', async (_request, response, next) => {
    try {
      response.json({ agents: agentService.listAgents() });
    } catch (error) {
      next(error);
    }
  });

  app.get('/agents/:agentId', async (request, response, next) => {
    try {
      response.json({ agent: agentService.getAgent(request.params.agentId) });
    } catch (error) {
      next(error);
    }
  });

  app.patch('/agents/:agentId', async (request, response, next) => {
    try {
      response.json({ agent: agentService.updateAgent(request.params.agentId, request.body ?? {}) });
    } catch (error) {
      next(error);
    }
  });
}
