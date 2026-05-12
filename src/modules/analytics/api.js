export function registerAnalyticsRoutes(app, { analyticsService }) {
  app.get('/dashboard/summary', async (_request, response, next) => {
    try {
      response.json({ summary: analyticsService.getDashboardSummary() });
    } catch (error) {
      next(error);
    }
  });
}
