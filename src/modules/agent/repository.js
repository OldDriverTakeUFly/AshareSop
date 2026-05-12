export function createAgentRepository(state) {
  return {
    create(agent) {
      state.agents.set(agent.id, agent);
      return agent;
    },
    update(agent) {
      state.agents.set(agent.id, agent);
      return agent;
    },
    getById(agentId) {
      return state.agents.get(agentId) ?? null;
    },
    list() {
      return Array.from(state.agents.values()).sort((left, right) => {
        if (left.createdAt !== right.createdAt) {
          return left.createdAt.localeCompare(right.createdAt);
        }

        return left.id.localeCompare(right.id);
      });
    },
  };
}
