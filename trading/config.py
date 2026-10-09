import os
from enum import Enum
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

class MarketDataMode(str, Enum):
    MOCK = "MOCK"
    GROWW = "GROWW"
    UPSTOX = "UPSTOX"
    PAPER = "PAPER"

class Settings:
    def __init__(self):
        # Gemini AI Config
        self.GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
        self.GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

        # Upstox API v2 Config
        self.UPSTOX_ACCESS_TOKEN: str = os.getenv("UPSTOX_ACCESS_TOKEN", "")
        self.UPSTOX_API_KEY: str = os.getenv("UPSTOX_API_KEY", "")
        self.UPSTOX_API_SECRET: str = os.getenv("UPSTOX_API_SECRET", "")
        self.UPSTOX_BASE_URL: str = os.getenv("UPSTOX_BASE_URL", "https://api.upstox.com/v2")

        # Groww API & TOTP Config
        self.GROWW_API_KEY: str = os.getenv("GROWW_API_KEY", "")
        self.GROWW_API_SECRET: str = os.getenv("GROWW_API_SECRET", "")
        self.GROWW_TOTP_SECRET: str = os.getenv("GROWW_TOTP_SECRET", "")
        self.GROWW_CLIENT_ID: str = os.getenv("GROWW_CLIENT_ID", "")
        self.GROWW_API_BASE_URL: str = os.getenv("GROWW_API_BASE_URL", "https://api.groww.in")
        self.GROWW_HTTP_TIMEOUT_SECONDS: float = float(os.getenv("GROWW_HTTP_TIMEOUT_SECONDS", "8.0"))
        self.GROWW_AUTH_MODE: str = os.getenv("GROWW_AUTH_MODE", "TOTP")

        # Telegram Alert Config
        self.TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
        self.ENABLE_TELEGRAM: bool = os.getenv("ENABLE_TELEGRAM", "false").lower() in ("true", "1", "yes")

        # Scalping & Trading Parameters (Dynamic Index Sizing)
        self.SYMBOL: str = os.getenv("SYMBOL", "NIFTY")
        
        # Dynamic lot sizing based on NSE / BSE Circulars
        self.LOT_SIZE_MAP = {
            "NIFTY": 65,
            "BANKNIFTY": 30,
            "FINNIFTY": 60,
            "MIDCPNIFTY": 120,
            "SENSEX": 20
        }
        custom_lot = os.getenv("LOT_SIZE")
        self.LOT_SIZE: int = int(custom_lot) if custom_lot else self.LOT_SIZE_MAP.get(self.SYMBOL.upper(), 65)

        self.ATM_OPTION_DELTA: float = float(os.getenv("ATM_OPTION_DELTA", "0.50"))
        self.BREAKOUT_THRESHOLD_PCT: float = float(os.getenv("BREAKOUT_THRESHOLD_PCT", "0.05"))
        self.MIN_COOLDOWN_SECONDS: int = int(os.getenv("MIN_COOLDOWN_SECONDS", "180"))
        self.MIN_SL_FLOOR_PTS: float = float(os.getenv("MIN_SL_FLOOR_PTS", "12.0")) # Minimum 12 pt SL floor prevents spread stop-outs
        self.DEFAULT_STOP_LOSS_PTS: float = float(os.getenv("DEFAULT_STOP_LOSS_PTS", "14.0"))
        self.DEFAULT_TARGET_PTS: float = float(os.getenv("DEFAULT_TARGET_PTS", "28.0"))
        self.RISK_REWARD_RATIO: float = float(os.getenv("RISK_REWARD_RATIO", "2.0"))
        self.MIN_EXECUTION_SCORE: int = int(os.getenv("MIN_EXECUTION_SCORE", "80"))
        raw_mode = os.getenv("MARKET_DATA_MODE", "MOCK").upper()
        try:
            self.MARKET_DATA_MODE: str = MarketDataMode(raw_mode).value
        except ValueError:
            import warnings
            warnings.warn(f"Unknown MARKET_DATA_MODE='{raw_mode}'. Falling back to MOCK. Valid values: MOCK, GROWW, PAPER")
            self.MARKET_DATA_MODE: str = MarketDataMode.MOCK.value
        self.OPTION_DATA_MODE: str = os.getenv("OPTION_DATA_MODE", "SYNTHETIC").upper()
        self.OPTION_SNAPSHOT_CACHE_SECONDS: float = float(os.getenv("OPTION_SNAPSHOT_CACHE_SECONDS", "20"))
        self.MAX_TRADE_HOLD_MINUTES: int = int(os.getenv("MAX_TRADE_HOLD_MINUTES", "20")) # Time-In-Force auto-exit on theta decay
        self.ESTIMATED_ROUNDTRIP_CHARGES: float = float(os.getenv("ESTIMATED_ROUNDTRIP_CHARGES", "75.0"))

        # Risk Management & Circuit Breakers
        self.MAX_DAILY_CONSECUTIVE_LOSSES: int = int(os.getenv("MAX_DAILY_CONSECUTIVE_LOSSES", "2")) # Hard-stop on 2 consecutive SLs
        self.MAX_DAILY_RUPEE_LOSS: float = float(os.getenv("MAX_DAILY_RUPEE_LOSS", "2500.0")) # Hard-stop on max daily loss in INR
        self.MAX_SIGNALS_PER_DAY: int = int(os.getenv("MAX_SIGNALS_PER_DAY", "5")) # Hard daily cap on maximum signals
        self.MAX_TICK_STALENESS_SECONDS: float = float(os.getenv("MAX_TICK_STALENESS_SECONDS", "3.0")) # Stale tick hard veto
        self.MAX_ALLOWED_SPREAD_PCT: float = float(os.getenv("MAX_ALLOWED_SPREAD_PCT", "0.40")) # Spread blowout hard veto
        self.BASE_RECOMMENDED_CAPITAL: float = float(os.getenv("BASE_RECOMMENDED_CAPITAL", "25000.0")) # Baseline capital cushion per lot
        self.ENFORCE_MARKET_HOURS: bool = os.getenv("ENFORCE_MARKET_HOURS", "true").lower() in ("true", "1", "yes") # 09:30 - 15:15 IST
        self.EVENT_RISK_WINDOW_MINUTES: int = int(os.getenv("EVENT_RISK_WINDOW_MINUTES", "30"))
        self.HIGH_RISK_EVENTS_JSON: str = os.getenv("HIGH_RISK_EVENTS_JSON", "[]")

        # Execution & Simulation
        self.DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")
        self.USE_MOCK_FEED_IF_OFFLINE: bool = os.getenv("USE_MOCK_FEED_IF_OFFLINE", "true").lower() in ("true", "1", "yes")
        self.DATABASE_PATH: str = os.getenv("DATABASE_PATH", str(BASE_DIR / "trades.db"))

        # Server settings
        self.SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
        self.SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8000"))

        # Advanced Risk Budgeting & Limit-Chase Execution
        self.ACCOUNT_EQUITY: float = float(os.getenv("ACCOUNT_EQUITY", "150000.0")) # Rs 1.5L equity for standard 1-lot NIFTY risk budget
        self.MAX_RISK_PER_TRADE_PCT: float = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "1.5")) # 1.5% risk budget allows 1-2 lots
        self.MAX_EXECUTION_LATENCY_MS: float = float(os.getenv("MAX_EXECUTION_LATENCY_MS", "300.0")) # Latency veto (>300ms)
        self.LIMIT_CHASE_TIMEOUT_SECONDS: float = float(os.getenv("LIMIT_CHASE_TIMEOUT_SECONDS", "2.0"))
        self.ENABLE_TIME_OF_DAY_FILTER: bool = os.getenv("ENABLE_TIME_OF_DAY_FILTER", "true").lower() in ("true", "1", "yes")
        self.ALLOW_SYNTHETIC_IN_LIVE: bool = False # Strict Invariant: Zero synthetic fallback in live mode

settings = Settings()
