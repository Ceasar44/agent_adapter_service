from sqlalchemy import select

from agent_adapter_service.persistence.contracts import AuditEvent, AuditLog, AuditQuery
from agent_adapter_service.persistence.models.audit_log import AuditLogModel as Model
from agent_adapter_service.persistence.repositories.base import ScopedRepository
from agent_adapter_service.persistence.sanitization import sanitize_arguments


class SqlAuditLogRepository(ScopedRepository):
    async def append(self, event: AuditEvent) -> AuditLog:
        values = event.model_dump()
        values["arguments_summary"] = sanitize_arguments(event.arguments_summary)
        async with self.database.session() as session:
            row = Model(**self.scope.model_dump(), **values)
            session.add(row)
            await session.flush()
            result = AuditLog.model_validate(row)
        return result

    async def search(self, query: AuditQuery | None = None) -> list[AuditLog]:
        query = query or AuditQuery()
        statement = select(Model).where(*self.scope_conditions(Model))
        for field in ("actor", "surface", "tool_name", "result_status", "trace_id"):
            value = getattr(query, field)
            if value is not None:
                statement = statement.where(getattr(Model, field) == value)
        if query.since is not None:
            statement = statement.where(Model.timestamp >= query.since)
        if query.until is not None:
            statement = statement.where(Model.timestamp <= query.until)
        statement = statement.order_by(Model.timestamp.desc(), Model.id.desc())
        async with self.database.session() as session:
            rows = await session.scalars(statement.limit(query.limit).offset(query.offset))
            return [AuditLog.model_validate(row) for row in rows]
