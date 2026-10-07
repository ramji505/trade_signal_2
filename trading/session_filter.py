"""
Intraday Time-of-Day Market Session & Chop Zone Filter.
Enforces institutional session regimes across the Indian NSE trading day (IST):
- 09:15 - 09:30: Opening Price Discovery (Min Score 85)
- 09:30 - 11:30: Prime Morning Expansion (Min Score 80)
- 11:30 - 13:30: Midday Chop Zone (Elevated Min Score 88 - Filters False Breakouts)
- 13:30 - 15:10: Prime Afternoon Trend Continuation (Min Score 80)
- 15:10 - 15:30: EOD Expiry / Closing Lockout (Zero New Positions)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone, timedelta
from typing import Optional

from config import settings

IST_OFFSET = timedelta(hours=5, minutes=30)
IST_TZ = timezone(IST_OFFSET, name="IST")

@dataclass(frozen=True)
class SessionState:
    session_name: str
    allow_trading: bool
    is_chop_zone: bool
    required_min_score: int
    veto_reason: Optional[str] = None

class SessionTimeFilter:
    def __init__(self, enforce_market_hours: bool = True):
        self.enforce_hours = enforce_market_hours

    @staticmethod
    def get_ist_now(dt: Optional[datetime] = None) -> datetime:
        if dt is None:
            return datetime.now(timezone.utc).astimezone(IST_TZ)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc).astimezone(IST_TZ)
        return dt.astimezone(IST_TZ)

    def evaluate_session(self, current_dt: Optional[datetime] = None) -> SessionState:
        now_ist = self.get_ist_now(current_dt)
        curr_time = now_ist.time()

        # NSE Equity & F&O Market Hours: 09:15 to 15:30 IST
        market_open = time(9, 15)
        opening_discovery_end = time(9, 30)
        morning_prime_end = time(11, 30)
        chop_zone_end = time(13, 30)
        closing_lockout = time(15, 10)
        market_close = time(15, 30)

        # 1. Outside Market Hours Check
        if self.enforce_hours and settings.ENFORCE_MARKET_HOURS:
            if curr_time < market_open or curr_time > market_close:
                return SessionState(
                    session_name="OUTSIDE_MARKET_HOURS",
                    allow_trading=False,
                    is_chop_zone=False,
                    required_min_score=100,
                    veto_reason=f"HARD_VETO_OUTSIDE_NSE_HOURS_({curr_time.strftime('%H:%M')} IST)"
                )

        # 2. End-of-Day Closing Lockout (> 15:10 IST)
        if (self.enforce_hours and settings.ENFORCE_MARKET_HOURS) and curr_time >= closing_lockout:
            return SessionState(
                session_name="MARKET_CLOSING_LOCKOUT",
                allow_trading=False,
                is_chop_zone=False,
                required_min_score=100,
                veto_reason=f"HARD_VETO_MARKET_CLOSING_LOCKOUT_({curr_time.strftime('%H:%M')} IST)"
            )

        # 3. Opening Price Discovery (09:15 - 09:30 IST)
        if curr_time < opening_discovery_end:
            return SessionState(
                session_name="OPENING_AUCTION_DISCOVERY",
                allow_trading=True,
                is_chop_zone=False,
                required_min_score=max(settings.MIN_EXECUTION_SCORE, 85),
                veto_reason=None
            )

        # 4. Prime Morning Momentum (09:30 - 11:30 IST)
        if curr_time < morning_prime_end:
            return SessionState(
                session_name="PRIME_MORNING_MOMENTUM",
                allow_trading=True,
                is_chop_zone=False,
                required_min_score=settings.MIN_EXECUTION_SCORE,
                veto_reason=None
            )

        # 5. Midday European Lull / Chop Zone (11:30 - 13:30 IST)
        # Filters out false breakouts by requiring Grade A+ confluence (Score >= 88)
        if curr_time < chop_zone_end:
            return SessionState(
                session_name="MIDDAY_CHOP_ZONE",
                allow_trading=True,
                is_chop_zone=True,
                required_min_score=max(settings.MIN_EXECUTION_SCORE + 8, 88),
                veto_reason=None
            )

        # 6. Prime Afternoon Institutional Expansion (13:30 - 15:10 IST)
        return SessionState(
            session_name="PRIME_AFTERNOON_EXPANSION",
            allow_trading=True,
            is_chop_zone=False,
            required_min_score=settings.MIN_EXECUTION_SCORE,
            veto_reason=None
        )

session_time_filter = SessionTimeFilter()
