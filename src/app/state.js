export function createState() {
  return {
    tasks: new Map(),
    tasksByParentTaskId: new Map(),
    taskRuns: new Map(),
    taskRunsByTaskId: new Map(),
    approvals: new Map(),
    approvalsByTaskId: new Map(),
    agents: new Map(),
    auditEvents: [],
  };
}
