export function createAuditRepository(state) {
  return {
    append(auditEvent) {
      state.auditEvents.push(auditEvent);
      return auditEvent;
    },
    list() {
      return [...state.auditEvents].sort((left, right) => {
        return left.timestamp.localeCompare(right.timestamp);
      });
    },
  };
}
