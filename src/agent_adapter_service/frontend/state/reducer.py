import json
from collections.abc import Mapping
from datetime import UTC, datetime

from pydantic import ValidationError

from agent_adapter_service.frontend.state.models import (
    FrontendState,
    FrontendStateDelta,
    FrontendStateError,
    StateVersionConflict,
)


class FrontendStateReducer:
    def __init__(self, *, max_bytes: int = 65536) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = max_bytes

    def _bounded(self, value: object) -> None:
        try:
            size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, RecursionError, UnicodeError):
            raise FrontendStateError("State must be bounded JSON") from None
        if size > self.max_bytes:
            raise FrontendStateError("Frontend state exceeds size limit")

    def _validate(self, data: dict) -> FrontendState:
        try:
            # Timestamp is server-owned, never copied from browser input.
            data["updated_at"] = datetime.now(UTC)
            state = FrontendState.model_validate(data)
            self._bounded(state.model_dump(mode="json"))
            return state
        except (ValidationError, ValueError, TypeError):
            raise FrontendStateError("Invalid or unknown frontend state fields") from None

    def apply_snapshot(
        self,
        current: FrontendState | None,
        snapshot: FrontendState | Mapping,
    ) -> FrontendState:
        data = (
            snapshot.model_dump(mode="json")
            if isinstance(snapshot, FrontendState)
            else dict(snapshot)
        )
        self._bounded(data)
        state = self._validate(data)
        if state.version != (current.version if current else 0) + 1:
            raise StateVersionConflict("Snapshot must advance the current revision by one")
        return state

    def apply_delta(
        self,
        current: FrontendState | None,
        delta: FrontendStateDelta | Mapping,
    ) -> FrontendState:
        data = (
            delta.model_dump(mode="json") if isinstance(delta, FrontendStateDelta) else dict(delta)
        )
        self._bounded(data)
        try:
            change = FrontendStateDelta.model_validate(data)
        except ValidationError:
            raise FrontendStateError("Invalid state delta") from None
        if current is None or change.base_version != current.version:
            raise StateVersionConflict("Delta requires an existing matching revision")
        if change.version != change.base_version + 1:
            raise StateVersionConflict("Delta must advance the current revision by one")
        merged = current.model_dump()
        for section, patch in change.changes.items():
            if section not in FrontendState.model_fields or section in {"version", "updated_at"}:
                raise FrontendStateError("Unknown or reserved state field")
            if not isinstance(patch, dict):
                raise FrontendStateError("State sections must be objects")
            merged[section] = {**merged[section], **patch}
        merged["version"] = change.version
        return self._validate(merged)
