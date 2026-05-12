import { createId } from '../../shared/ids.js';
import { nowIso } from '../../shared/time.js';

export function createAuditService({ auditRepository }) {
  function append({ entityType, entityId, eventType, actorType, actorId, correlationId, payload }) {
    const auditEvent = {
      id: createId(),
      entityType,
      entityId,
      eventType,
      actorType,
      actorId,
      timestamp: nowIso(),
      correlationId,
      payload,
    };

    return auditRepository.append(auditEvent);
  }

  function listForTask(taskId, options = {}) {
    const descendantTaskIds = new Set(options.descendantTaskIds ?? []);

    return auditRepository.list().filter((event) => {
      if (event.entityType === 'task' && event.entityId === taskId) {
        return true;
      }

      if (event.payload?.taskId === taskId) {
        return true;
      }

      return descendantTaskIds.has(event.payload?.taskId) || descendantTaskIds.has(event.entityId);
    });
  }

  return {
    append,
    listForTask,
  };
}
