const state = {
  sections: {
    summary: createSection('loading'),
    tasks: createSection('loading'),
    approvals: createSection('loading'),
    agents: createSection('loading'),
  },
  detail: createSection('idle'),
  selectedTaskId: null,
  activeAction: null,
};

const elements = {
  refreshButton: document.querySelector('#refresh-button'),
  statusBanner: document.querySelector('#status-banner'),
  summaryGeneratedAt: document.querySelector('#summary-generated-at'),
  summaryCards: document.querySelector('#summary-cards'),
  rootTasks: document.querySelector('#root-tasks'),
  pendingApprovals: document.querySelector('#pending-approvals'),
  agents: document.querySelector('#agents'),
  recentFailedRuns: document.querySelector('#recent-failed-runs'),
  recentActivity: document.querySelector('#recent-activity'),
  drawer: document.querySelector('#task-drawer'),
  drawerOverlay: document.querySelector('#drawer-overlay'),
  drawerClose: document.querySelector('#drawer-close'),
  drawerTitle: document.querySelector('#drawer-title'),
  drawerSubtitle: document.querySelector('#drawer-subtitle'),
  drawerBody: document.querySelector('#drawer-body'),
};

const sectionLoaders = [
  { key: 'summary', url: '/dashboard/summary', pick: (body) => body.summary },
  { key: 'tasks', url: '/tasks', pick: (body) => body.tasks },
  { key: 'approvals', url: '/approvals?status=pending', pick: (body) => body.approvals },
  { key: 'agents', url: '/agents', pick: (body) => body.agents },
];

bindEvents();
renderOverview();
renderDrawer();
reloadDashboard();

function bindEvents() {
  elements.refreshButton.addEventListener('click', () => {
    reloadDashboard();
  });

  elements.drawerClose.addEventListener('click', closeDrawer);
  elements.drawerOverlay.addEventListener('click', closeDrawer);

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closeDrawer();
    }
  });

  document.addEventListener('click', async (event) => {
    const actionElement = event.target.closest('[data-action]');
    if (!actionElement) {
      return;
    }

    const { action, taskId, approvalId } = actionElement.dataset;

    if (action === 'select-task' && taskId) {
      await openTask(taskId);
      return;
    }

    if (action === 'trigger-run' && taskId) {
      await runMutation('Queued task run.', async () => {
        await postJson(`/tasks/${taskId}/runs`, { triggeredBy: 'dashboard-ui' });
      });
      return;
    }

    if (action === 'trigger-parallel-run' && taskId) {
      await runMutation('Queued parallel subtask runs.', async () => {
        await postJson(`/tasks/${taskId}/parallel-runs`, { triggeredBy: 'dashboard-ui' });
      });
      return;
    }

    if (action === 'approve' && approvalId) {
      const confirmed = window.confirm('Approve this pending result?');
      if (!confirmed) {
        return;
      }

      await runMutation('Approval accepted.', async () => {
        await postJson(`/approvals/${approvalId}/approve`, {
          decidedBy: 'dashboard-ui',
          comment: 'Approved from the dashboard console.',
        });
      });
      return;
    }

    if (action === 'reject' && approvalId) {
      const confirmed = window.confirm('Reject this pending result?');
      if (!confirmed) {
        return;
      }

      await runMutation('Approval rejected.', async () => {
        await postJson(`/approvals/${approvalId}/reject`, {
          decidedBy: 'dashboard-ui',
          comment: 'Rejected from the dashboard console.',
        });
      });
    }
  });
}

function createSection(status = 'idle') {
  return { status, data: null, error: null };
}

async function reloadDashboard(options = {}) {
  const quiet = Boolean(options.quiet);

  for (const loader of sectionLoaders) {
    state.sections[loader.key] = {
      ...state.sections[loader.key],
      status: 'loading',
      error: null,
    };
  }

  if (!quiet) {
    setBanner('Refreshing dashboard data…', 'muted');
  }

  renderOverview();

  const results = await Promise.allSettled(sectionLoaders.map((loader) => fetchJson(loader.url)));

  let errorCount = 0;

  results.forEach((result, index) => {
    const loader = sectionLoaders[index];
    if (result.status === 'fulfilled') {
      state.sections[loader.key] = {
        status: 'ready',
        data: loader.pick(result.value),
        error: null,
      };
      return;
    }

    errorCount += 1;
    state.sections[loader.key] = {
      status: 'error',
      data: null,
      error: result.reason,
    };
  });

  renderOverview();

  if (state.selectedTaskId) {
    await loadTaskDetail(state.selectedTaskId, { quiet: true });
  }

  if (errorCount === 0) {
    const generatedAt = state.sections.summary.data?.generatedAt;
    setBanner(
      generatedAt ? `Snapshot updated ${formatDateTime(generatedAt)}.` : 'Dashboard refreshed.',
      'success'
    );
    return;
  }

  setBanner(`Loaded with ${errorCount} section error${errorCount === 1 ? '' : 's'}.`, 'warning');
}

async function openTask(taskId) {
  state.selectedTaskId = taskId;
  setDrawerOpen(true);
  await loadTaskDetail(taskId);
}

function closeDrawer() {
  state.selectedTaskId = null;
  state.detail = createSection('idle');
  setDrawerOpen(false);
  renderDrawer();
}

function setDrawerOpen(isOpen) {
  elements.drawer.classList.toggle('is-open', isOpen);
  elements.drawer.setAttribute('aria-hidden', String(!isOpen));
  elements.drawerOverlay.hidden = !isOpen;
}

async function loadTaskDetail(taskId, options = {}) {
  const quiet = Boolean(options.quiet);
  state.detail = createSection('loading');
  renderDrawer();

  if (!quiet) {
    setBanner('Loading task detail…', 'muted');
  }

  try {
    const [taskResponse, subtasksResponse, runsResponse, approvalsResponse, auditResponse] = await Promise.all([
      fetchJson(`/tasks/${taskId}`),
      fetchJson(`/tasks/${taskId}/subtasks`),
      fetchJson(`/tasks/${taskId}/runs`),
      fetchJson(`/tasks/${taskId}/approvals`),
      fetchJson(`/tasks/${taskId}/audit-events?scope=tree`),
    ]);

    if (state.selectedTaskId !== taskId) {
      return;
    }

    state.detail = {
      status: 'ready',
      error: null,
      data: {
        task: taskResponse.task,
        subtasks: subtasksResponse.tasks,
        runs: runsResponse.taskRuns,
        approvals: approvalsResponse.approvals,
        auditEvents: auditResponse.auditEvents,
      },
    };

    renderDrawer();

    if (!quiet) {
      setBanner(`Opened task ${taskResponse.task.title}.`, 'success');
    }
  } catch (error) {
    if (state.selectedTaskId !== taskId) {
      return;
    }

    state.detail = {
      status: 'error',
      data: null,
      error,
    };
    renderDrawer();
    setBanner(error.message, 'danger');
  }
}

async function runMutation(successMessage, action) {
  if (state.activeAction) {
    return;
  }

  state.activeAction = successMessage;
  renderOverview();
  renderDrawer();
  setBanner('Applying action…', 'muted');

  try {
    await action();
    await delay(40);
    await reloadDashboard({ quiet: true });
    setBanner(successMessage, 'success');
  } catch (error) {
    setBanner(error.message, 'danger');
  } finally {
    state.activeAction = null;
    renderOverview();
    renderDrawer();
  }
}

function renderOverview() {
  renderSummary();
  renderTasks();
  renderApprovals();
  renderAgents();
  renderRecentFailedRuns();
  renderRecentActivity();
}

function renderSummary() {
  const section = state.sections.summary;

  if (section.status === 'loading') {
    elements.summaryGeneratedAt.textContent = 'Refreshing metrics…';
    elements.summaryCards.innerHTML = renderStateMessage({
      title: 'Loading summary',
      description: 'Collecting task, run, approval, and agent rollups from the backend.',
    });
    return;
  }

  if (section.status === 'error') {
    elements.summaryGeneratedAt.textContent = 'Summary unavailable';
    elements.summaryCards.innerHTML = renderStateMessage({
      title: 'Summary unavailable',
      description: section.error.message,
      tone: 'danger',
    });
    return;
  }

  const summary = section.data;
  elements.summaryGeneratedAt.textContent = `Generated ${formatDateTime(summary.generatedAt)}`;

  const cards = [
    {
      label: 'Root task completion',
      value: `${summary.tasks.root.total}`,
      meta: `${formatPercent(summary.tasks.root.completionRate)} completed · ${summary.tasks.root.waitingApproval} waiting approval`,
    },
    {
      label: 'Subtask throughput',
      value: `${summary.tasks.subtasks.total}`,
      meta: `${formatPercent(summary.tasks.subtasks.completionRate)} completed · ${summary.tasks.subtasks.running} running`,
    },
    {
      label: 'Task run success',
      value: `${formatPercent(summary.taskRuns.successRate)}`,
      meta: `${summary.taskRuns.total} total runs · ${formatDuration(summary.taskRuns.averageExecutionMs)} average execution`,
    },
    {
      label: 'Pending approvals',
      value: `${summary.approvals.pending}`,
      meta: `${summary.approvals.total} total approvals · ${formatDuration(summary.approvals.averageWaitMs)} average wait`,
    },
    {
      label: 'Agent capacity',
      value: `${summary.agents.busy}/${summary.agents.total}`,
      meta: `${summary.agents.active} active · ${summary.agents.idle} idle`,
    },
    {
      label: 'Operational signals',
      value: `${summary.recentFailedRuns.length}`,
      meta: `${summary.recentActivity.length} recent events · ${summary.activeRootTasks.length} active roots`,
    },
  ];

  elements.summaryCards.innerHTML = cards.map((card) => `
    <article class="summary-card">
      <p class="summary-card__label">${escapeHtml(card.label)}</p>
      <p class="summary-card__value">${escapeHtml(card.value)}</p>
      <p class="summary-card__meta">${escapeHtml(card.meta)}</p>
    </article>
  `).join('');
}

function renderTasks() {
  const section = state.sections.tasks;
  if (section.status === 'loading') {
    elements.rootTasks.innerHTML = renderStateMessage({
      title: 'Loading tasks',
      description: 'Fetching the current task tree so root work can be inspected and triggered.',
    });
    return;
  }

  if (section.status === 'error') {
    elements.rootTasks.innerHTML = renderStateMessage({
      title: 'Tasks unavailable',
      description: section.error.message,
      tone: 'danger',
    });
    return;
  }

  const tasks = section.data;
  const rootTasks = tasks.filter((task) => !task.parentTaskId);

  if (rootTasks.length === 0) {
    elements.rootTasks.innerHTML = renderStateMessage({
      title: 'No root tasks yet',
      description: 'Create tasks through the existing API and they will appear here automatically.',
    });
    return;
  }

  const subtaskCounts = new Map();
  for (const task of tasks) {
    if (!task.parentTaskId) {
      continue;
    }

    subtaskCounts.set(task.parentTaskId, (subtaskCounts.get(task.parentTaskId) ?? 0) + 1);
  }

  elements.rootTasks.innerHTML = `
    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>Task</th>
            <th>Status</th>
            <th>Capabilities</th>
            <th>Subtasks</th>
            <th>Mode</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          ${rootTasks.map((task) => `
            <tr>
              <td data-label="Task">
                <button class="link-button" type="button" data-action="select-task" data-task-id="${escapeHtml(task.id)}">
                  ${escapeHtml(task.title)}
                </button>
                <p class="cell-meta">${escapeHtml(truncate(task.description || 'No description provided.', 120))}</p>
              </td>
              <td data-label="Status">${renderStatusPill(task.status)}</td>
              <td data-label="Capabilities">${renderTagList(task.requiredCapabilities, 'No capability filter')}</td>
              <td data-label="Subtasks">${escapeHtml(String(subtaskCounts.get(task.id) ?? 0))}</td>
              <td data-label="Mode">${task.isSplittable ? '<span class="pill">Parallel parent</span>' : '<span class="pill">Leaf task</span>'}</td>
              <td data-label="Updated">${escapeHtml(formatDateTime(task.updatedAt))}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

function renderApprovals() {
  const section = state.sections.approvals;
  if (section.status === 'loading') {
    elements.pendingApprovals.innerHTML = renderStateMessage({
      title: 'Loading approvals',
      description: 'Checking for results that need a human decision.',
    });
    return;
  }

  if (section.status === 'error') {
    elements.pendingApprovals.innerHTML = renderStateMessage({
      title: 'Approvals unavailable',
      description: section.error.message,
      tone: 'danger',
    });
    return;
  }

  const approvals = section.data;
  if (approvals.length === 0) {
    elements.pendingApprovals.innerHTML = renderStateMessage({
      title: 'No pending approvals',
      description: 'When a task or subtask finishes in waiting approval, it will show up here.',
    });
    return;
  }

  elements.pendingApprovals.innerHTML = `
    <ul class="stack-list">
      ${approvals.map((approval) => `
        <li class="stack-list__item">
          <div class="stack-list__header">
            <div>
              <h3>${escapeHtml(getTaskName(approval.taskId))}</h3>
              <p class="timeline__meta">${escapeHtml(approval.type)} · requested ${escapeHtml(formatDateTime(approval.requestedAt))}</p>
            </div>
            ${renderStatusPill(approval.status)}
          </div>
          <div class="stack-list__body">
            <p>Run <span class="code-inline">${escapeHtml(approval.taskRunId)}</span> is waiting for a final-result decision.</p>
          </div>
          <div class="button-group">
            <button class="button button--primary" type="button" data-action="select-task" data-task-id="${escapeHtml(approval.taskId)}">View task</button>
            <button class="button" type="button" data-action="approve" data-approval-id="${escapeHtml(approval.id)}" ${state.activeAction ? 'disabled' : ''}>Approve</button>
            <button class="button button--danger" type="button" data-action="reject" data-approval-id="${escapeHtml(approval.id)}" ${state.activeAction ? 'disabled' : ''}>Reject</button>
          </div>
        </li>
      `).join('')}
    </ul>
  `;
}

function renderAgents() {
  const section = state.sections.agents;
  if (section.status === 'loading') {
    elements.agents.innerHTML = renderStateMessage({
      title: 'Loading agents',
      description: 'Retrieving registered agent capacity and live load.',
    });
    return;
  }

  if (section.status === 'error') {
    elements.agents.innerHTML = renderStateMessage({
      title: 'Agents unavailable',
      description: section.error.message,
      tone: 'danger',
    });
    return;
  }

  const agents = section.data;
  if (agents.length === 0) {
    elements.agents.innerHTML = renderStateMessage({
      title: 'No agents registered',
      description: 'Add agents through the existing API to enable task assignment and runtime metrics.',
    });
    return;
  }

  const performanceByAgentId = new Map((state.sections.summary.data?.agentPerformance ?? []).map((metric) => [metric.agentId, metric]));

  elements.agents.innerHTML = `
    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>Agent</th>
            <th>Status</th>
            <th>Capabilities</th>
            <th>Load</th>
            <th>Success rate</th>
            <th>Average execution</th>
          </tr>
        </thead>
        <tbody>
          ${agents.map((agent) => {
            const performance = performanceByAgentId.get(agent.id);
            return `
              <tr>
                <td data-label="Agent">
                  <strong>${escapeHtml(agent.name)}</strong>
                  <p class="cell-meta">${escapeHtml(agent.type)} · concurrency ${escapeHtml(String(agent.maxConcurrency))}</p>
                </td>
                <td data-label="Status">${renderStatusPill(agent.status)}</td>
                <td data-label="Capabilities">${renderTagList(agent.capabilities, 'No capabilities')}</td>
                <td data-label="Load">${escapeHtml(String(agent.activeRunCount))} active</td>
                <td data-label="Success rate">${escapeHtml(formatPercent(performance?.successRate ?? 0))}</td>
                <td data-label="Average execution">${escapeHtml(formatDuration(performance?.averageExecutionMs ?? 0))}</td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    </div>
  `;
}

function renderRecentFailedRuns() {
  const summarySection = state.sections.summary;
  if (summarySection.status === 'loading') {
    elements.recentFailedRuns.innerHTML = renderStateMessage({
      title: 'Loading failures',
      description: 'Gathering the most recent failed task runs from the summary snapshot.',
    });
    return;
  }

  if (summarySection.status === 'error') {
    elements.recentFailedRuns.innerHTML = renderStateMessage({
      title: 'Failed runs unavailable',
      description: summarySection.error.message,
      tone: 'danger',
    });
    return;
  }

  const recentFailedRuns = summarySection.data.recentFailedRuns;
  if (recentFailedRuns.length === 0) {
    elements.recentFailedRuns.innerHTML = renderStateMessage({
      title: 'No recent failures',
      description: 'The latest snapshot does not contain failed runs.',
    });
    return;
  }

  elements.recentFailedRuns.innerHTML = `
    <ul class="stack-list">
      ${recentFailedRuns.map((run) => `
        <li class="stack-list__item">
          <div class="stack-list__header">
            <div>
              <h3>${escapeHtml(getTaskName(run.taskId))}</h3>
              <p class="timeline__meta">Attempt ${escapeHtml(String(run.attempt))} · finished ${escapeHtml(formatDateTime(run.finishedAt))}</p>
            </div>
            ${renderStatusPill(run.status)}
          </div>
          <div class="stack-list__body">
            <p>${escapeHtml(run.failureReason || 'No failure reason supplied.')}</p>
          </div>
          <div class="button-group">
            <button class="button button--primary" type="button" data-action="select-task" data-task-id="${escapeHtml(run.taskId)}">Inspect task</button>
          </div>
        </li>
      `).join('')}
    </ul>
  `;
}

function renderRecentActivity() {
  const summarySection = state.sections.summary;
  if (summarySection.status === 'loading') {
    elements.recentActivity.innerHTML = renderStateMessage({
      title: 'Loading activity',
      description: 'Fetching the latest audit events from the summary endpoint.',
    });
    return;
  }

  if (summarySection.status === 'error') {
    elements.recentActivity.innerHTML = renderStateMessage({
      title: 'Activity unavailable',
      description: summarySection.error.message,
      tone: 'danger',
    });
    return;
  }

  const recentActivity = summarySection.data.recentActivity;
  if (recentActivity.length === 0) {
    elements.recentActivity.innerHTML = renderStateMessage({
      title: 'No audit events yet',
      description: 'Task, run, approval, and agent operations will stream into this activity feed.',
    });
    return;
  }

  elements.recentActivity.innerHTML = `
    <ul class="timeline">
      ${recentActivity.map((event) => {
        const relatedTaskId = getRelatedTaskId(event);
        return `
          <li class="timeline__item">
            <div class="timeline__header">
              <div>
                <h3>${escapeHtml(formatEventName(event.eventType))}</h3>
                <p class="timeline__meta">${escapeHtml(event.actorType)} · ${escapeHtml(event.actorId)} · ${escapeHtml(formatDateTime(event.timestamp))}</p>
              </div>
              ${relatedTaskId ? `<button class="button button--ghost" type="button" data-action="select-task" data-task-id="${escapeHtml(relatedTaskId)}">Open task</button>` : ''}
            </div>
            <div class="timeline__body">
              <p>${escapeHtml(summarizeAuditEvent(event))}</p>
            </div>
          </li>
        `;
      }).join('')}
    </ul>
  `;
}

function renderDrawer() {
  if (state.detail.status === 'idle' || !state.selectedTaskId) {
    elements.drawerTitle.textContent = 'Select a task';
    elements.drawerSubtitle.textContent = 'Use the root task list, failed runs, approvals, or activity feed to open a task.';
    elements.drawerBody.innerHTML = renderStateMessage({
      title: 'No task selected',
      description: 'The detail drawer will show task metadata, subtasks, runs, approvals, and the tree audit timeline.',
    });
    return;
  }

  if (state.detail.status === 'loading') {
    elements.drawerTitle.textContent = 'Loading task detail';
    elements.drawerSubtitle.textContent = 'Fetching metadata, subtasks, runs, approvals, and audit events…';
    elements.drawerBody.innerHTML = renderStateMessage({
      title: 'Loading task detail',
      description: 'The drawer is requesting the task endpoints in parallel.',
    });
    return;
  }

  if (state.detail.status === 'error') {
    elements.drawerTitle.textContent = 'Task detail unavailable';
    elements.drawerSubtitle.textContent = state.detail.error.message;
    elements.drawerBody.innerHTML = renderStateMessage({
      title: 'Task detail unavailable',
      description: state.detail.error.message,
      tone: 'danger',
    });
    return;
  }

  const { task, subtasks, runs, approvals, auditEvents } = state.detail.data;
  const pendingApproval = approvals.find((approval) => approval.status === 'pending') ?? null;
  const readySubtasks = subtasks.filter((subtask) => subtask.status === 'ready').length;
  const canTriggerLeafRun = !task.isSplittable && subtasks.length === 0 && !['running', 'waiting_approval'].includes(task.status);
  const canTriggerParallelRun = task.isSplittable && readySubtasks > 0;

  elements.drawerTitle.textContent = task.title;
  elements.drawerSubtitle.textContent = task.description || 'No task description provided.';

  elements.drawerBody.innerHTML = `
    <section class="drawer-actions">
      <div class="button-group">
        <button class="button button--primary" type="button" data-action="trigger-run" data-task-id="${escapeHtml(task.id)}" ${canTriggerLeafRun && !state.activeAction ? '' : 'disabled'}>
          Trigger leaf run
        </button>
        <button class="button button--primary" type="button" data-action="trigger-parallel-run" data-task-id="${escapeHtml(task.id)}" ${canTriggerParallelRun && !state.activeAction ? '' : 'disabled'}>
          Trigger parent parallel run
        </button>
        <button class="button" type="button" data-action="select-task" data-task-id="${escapeHtml(task.id)}">
          Refresh detail
        </button>
        ${pendingApproval ? `<button class="button" type="button" data-action="approve" data-approval-id="${escapeHtml(pendingApproval.id)}" ${state.activeAction ? 'disabled' : ''}>Approve</button>` : ''}
        ${pendingApproval ? `<button class="button button--danger" type="button" data-action="reject" data-approval-id="${escapeHtml(pendingApproval.id)}" ${state.activeAction ? 'disabled' : ''}>Reject</button>` : ''}
      </div>
      <p class="helper-text">
        ${escapeHtml(buildDrawerHint(task, subtasks, pendingApproval))}
      </p>
    </section>

    <section class="drawer-grid">
      <article class="drawer-section">
        <h3>Metadata</h3>
        <dl class="meta-grid">
          ${renderMetaItem('Task ID', task.id)}
          ${renderMetaItem('Status', formatStatus(task.status))}
          ${renderMetaItem('Created by', task.createdBy)}
          ${renderMetaItem('Created at', formatDateTime(task.createdAt))}
          ${renderMetaItem('Updated at', formatDateTime(task.updatedAt))}
          ${renderMetaItem('Latest run', task.latestRunId ?? 'None yet')}
          ${renderMetaItem('Requires approval', task.requiresApproval ? 'Yes' : 'No')}
          ${renderMetaItem('Task type', task.isSplittable ? 'Splittable parent' : 'Leaf or child task')}
          ${renderMetaItem('Preferred agent', task.preferredAgentId ?? 'No preference')}
          ${renderMetaItem('Parent task', task.parentTaskId ?? 'Root task')}
          ${renderMetaItem('Capabilities', task.requiredCapabilities?.join(', ') || 'No capability filter')}
          ${renderMetaItem('Pending approvals', String(approvals.filter((approval) => approval.status === 'pending').length))}
        </dl>
      </article>

      <article class="drawer-section">
        <h3>Subtasks</h3>
        ${subtasks.length === 0 ? renderStateMessage({
          title: 'No subtasks',
          description: 'This task is currently operating as a leaf task.',
        }) : `
          <ul class="stack-list">
            ${subtasks.map((subtask) => `
              <li class="stack-list__item">
                <div class="stack-list__header">
                  <div>
                    <h3>${escapeHtml(subtask.title)}</h3>
                    <p class="timeline__meta">Updated ${escapeHtml(formatDateTime(subtask.updatedAt))}</p>
                  </div>
                  ${renderStatusPill(subtask.status)}
                </div>
                <div class="button-group">
                  <button class="button button--ghost" type="button" data-action="select-task" data-task-id="${escapeHtml(subtask.id)}">Open subtask</button>
                </div>
              </li>
            `).join('')}
          </ul>
        `}
      </article>

      <article class="drawer-section drawer-section--full">
        <h3>Runs</h3>
        ${runs.length === 0 ? renderStateMessage({
          title: 'No runs recorded',
          description: 'Trigger a run to populate execution history for this task.',
        }) : renderRunsTable(runs)}
      </article>

      <article class="drawer-section drawer-section--full">
        <h3>Approvals</h3>
        ${approvals.length === 0 ? renderStateMessage({
          title: 'No approvals',
          description: 'Approvals only appear for tasks that require final-result review.',
        }) : renderApprovalsTable(approvals)}
      </article>

      <article class="drawer-section drawer-section--full">
        <h3>Audit timeline</h3>
        ${auditEvents.length === 0 ? renderStateMessage({
          title: 'No audit events',
          description: 'Task activity will appear here as the backend records it.',
        }) : `
          <ul class="timeline">
            ${auditEvents.map((event) => `
              <li class="timeline__item">
                <div class="timeline__header">
                  <div>
                    <h3>${escapeHtml(formatEventName(event.eventType))}</h3>
                    <p class="timeline__meta">${escapeHtml(formatDateTime(event.timestamp))} · ${escapeHtml(event.actorType)} · ${escapeHtml(event.actorId)}</p>
                  </div>
                  <span class="pill">${escapeHtml(event.entityType)}</span>
                </div>
                <div class="timeline__body">
                  <p>${escapeHtml(summarizeAuditEvent(event))}</p>
                </div>
              </li>
            `).join('')}
          </ul>
        `}
      </article>
    </section>
  `;
}

function renderRunsTable(runs) {
  return `
    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>Attempt</th>
            <th>Status</th>
            <th>Agent</th>
            <th>Queued</th>
            <th>Finished</th>
            <th>Summary</th>
          </tr>
        </thead>
        <tbody>
          ${runs.map((run) => `
            <tr>
              <td data-label="Attempt">${escapeHtml(String(run.attempt))}</td>
              <td data-label="Status">${renderStatusPill(run.status)}</td>
              <td data-label="Agent">${escapeHtml(run.agentId ?? 'Unassigned')}</td>
              <td data-label="Queued">${escapeHtml(formatDateTime(run.queuedAt))}</td>
              <td data-label="Finished">${escapeHtml(run.finishedAt ? formatDateTime(run.finishedAt) : 'Still active')}</td>
              <td data-label="Summary">${escapeHtml(run.outputSummary ?? run.failureReason ?? 'No output yet')}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

function renderApprovalsTable(approvals) {
  return `
    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>Type</th>
            <th>Status</th>
            <th>Requested</th>
            <th>Decided</th>
            <th>Comment</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${approvals.map((approval) => `
            <tr>
              <td data-label="Type">${escapeHtml(approval.type)}</td>
              <td data-label="Status">${renderStatusPill(approval.status)}</td>
              <td data-label="Requested">${escapeHtml(formatDateTime(approval.requestedAt))}</td>
              <td data-label="Decided">${escapeHtml(approval.decidedAt ? formatDateTime(approval.decidedAt) : 'Pending')}</td>
              <td data-label="Comment">${escapeHtml(approval.comment ?? 'No comment')}</td>
              <td data-label="Actions">
                ${approval.status === 'pending' ? `
                  <div class="button-group">
                    <button class="button" type="button" data-action="approve" data-approval-id="${escapeHtml(approval.id)}" ${state.activeAction ? 'disabled' : ''}>Approve</button>
                    <button class="button button--danger" type="button" data-action="reject" data-approval-id="${escapeHtml(approval.id)}" ${state.activeAction ? 'disabled' : ''}>Reject</button>
                  </div>
                ` : '<span class="empty-value">Resolved</span>'}
              </td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

function renderMetaItem(label, value) {
  return `
    <div>
      <dt>${escapeHtml(label)}</dt>
      <dd>${escapeHtml(value)}</dd>
    </div>
  `;
}

function renderStatusPill(status) {
  return `<span class="status-pill status-pill--${escapeHtml(status)}">${escapeHtml(formatStatus(status))}</span>`;
}

function renderTagList(values, emptyLabel) {
  if (!values || values.length === 0) {
    return `<span class="empty-value">${escapeHtml(emptyLabel)}</span>`;
  }

  return `<div class="pill-list">${values.map((value) => `<span class="pill">${escapeHtml(value)}</span>`).join('')}</div>`;
}

function renderStateMessage({ title, description, tone = 'muted' }) {
  return `
    <div class="state-message state-message--${escapeHtml(tone)}">
      <h3>${escapeHtml(title)}</h3>
      <p>${escapeHtml(description)}</p>
    </div>
  `;
}

function setBanner(message, tone) {
  elements.statusBanner.textContent = message;
  elements.statusBanner.className = `banner banner--${tone}`;
}

function getTasks() {
  return state.sections.tasks.data ?? [];
}

function getTaskMap() {
  return new Map(getTasks().map((task) => [task.id, task]));
}

function getTaskName(taskId) {
  return getTaskMap().get(taskId)?.title ?? `Task ${taskId}`;
}

function getRelatedTaskId(event) {
  return event.payload?.taskId ?? (event.entityType === 'task' ? event.entityId : null);
}

function buildDrawerHint(task, subtasks, pendingApproval) {
  if (pendingApproval) {
    return 'This task currently has a pending approval decision. Use approve or reject to resolve it.';
  }

  if (task.isSplittable && subtasks.length === 0) {
    return 'This parent task is splittable, but it has no subtasks yet. Parallel dispatch stays disabled until children exist.';
  }

  if (task.isSplittable) {
    return 'Parallel dispatch is enabled when at least one child task is still in the ready state.';
  }

  if (subtasks.length > 0) {
    return 'Direct leaf execution is disabled because this task currently owns child work items.';
  }

  return 'Leaf execution is available when the task is not already running or waiting on approval.';
}

function summarizeAuditEvent(event) {
  const payload = event.payload ?? {};
  const parts = [];

  if (payload.taskId) {
    parts.push(`Task ${getTaskName(payload.taskId)}`);
  }
  if (payload.status) {
    parts.push(`status ${formatStatus(payload.status)}`);
  }
  if (payload.selectionReason) {
    parts.push(`selection ${formatStatus(payload.selectionReason)}`);
  }
  if (payload.agentId) {
    parts.push(`agent ${payload.agentId}`);
  }
  if (payload.reason) {
    parts.push(`reason ${formatStatus(payload.reason)}`);
  }
  if (payload.comment) {
    parts.push(`comment ${payload.comment}`);
  }
  if (payload.failureReason) {
    parts.push(`failure ${payload.failureReason}`);
  }
  if (payload.outputSummary) {
    parts.push(payload.outputSummary);
  }

  return parts.length > 0 ? parts.join(' · ') : 'No additional payload details were recorded for this event.';
}

function formatEventName(value) {
  return formatStatus(value).replace(/\b\w/g, (character) => character.toUpperCase());
}

function formatStatus(value) {
  return String(value).replace(/[._]/g, ' ');
}

function formatPercent(value) {
  return `${Math.round((Number(value) || 0) * 100)}%`;
}

function formatDuration(value) {
  const milliseconds = Number(value) || 0;
  if (milliseconds <= 0) {
    return '0 ms';
  }

  if (milliseconds < 1000) {
    return `${milliseconds} ms`;
  }

  if (milliseconds < 60000) {
    return `${(milliseconds / 1000).toFixed(1)} s`;
  }

  return `${(milliseconds / 60000).toFixed(1)} min`;
}

function formatDateTime(value) {
  if (!value) {
    return '—';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }

  return new Intl.DateTimeFormat('en', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

function truncate(value, length) {
  if (!value || value.length <= length) {
    return value;
  }

  return `${value.slice(0, Math.max(0, length - 1)).trimEnd()}…`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => null);

  if (!response.ok) {
    throw new Error(body?.error ?? `Request failed with status ${response.status}`);
  }

  return body;
}

async function postJson(url, body) {
  return fetchJson(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });
}

function delay(milliseconds) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
}
