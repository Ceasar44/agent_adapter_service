"""Identifiers and enums shared across domain boundaries."""

from enum import StrEnum
from typing import NewType

VisitorId = NewType("VisitorId", str)
SaleorUserId = NewType("SaleorUserId", str)
ParlantCustomerId = NewType("ParlantCustomerId", str)
ParlantSessionId = NewType("ParlantSessionId", str)
ThreadId = NewType("ThreadId", str)
RunId = NewType("RunId", str)
TraceId = NewType("TraceId", str)


class AuthState(StrEnum):
    ANONYMOUS = "anonymous"
    AUTHENTICATED = "authenticated"
    UNAVAILABLE = "unavailable"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
