import { AppError } from '../../shared/errors.js';
import { createId } from '../../shared/ids.js';
import { nowIso } from '../../shared/time.js';

const AGENT_STATUSES = new Set(['active', 'paused', 'offline', 'deprecated']);

function normalizeCapabilities(capabilities) {
  if (!Array.isArray(capabilities)) {
    return [];
  }

  return [...new Set(capabilities.map((value) => String(value).trim()).filter(Boolean))];
}

export function createAgentService({ agentRepository, taskRunRepository, auditService }) {
  function createAgent({
    name,
    type = 'stub-local',
    status = 'active',
    capabilities = [],
    maxConcurrency = 1,
    metadata = {},
  }) {
    const normalizedName = typeof name === 'string' ? name.trim() : '';
    if (!normalizedName) {
      throw new AppError(400, 'agent name is required');
    }

    const normalizedStatus = normalizeStatus(status);
    const normalizedCapabilities = normalizeCapabilities(capabilities);
    const normalizedMaxConcurrency = normalizeConcurrency(maxConcurrency);
    const timestamp = nowIso();

    const agent = {
      id: createId(),
      name: normalizedName,
      type,
      status: normalizedStatus,
      capabilities: normalizedCapabilities,
      maxConcurrency: normalizedMaxConcurrency,
      createdAt: timestamp,
      updatedAt: timestamp,
      lastSeenAt: timestamp,
      metadata: metadata && typeof metadata === 'object' && !Array.isArray(metadata) ? metadata : {},
    };

    agentRepository.create(agent);
    auditService.append({
      entityType: 'agent',
      entityId: agent.id,
      eventType: 'agent.registered',
      actorType: 'user',
      actorId: 'api-user',
      correlationId: agent.id,
      payload: {
        agentId: agent.id,
        status: agent.status,
        capabilities: agent.capabilities,
        maxConcurrency: agent.maxConcurrency,
      },
    });

    return withLoad(agent);
  }

  function listAgents() {
    return agentRepository.list().map(withLoad);
  }

  function getAgent(agentId) {
    const agent = agentRepository.getById(agentId);
    if (!agent) {
      throw new AppError(404, 'agent not found');
    }

    return withLoad(agent);
  }

  function updateAgent(agentId, updates) {
    const agent = requireAgent(agentId);
    const nextStatus = updates.status === undefined ? agent.status : normalizeStatus(updates.status);
    const nextCapabilities = updates.capabilities === undefined ? agent.capabilities : normalizeCapabilities(updates.capabilities);
    const nextMaxConcurrency = updates.maxConcurrency === undefined ? agent.maxConcurrency : normalizeConcurrency(updates.maxConcurrency);
    const nextMetadata = updates.metadata === undefined ? agent.metadata : updates.metadata;

    const updatedAgent = {
      ...agent,
      name: updates.name === undefined ? agent.name : String(updates.name).trim() || agent.name,
      type: updates.type === undefined ? agent.type : updates.type,
      status: nextStatus,
      capabilities: nextCapabilities,
      maxConcurrency: nextMaxConcurrency,
      metadata: nextMetadata && typeof nextMetadata === 'object' && !Array.isArray(nextMetadata) ? nextMetadata : agent.metadata,
      updatedAt: nowIso(),
      lastSeenAt: updates.lastSeenAt === undefined ? agent.lastSeenAt : updates.lastSeenAt,
    };

    agentRepository.update(updatedAgent);
    auditService.append({
      entityType: 'agent',
      entityId: updatedAgent.id,
      eventType: 'agent.updated',
      actorType: 'user',
      actorId: 'api-user',
      correlationId: updatedAgent.id,
      payload: {
        agentId: updatedAgent.id,
        status: updatedAgent.status,
        capabilities: updatedAgent.capabilities,
        maxConcurrency: updatedAgent.maxConcurrency,
      },
    });

    return withLoad(updatedAgent);
  }

  function selectAgentForTask(task, options = {}) {
    const requiredCapabilities = Array.isArray(task.requiredCapabilities) ? task.requiredCapabilities : [];
    const reservedLoads = options.reservedLoads ?? new Map();

    const preferredAgent = task.preferredAgentId ? agentRepository.getById(task.preferredAgentId) : null;
    if (preferredAgent && isEligible(preferredAgent, requiredCapabilities, reservedLoads)) {
      return {
        agent: withLoad(preferredAgent),
        selectionReason: 'preferred_selected',
      };
    }

    const candidates = agentRepository.list()
      .filter((agent) => isEligible(agent, requiredCapabilities, reservedLoads))
      .map(withLoad)
      .sort((left, right) => {
        const leftLoad = left.activeRunCount + (reservedLoads.get(left.id) ?? 0);
        const rightLoad = right.activeRunCount + (reservedLoads.get(right.id) ?? 0);
        if (leftLoad !== rightLoad) {
          return leftLoad - rightLoad;
        }

        if (left.createdAt !== right.createdAt) {
          return left.createdAt.localeCompare(right.createdAt);
        }

        return left.id.localeCompare(right.id);
      });

    if (candidates[0]) {
      return {
        agent: candidates[0],
        selectionReason: preferredAgent ? 'preferred_ineligible_fallback' : 'least_loaded_match',
      };
    }

    return null;
  }

  function requireAgent(agentId) {
    const agent = agentRepository.getById(agentId);
    if (!agent) {
      throw new AppError(404, 'agent not found');
    }

    return agent;
  }

  function withLoad(agent) {
    return {
      ...agent,
      activeRunCount: taskRunRepository.countActiveByAgentId(agent.id),
    };
  }

  function isEligible(agent, requiredCapabilities, reservedLoads = new Map()) {
    if (agent.status !== 'active') {
      return false;
    }

    const hasCapabilities = requiredCapabilities.every((capability) => agent.capabilities.includes(capability));
    if (!hasCapabilities) {
      return false;
    }

    return taskRunRepository.countActiveByAgentId(agent.id) + (reservedLoads.get(agent.id) ?? 0) < agent.maxConcurrency;
  }

  function normalizeStatus(status) {
    const normalizedStatus = String(status);
    if (!AGENT_STATUSES.has(normalizedStatus)) {
      throw new AppError(400, 'invalid agent status');
    }

    return normalizedStatus;
  }

  function normalizeConcurrency(value) {
    const normalizedValue = Number(value);
    if (!Number.isInteger(normalizedValue) || normalizedValue <= 0) {
      throw new AppError(400, 'maxConcurrency must be a positive integer');
    }

    return normalizedValue;
  }

  return {
    createAgent,
    listAgents,
    getAgent,
    updateAgent,
    selectAgentForTask,
  };
}
