export function createTaskRepository(state) {
  function linkParent(task) {
    if (!task.parentTaskId) {
      return;
    }

    const current = state.tasksByParentTaskId.get(task.parentTaskId) ?? [];
    if (!current.includes(task.id)) {
      state.tasksByParentTaskId.set(task.parentTaskId, [...current, task.id]);
    }
  }

  function getChildIds(parentTaskId) {
    return state.tasksByParentTaskId.get(parentTaskId) ?? [];
  }

  return {
    create(task) {
      state.tasks.set(task.id, task);
      linkParent(task);
      return task;
    },
    update(task) {
      state.tasks.set(task.id, task);
      linkParent(task);
      return task;
    },
    getById(taskId) {
      return state.tasks.get(taskId) ?? null;
    },
    list() {
      return Array.from(state.tasks.values()).sort((left, right) => {
        return left.createdAt.localeCompare(right.createdAt);
      });
    },
    listByParentTaskId(parentTaskId) {
      return getChildIds(parentTaskId)
        .map((taskId) => state.tasks.get(taskId))
        .filter(Boolean)
        .sort((left, right) => left.createdAt.localeCompare(right.createdAt));
    },
    hasChildren(taskId) {
      return getChildIds(taskId).length > 0;
    },
  };
}
