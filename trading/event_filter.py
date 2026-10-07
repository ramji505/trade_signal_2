from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import settings


@dataclass(frozen=True)
class EventRisk:
    risk_level: str
    event_name: Optional[str]
    allow_trading: bool
    confidence_penalty: int


class EventRiskFilter:
    """Manual, auditable high-impact event veto calendar.

    Events are supplied through HIGH_RISK_EVENTS_JSON, for example:
    [{"name":"RBI Policy","start":"2026-10-09T10:00:00+05:30","end":"2026-10-09T12:00:00+05:30","risk":"EXTREME"}]
    """
    def __init__(self) -> None:
        try:
            raw = json.loads(settings.HIGH_RISK_EVENTS_JSON or "[]")
            self.events = raw if isinstance(raw, list) else []
        except json.JSONDecodeError:
            self.events = []

    def evaluate(self, current_dt: Optional[datetime] = None) -> EventRisk:
        now = current_dt or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        window = timedelta(minutes=settings.EVENT_RISK_WINDOW_MINUTES)
        for event in self.events:
            try:
                start = datetime.fromisoformat(str(event["start"]).replace("Z", "+00:00"))
                end = datetime.fromisoformat(str(event.get("end", event["start"])).replace("Z", "+00:00"))
                if start.tzinfo is None:
                    start = start.replace(tzinfo=now.tzinfo)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=now.tzinfo)
                risk = str(event.get("risk", "HIGH")).upper()
                if start - window <= now <= end + window:
                    if risk == "EXTREME":
                        return EventRisk("EXTREME", str(event.get("name", "High-risk event")), False, 25)
                    if risk == "HIGH":
                        return EventRisk("HIGH", str(event.get("name", "High-risk event")), False, 20)
                    return EventRisk("MEDIUM", str(event.get("name", "Scheduled event")), True, 10)
            except (KeyError, TypeError, ValueError):
                continue
        return EventRisk("LOW", None, True, 0)
