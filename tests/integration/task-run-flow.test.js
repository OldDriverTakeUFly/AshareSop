import request from 'supertest';
import { beforeEach, describe, expect, it } from 'vitest';

import { createApp } from '../../src/app/createApp.js';

async function createTask(api, overrides = {}) {
  const response = await api.post('/tasks').send({
    title: 'Implement the first MVP slice',
    description: 'Create a task, run it, and capture audit events.',
    ...overrides,
  });

  return response.body.task;
}

async function createAgent(api, overrides = {}) {
  const response = await api.post('/agents').send({
    name: 'Stub Agent',
    capabilities: ['general'],
    maxConcurrency: 1,
    ...overrides,
  });

  return response.body.agent;
}

async function createSubtask(api, parentTaskId, overrides = {}) {
  const response = await api.post(`/tasks/${parentTaskId}/subtasks`).send({
    title: 'Subtask',
    description: 'Child task for parallel execution.',
    ...overrides,
  });

  return response.body.task;
}

async function waitForTaskStatus(api, taskId, expectedStatus) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const response = await api.get(`/tasks/${taskId}`);
    if (response.body.task.status === expectedStatus) {
      return response.body.task;
    }

    await new Promise((resolve) => setTimeout(resolve, 10));
  }

  throw new Error(`Task ${taskId} did not reach ${expectedStatus}`);
}

describe('task and task run flow', () => {
  let api;

  beforeEach(() => {
    api = request(createApp());
  });

  it('serves the dashboard console shell from root', async () => {
    const response = await api.get('/');

    expect(response.status).toBe(200);
    expect(response.headers['content-type']).toContain('text/html');
    expect(response.text).toContain('CodeAgent Dashboard');
    expect(response.text).toContain('Root tasks');
  });

  it('creates a task in ready state', async () => {
    const response = await api.post('/tasks').send({ title: 'Create task' });

    expect(response.status).toBe(201);
    expect(response.body.task.status).toBe('ready');
    expect(response.body.task.latestRunId).toBeNull();
    expect(response.body.task.requiresApproval).toBe(false);
  });

  it('returns zero-filled dashboard summary on an empty app', async () => {
    const response = await api.get('/dashboard/summary');

    expect(response.status).toBe(200);
    expect(response.body.summary.tasks.root.total).toBe(0);
    expect(response.body.summary.tasks.root.completionRate).toBe(0);
    expect(response.body.summary.taskRuns.total).toBe(0);
    expect(response.body.summary.approvals.total).toBe(0);
    expect(response.body.summary.agents.total).toBe(0);
    expect(response.body.summary.agentPerformance).toEqual([]);
  });

  it('rejects invalid parent task references and unsupported split-parent approval', async () => {
    const missingParentResponse = await api.post('/tasks').send({
      title: 'Invalid child',
      parentTaskId: 'does-not-exist',
    });
    expect(missingParentResponse.status).toBe(404);
    expect(missingParentResponse.body.error).toBe('parent task not found');

    const invalidSplitParent = await api.post('/tasks').send({
      title: 'Invalid parent',
      isSplittable: true,
      requiresApproval: true,
    });
    expect(invalidSplitParent.status).toBe(400);
    expect(invalidSplitParent.body.error).toBe('splittable parent task approval is not supported');
  });

  it('creates and lists subtasks under a splittable parent', async () => {
    const parentTask = await createTask(api, { title: 'Parent task', isSplittable: true });

    const childTask = await createSubtask(api, parentTask.id, { title: 'Child A' });
    expect(childTask.parentTaskId).toBe(parentTask.id);

    const listResponse = await api.get(`/tasks/${parentTask.id}/subtasks`);
    expect(listResponse.status).toBe(200);
    expect(listResponse.body.tasks).toHaveLength(1);
    expect(listResponse.body.tasks[0].title).toBe('Child A');
  });

  it('prevents direct runs on parent container tasks', async () => {
    await createAgent(api, { capabilities: ['general'] });
    const parentTask = await createTask(api, { title: 'Parent task', isSplittable: true });
    await createSubtask(api, parentTask.id, { title: 'Child A' });

    const response = await api.post(`/tasks/${parentTask.id}/runs`).send({});
    expect(response.status).toBe(409);
    expect(response.body.error).toBe('parent task cannot run directly');
  });

  it('registers and updates agents', async () => {
    const agent = await createAgent(api, { name: 'Planner Agent', capabilities: ['planning'], maxConcurrency: 2 });

    const listResponse = await api.get('/agents');
    expect(listResponse.status).toBe(200);
    expect(listResponse.body.agents).toHaveLength(1);
    expect(listResponse.body.agents[0].name).toBe('Planner Agent');

    const detailResponse = await api.get(`/agents/${agent.id}`);
    expect(detailResponse.status).toBe(200);
    expect(detailResponse.body.agent.capabilities).toEqual(['planning']);

    const updateResponse = await api.patch(`/agents/${agent.id}`).send({ status: 'paused', maxConcurrency: 3 });
    expect(updateResponse.status).toBe(200);
    expect(updateResponse.body.agent.status).toBe('paused');
    expect(updateResponse.body.agent.maxConcurrency).toBe(3);
  });

  it('runs a task successfully and records immutable run history plus audit events', async () => {
    const agent = await createAgent(api);
    const task = await createTask(api);

    const triggerResponse = await api.post(`/tasks/${task.id}/runs`).send({});
    expect(triggerResponse.status).toBe(201);
    expect(triggerResponse.body.taskRun.status).toBe('queued');
    expect(triggerResponse.body.taskRun.agentId).toBe(agent.id);

    const completedTask = await waitForTaskStatus(api, task.id, 'completed');
    expect(completedTask.latestRunId).toBeTruthy();

    const runsResponse = await api.get(`/tasks/${task.id}/runs`);
    expect(runsResponse.status).toBe(200);
    expect(runsResponse.body.taskRuns).toHaveLength(1);
    expect(runsResponse.body.taskRuns[0].status).toBe('succeeded');
    expect(runsResponse.body.taskRuns[0].outputSummary).toContain('completed');

    const auditResponse = await api.get(`/tasks/${task.id}/audit-events`);
    expect(auditResponse.status).toBe(200);
    expect(auditResponse.body.auditEvents.map((event) => event.eventType)).toEqual([
      'task.created',
      'task_run.queued',
      'task_run.assigned',
      'task.status_changed',
      'task_run.started',
      'task_run.succeeded',
      'task.status_changed',
    ]);
  });

  it('returns 404 when triggering a run for a missing task', async () => {
    const response = await api.post('/tasks/does-not-exist/runs').send({});

    expect(response.status).toBe(404);
    expect(response.body.error).toBe('task not found');
  });

  it('returns 409 when a task already has an active run', async () => {
    await createAgent(api);
    const task = await createTask(api);

    const firstResponse = await api.post(`/tasks/${task.id}/runs`).send({});
    expect(firstResponse.status).toBe(201);

    const secondResponse = await api.post(`/tasks/${task.id}/runs`).send({});
    expect(secondResponse.status).toBe(409);
    expect(secondResponse.body.error).toBe('task already has an active run');
  });

  it('returns 409 when no eligible agent is available', async () => {
    const task = await createTask(api, { requiredCapabilities: ['python'] });

    const response = await api.post(`/tasks/${task.id}/runs`).send({});
    expect(response.status).toBe(409);
    expect(response.body.error).toBe('no eligible agent available');

    const auditResponse = await api.get(`/tasks/${task.id}/audit-events`);
    expect(auditResponse.body.auditEvents.at(-1).eventType).toBe('task_run.assignment_rejected');
  });

  it('uses preferred agent when it is eligible and falls back when it is not', async () => {
    const preferred = await createAgent(api, { name: 'Preferred Agent', capabilities: ['general'], status: 'active' });
    const fallback = await createAgent(api, { name: 'Fallback Agent', capabilities: ['general'], status: 'active' });

    const preferredTask = await createTask(api, { preferredAgentId: preferred.id });
    const preferredRun = await api.post(`/tasks/${preferredTask.id}/runs`).send({});
    expect(preferredRun.body.taskRun.agentId).toBe(preferred.id);
    await waitForTaskStatus(api, preferredTask.id, 'completed');

    await api.patch(`/agents/${preferred.id}`).send({ status: 'offline' });
    const fallbackTask = await createTask(api, { preferredAgentId: preferred.id });
    const fallbackRun = await api.post(`/tasks/${fallbackTask.id}/runs`).send({});
    expect(fallbackRun.body.taskRun.agentId).toBe(fallback.id);
  });

  it('enforces maxConcurrency and frees capacity after waiting_approval', async () => {
    const agent = await createAgent(api, { capabilities: ['review'], maxConcurrency: 1 });
    const firstTask = await createTask(api, { requiresApproval: true, preferredAgentId: agent.id, requiredCapabilities: ['review'] });
    const secondTask = await createTask(api, { preferredAgentId: agent.id, requiredCapabilities: ['review'] });

    const firstRun = await api.post(`/tasks/${firstTask.id}/runs`).send({});
    expect(firstRun.status).toBe(201);

    const blockedRun = await api.post(`/tasks/${secondTask.id}/runs`).send({});
    expect(blockedRun.status).toBe(409);
    expect(blockedRun.body.error).toBe('no eligible agent available');

    await waitForTaskStatus(api, firstTask.id, 'waiting_approval');

    const releasedRun = await api.post(`/tasks/${secondTask.id}/runs`).send({});
    expect(releasedRun.status).toBe(201);
    expect(releasedRun.body.taskRun.agentId).toBe(agent.id);
  });

  it('requests final-result approval when a task requires approval', async () => {
    const agent = await createAgent(api, { capabilities: ['review'] });
    expect(agent.activeRunCount).toBe(0);
    const task = await createTask(api, { requiresApproval: true });

    const runResponse = await api.post(`/tasks/${task.id}/runs`).send({});
    expect(runResponse.status).toBe(201);

    await waitForTaskStatus(api, task.id, 'waiting_approval');

    const taskResponse = await api.get(`/tasks/${task.id}`);
    expect(taskResponse.body.task.status).toBe('waiting_approval');

    const runsResponse = await api.get(`/tasks/${task.id}/runs`);
    expect(runsResponse.body.taskRuns).toHaveLength(1);
    expect(runsResponse.body.taskRuns[0].status).toBe('waiting_approval');
    expect(runsResponse.body.taskRuns[0].outputSummary).toContain('completed');

    const approvalsResponse = await api.get(`/tasks/${task.id}/approvals`);
    expect(approvalsResponse.status).toBe(200);
    expect(approvalsResponse.body.approvals).toHaveLength(1);
    expect(approvalsResponse.body.approvals[0].status).toBe('pending');

    const pendingResponse = await api.get('/approvals?status=pending');
    expect(pendingResponse.status).toBe(200);
    expect(pendingResponse.body.approvals).toHaveLength(1);

    const auditResponse = await api.get(`/tasks/${task.id}/audit-events`);
    expect(auditResponse.body.auditEvents.map((event) => event.eventType)).toEqual([
      'task.created',
      'task_run.queued',
      'task_run.assigned',
      'task.status_changed',
      'task_run.started',
      'task_run.waiting_approval',
      'approval.requested',
      'task.status_changed',
    ]);
  });

  it('approves a pending result and completes the task', async () => {
    await createAgent(api);
    const task = await createTask(api, { requiresApproval: true });

    await api.post(`/tasks/${task.id}/runs`).send({});
    await waitForTaskStatus(api, task.id, 'waiting_approval');

    const approvalsResponse = await api.get(`/tasks/${task.id}/approvals`);
    const approval = approvalsResponse.body.approvals[0];

    const approveResponse = await api.post(`/approvals/${approval.id}/approve`).send({
      decidedBy: 'team-lead',
      comment: 'Looks good',
    });
    expect(approveResponse.status).toBe(200);
    expect(approveResponse.body.approval.status).toBe('approved');

    await waitForTaskStatus(api, task.id, 'completed');

    const runsResponse = await api.get(`/tasks/${task.id}/runs`);
    expect(runsResponse.body.taskRuns[0].status).toBe('succeeded');

    const pendingResponse = await api.get('/approvals?status=pending');
    expect(pendingResponse.body.approvals).toHaveLength(0);

    const auditResponse = await api.get(`/tasks/${task.id}/audit-events`);
    expect(auditResponse.body.auditEvents.map((event) => event.eventType)).toContain('task_run.succeeded');
  });

  it('records assignment selection reasons for preferred and fallback choices', async () => {
    const preferred = await createAgent(api, { name: 'Preferred Agent', capabilities: ['review'] });
    const fallback = await createAgent(api, { name: 'Fallback Agent', capabilities: ['review'] });

    const preferredTask = await createTask(api, { preferredAgentId: preferred.id, requiredCapabilities: ['review'] });
    await api.post(`/tasks/${preferredTask.id}/runs`).send({});
    await waitForTaskStatus(api, preferredTask.id, 'completed');
    const preferredAudit = await api.get(`/tasks/${preferredTask.id}/audit-events`);
    const preferredAssignment = preferredAudit.body.auditEvents.find((event) => event.eventType === 'task_run.assigned');
    expect(preferredAssignment.payload.selectionReason).toBe('preferred_selected');

    await api.patch(`/agents/${preferred.id}`).send({ status: 'offline' });
    const fallbackTask = await createTask(api, { preferredAgentId: preferred.id, requiredCapabilities: ['review'] });
    await api.post(`/tasks/${fallbackTask.id}/runs`).send({});
    await waitForTaskStatus(api, fallbackTask.id, 'completed');
    const fallbackAudit = await api.get(`/tasks/${fallbackTask.id}/audit-events`);
    const fallbackAssignment = fallbackAudit.body.auditEvents.find((event) => event.eventType === 'task_run.assigned');
    expect(fallbackAssignment.payload.selectionReason).toBe('preferred_ineligible_fallback');
    expect(fallbackAssignment.payload.agentId).toBe(fallback.id);
  });

  it('rejects a pending result, returns task to ready, and allows rerun', async () => {
    await createAgent(api);
    const task = await createTask(api, { requiresApproval: true });

    await api.post(`/tasks/${task.id}/runs`).send({});
    await waitForTaskStatus(api, task.id, 'waiting_approval');

    const approvalsResponse = await api.get(`/tasks/${task.id}/approvals`);
    const approval = approvalsResponse.body.approvals[0];

    const rejectResponse = await api.post(`/approvals/${approval.id}/reject`).send({
      decidedBy: 'team-lead',
      comment: 'Please revise the output',
    });
    expect(rejectResponse.status).toBe(200);
    expect(rejectResponse.body.approval.status).toBe('rejected');

    await waitForTaskStatus(api, task.id, 'ready');

    const runsAfterReject = await api.get(`/tasks/${task.id}/runs`);
    expect(runsAfterReject.body.taskRuns[0].status).toBe('cancelled');

    const rerunResponse = await api.post(`/tasks/${task.id}/runs`).send({});
    expect(rerunResponse.status).toBe(201);
    expect(rerunResponse.body.taskRun.attempt).toBe(2);

    await waitForTaskStatus(api, task.id, 'waiting_approval');

    const finalApprovals = await api.get(`/tasks/${task.id}/approvals`);
    expect(finalApprovals.body.approvals).toHaveLength(2);
    expect(finalApprovals.body.approvals[0].status).toBe('rejected');
    expect(finalApprovals.body.approvals[1].status).toBe('pending');
  });

  it('fails a run and allows a rerun that creates a new immutable attempt', async () => {
    await createAgent(api);
    const task = await createTask(api);

    const failedRunResponse = await api.post(`/tasks/${task.id}/runs`).set('x-stub-run-outcome', 'fail').send({});
    expect(failedRunResponse.status).toBe(201);

    await waitForTaskStatus(api, task.id, 'failed');

    const rerunResponse = await api.post(`/tasks/${task.id}/runs`).send({});
    expect(rerunResponse.status).toBe(201);
    expect(rerunResponse.body.taskRun.attempt).toBe(2);

    await waitForTaskStatus(api, task.id, 'completed');

    const runsResponse = await api.get(`/tasks/${task.id}/runs`);
    expect(runsResponse.body.taskRuns).toHaveLength(2);
    expect(runsResponse.body.taskRuns[0].status).toBe('failed');
    expect(runsResponse.body.taskRuns[1].status).toBe('succeeded');
  });

  it('dispatches independent subtasks in parallel and rolls parent status up', async () => {
    await createAgent(api, { name: 'Planner A', capabilities: ['planning'], maxConcurrency: 2 });
    await createAgent(api, { name: 'Planner B', capabilities: ['planning'], maxConcurrency: 2 });
    const parentTask = await createTask(api, {
      title: 'Parallel parent',
      isSplittable: true,
      requiredCapabilities: [],
    });

    const childOne = await createSubtask(api, parentTask.id, { title: 'Child One', requiredCapabilities: ['planning'] });
    const childTwo = await createSubtask(api, parentTask.id, { title: 'Child Two', requiredCapabilities: ['planning'] });

    const dispatchResponse = await api.post(`/tasks/${parentTask.id}/parallel-runs`).send({});
    expect(dispatchResponse.status).toBe(201);
    expect(dispatchResponse.body.taskRuns).toHaveLength(2);
    expect(new Set(dispatchResponse.body.taskRuns.map((taskRun) => taskRun.taskId))).toEqual(new Set([childOne.id, childTwo.id]));

    await waitForTaskStatus(api, childOne.id, 'completed');
    await waitForTaskStatus(api, childTwo.id, 'completed');
    const parentDone = await waitForTaskStatus(api, parentTask.id, 'completed');
    expect(parentDone.status).toBe('completed');

    const treeAudit = await api.get(`/tasks/${parentTask.id}/audit-events?scope=tree`);
    expect(treeAudit.status).toBe(200);
    expect(treeAudit.body.auditEvents.some((event) => event.eventType === 'task.parallel_dispatch_requested')).toBe(true);
    expect(treeAudit.body.auditEvents.some((event) => event.payload?.taskId === childOne.id)).toBe(true);
  });

  it('rejects parallel dispatch when any ready child has no eligible agent', async () => {
    await createAgent(api, { name: 'Planner A', capabilities: ['planning'], maxConcurrency: 1 });
    const parentTask = await createTask(api, { title: 'Blocked parent', isSplittable: true });
    await createSubtask(api, parentTask.id, { title: 'Child One', requiredCapabilities: ['planning'] });
    await createSubtask(api, parentTask.id, { title: 'Child Two', requiredCapabilities: ['python'] });

    const dispatchResponse = await api.post(`/tasks/${parentTask.id}/parallel-runs`).send({});
    expect(dispatchResponse.status).toBe(409);
    expect(dispatchResponse.body.error).toBe('no eligible agent available for parallel dispatch');

    const subtasks = await api.get(`/tasks/${parentTask.id}/subtasks`);
    expect(subtasks.body.tasks.every((task) => task.status === 'ready')).toBe(true);
  });

  it('rejects parallel dispatch when sibling reservations exceed agent capacity', async () => {
    await createAgent(api, { name: 'Planner A', capabilities: ['planning'], maxConcurrency: 1 });
    const parentTask = await createTask(api, { title: 'Reserved parent', isSplittable: true });
    await createSubtask(api, parentTask.id, { title: 'Child One', requiredCapabilities: ['planning'] });
    await createSubtask(api, parentTask.id, { title: 'Child Two', requiredCapabilities: ['planning'] });

    const dispatchResponse = await api.post(`/tasks/${parentTask.id}/parallel-runs`).send({});
    expect(dispatchResponse.status).toBe(409);
    expect(dispatchResponse.body.error).toBe('no eligible agent available for parallel dispatch');
  });

  it('rolls parent to waiting_approval when a child is pending approval', async () => {
    await createAgent(api, { name: 'Reviewer A', capabilities: ['review'], maxConcurrency: 2 });
    const parentTask = await createTask(api, { title: 'Approval parent', isSplittable: true });
    const childOne = await createSubtask(api, parentTask.id, { title: 'Review child', requiredCapabilities: ['review'], requiresApproval: true });
    const childTwo = await createSubtask(api, parentTask.id, { title: 'Plain child', requiredCapabilities: ['review'] });

    const dispatchResponse = await api.post(`/tasks/${parentTask.id}/parallel-runs`).send({});
    expect(dispatchResponse.status).toBe(201);

    await waitForTaskStatus(api, childOne.id, 'waiting_approval');
    await waitForTaskStatus(api, childTwo.id, 'completed');
    const parentWaiting = await waitForTaskStatus(api, parentTask.id, 'waiting_approval');
    expect(parentWaiting.status).toBe('waiting_approval');
  });

  it('aggregates dashboard summary metrics across runs, approvals, agents, and subtasks', async () => {
    await createAgent(api, { name: 'Planner A', capabilities: ['planning'], maxConcurrency: 2 });
    await createAgent(api, { name: 'Reviewer A', capabilities: ['review'], maxConcurrency: 2 });

    const successTask = await createTask(api, { title: 'Success task', requiredCapabilities: ['planning'] });
    await api.post(`/tasks/${successTask.id}/runs`).send({});
    await waitForTaskStatus(api, successTask.id, 'completed');

    const failureTask = await createTask(api, { title: 'Failure task', requiredCapabilities: ['planning'] });
    await api.post(`/tasks/${failureTask.id}/runs`).set('x-stub-run-outcome', 'fail').send({});
    await waitForTaskStatus(api, failureTask.id, 'failed');

    const approvalTask = await createTask(api, {
      title: 'Approval task',
      requiredCapabilities: ['review'],
      requiresApproval: true,
    });
    await api.post(`/tasks/${approvalTask.id}/runs`).send({});
    await waitForTaskStatus(api, approvalTask.id, 'waiting_approval');

    const parentTask = await createTask(api, { title: 'Parent task', isSplittable: true });
    await createSubtask(api, parentTask.id, { title: 'Child A', requiredCapabilities: ['planning'] });
    await createSubtask(api, parentTask.id, { title: 'Child B', requiredCapabilities: ['planning'] });
    await api.post(`/tasks/${parentTask.id}/parallel-runs`).send({});
    await waitForTaskStatus(api, parentTask.id, 'completed');

    const summaryResponse = await api.get('/dashboard/summary');
    expect(summaryResponse.status).toBe(200);

    const { summary } = summaryResponse.body;
    expect(summary.tasks.root.total).toBe(4);
    expect(summary.tasks.root.completed).toBe(2);
    expect(summary.tasks.root.failed).toBe(1);
    expect(summary.tasks.root.waitingApproval).toBe(1);
    expect(summary.tasks.root.completionRate).toBe(0.5);
    expect(summary.tasks.subtasks.total).toBe(2);
    expect(summary.tasks.subtasks.completed).toBe(2);
    expect(summary.tasks.subtasks.completionRate).toBe(1);
    expect(summary.taskRuns.total).toBe(5);
    expect(summary.taskRuns.succeeded).toBe(3);
    expect(summary.taskRuns.failed).toBe(1);
    expect(summary.taskRuns.waitingApproval).toBe(1);
    expect(summary.approvals.total).toBe(1);
    expect(summary.approvals.pending).toBe(1);
    expect(summary.agents.total).toBe(2);
    expect(summary.agentPerformance).toHaveLength(2);
    expect(summary.pendingApprovals).toHaveLength(1);
    expect(summary.recentFailedRuns).toHaveLength(1);
  });
});
