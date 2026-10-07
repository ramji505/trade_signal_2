# ⚡ NIFTY Options Scalping & AI Decision Support Engine

An institutional-grade, low-latency quantitative decision support and signal orchestration engine engineered for **Indian Index Derivatives (NSE NIFTY 50, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX)**. 

The system combines real-time tick telemetry, options microstructure (OI walls, PCR, IV regimes), Black-Scholes Greeks, a 0–100 Setup Quality Scoring model, a decoupled background **Google Gemini AI Anomaly Auditor**, and institutional circuit breakers.

---

## 📑 Table of Contents

- [Core Highlights](#-core-highlights)
- [System Architecture](#-system-architecture)
- [4-Stage Decision & Gating Pipeline](#-4-stage-decision--gating-pipeline)
- [Risk Management & Safety Circuit Breakers](#-risk-management--safety-circuit-breakers)
- [Project Structure](#-project-structure)
- [Installation & Setup](#-installation--setup)
- [Configuration Reference (`.env`)](#-configuration-reference-env)
- [Usage & Execution](#-usage--execution)
- [API & WebSocket Telemetry](#-api--websocket-telemetry)
- [Backtesting & Statutory Cost Modeling](#-backtesting--statutory-cost-modeling)
- [Test Suite](#-test-suite)
- [Disclaimer](#-disclaimer)

---

## 🌟 Core Highlights

- **⚡ Sub-Millisecond Signal Hot-Path**: Technical filtering and setup quality scoring execute within `< 5ms`. Heavy AI context checks run asynchronously in decoupled background workers without blocking execution.
- **🎯 0–100 Setup Quality Scoring**: Ranks intraday setups across 7 quantitative dimensions into graded tiers (`A+`, `A`, `B`, `REJECT`).
- **📊 Options Microstructure Analysis**: Live tracking of Near-the-Money (NTM) Put-Call Ratio (PCR), Call/Put open interest (OI) concentration walls, ATM Implied Volatility (IV), and Max Pain.
- **🤖 Decoupled Gemini AI Auditor**: Leverages `gemini-2.5-flash` to audit macro contradictions, false breakout traps, and abnormal liquidity grabs.
- **🛡️ Multi-Tier Risk Protection**: Circuit breaker hard-stops on consecutive losses, spread blowout filters, stale-tick vetoes, and floor-protected Stop-Loss ($SL \ge 12\text{ pts}$).
- **📱 Real-Time Telegram Alerts**: Instant mobile signal delivery complete with underlying spot invalidation levels, option entry zones, risk-reward ratios, and Groww 1-tap Scalper deep links.
- **📈 Real-Time HTML5/WebSocket Dashboard**: Live streaming tick charts, VWAP, EMA ribbons, regime indicators, live trade journals, and win-rate statistics.
- **🧪 Institutional Backtester**: Chronological candle replay with strict **SL-First invariant**, slippage modeling, and exact statutory Indian taxes (STT, GST, Exchange turnover, SEBI, Stamp Duty).

---

## 🏛️ System Architecture

```mermaid
flowchart TD
    subgraph Data_Feeds["Data Ingestion & Feed Layer"]
        A1[Groww API / TOTP Auth] --> B[Candle & Tick Engine]
        A2[Mock Feed Generator] --> B
        B --> C[Indicators: VWAP, EMA 9/21, ATR, ORH/ORL]
        B --> D[Options Microstructure: OI Walls, PCR, IV]
    end

    subgraph Decision_Pipeline["4-Stage Decision Pipeline"]
        C & D --> E1[Stage 1: Trend & Momentum Gating]
        E1 --> E2[Stage 2: Microstructure & Spread Veto]
        E2 --> E3[Stage 3: 0–100 Setup Quality Scoring]
        E3 --> E4[Stage 4: Asynchronous Gemini AI Anomaly Audit]
    end

    subgraph Risk_Circuit["Risk Management & Circuit Breaker"]
        F1[Max Consecutive Losses Check]
        F2[Stale Tick / Spread Blowout Veto]
        F3[Floor-Protected SL & Invalidation]
        Decision_Pipeline --> Risk_Circuit
    end

    subgraph Execution_Distribution["Distribution & Persistence"]
        Risk_Circuit --> G1[SQLite Database: trades.db]
        Risk_Circuit --> G2[WebSocket Server: port 8001]
        Risk_Circuit --> G3[Custom Asyncio HTTP Server: port 8000]
        Risk_Circuit --> G4[Telegram Bot Dispatcher]
    end
```

---

## 🚦 4-Stage Decision & Gating Pipeline

1. **Stage 1 — Market Structure & Momentum Gating**:
   - Evaluates Spot vs VWAP, EMA 9 vs EMA 21, EMA slope, and Opening Range (15-min ORH/ORL).
   - Identifies whether the index is in an expansion breakout or range-bound chop.
2. **Stage 2 — Options Microstructure & Volume Confluence**:
   - Assesses NTM Put-Call Ratio ($PCR \ge 1.05$ for Bullish / $PCR \le 0.85$ for Bearish).
   - Detects Call/Put concentration resistance/support walls.
   - Requires Volume Expansion Ratio $\ge 1.25\times$ 20-period average volume.
3. **Stage 3 — 0–100 Setup Quality Scoring Engine**:
   - Scored across 7 weighted dimensions:
     - **Market Regime**: 0–20 pts
     - **Price Structure & Key Levels**: 0–20 pts
     - **VWAP & EMA Alignment**: 0–15 pts
     - **Volume Expansion**: 0–10 pts
     - **Options Microstructure & OI Walls**: 0–15 pts
     - **IV & Spread Liquidity**: 0–10 pts
     - **Trap Risk Audit**: 0–10 pts
   - Setups scoring $\ge 80$ are marked Grade **A** / **A+** for high-probability execution.
4. **Stage 4 — Gemini AI Anomaly & Contradiction Audit**:
   - Operates in a decoupled background loop (every 3 minutes) auditing for spot vs OI divergence, false breakout liquidity grabs, and unexpected volatility spikes.

---

## 🛡️ Risk Management & Safety Circuit Breakers

| Mechanism | Setting / Invariant | Purpose |
| :--- | :--- | :--- |
| **Consecutive Loss Hard-Stop** | Max 2 consecutive losses | Halts trading engine immediately to protect capital during hostile market chop. |
| **Minimum SL Floor** | $\ge 12.0\text{ pts}$ (Option) | Prevents premature stop-outs caused by bid-ask spread jitter. |
| **Risk-to-Reward (R:R)** | Minimum $1 : 2.0$ | Ensures positive mathematical expectancy over the long run. |
| **Stale Tick Veto** | Max $3.0\text{s}$ lag | Suppresses signal generation if live feed experiences latency or disconnections. |
| **Spread Blowout Veto** | Max $0.40\%$ spread | Rejects illiquid or wide-spread option strikes. |
| **Time-In-Force (TIF) Exit** | Max 20 minutes | Auto-exits stagnant scalps to eliminate severe option Theta decay. |
| **Market Hours Enforcement** | 09:30 – 15:15 IST | Ignores erratic opening 15-minute traps (09:15–09:30 IST). |

---

## 📂 Project Structure

```
d:/trading/
├── .env                       # Active environment configuration
├── .env.example               # Template environment configuration
├── analyzer.py                # Setup quality scoring (0-100) & Gemini AI regime auditor
├── auth.py                    # Groww API TOTP authentication & token manager
├── backtester.py              # Chronological SL-first backtest engine & metrics exporter
├── config.py                  # Pydantic & environment settings manager
├── conftest.py                # Pytest global fixtures
├── database.py                # SQLite persistence (signals, trades, accuracy metrics)
├── engine.py                  # Trading orchestrator, circuit breakers, signal publisher
├── feed.py                    # Real-time tick & candle buffer, VWAP/EMA indicators, OI snapshot
├── main.py                    # Application bootstrap & entrypoint
├── notifier.py                # Telegram rich decision support card dispatcher
├── options_pricing.py         # Black-Scholes Greeks, IV calculations, Max Pain engine
├── requirements.txt           # Python package dependencies
├── server.py                  # FastAPI/AsyncIO HTTP server & WebSocket broadcaster
├── trades.db                  # SQLite database
├── static/
│   └── index.html             # Real-time web UI dashboard (Charts, Telemetry, Signal logs)
├── tests/
│   └── test_engine.py         # Test suite covering all modules (confluence, Greeks, DB, risk)
└── outputs/
    ├── accuracy_report.csv    # Exported accuracy & statistical breakdown
    ├── backtest_results.json  # Comprehensive backtest results
    ├── daily_performance.json # Daily performance ledger
    ├── signals_history.csv    # Exported signals history
    └── trades_history.csv     # Exported trades history
```

---

## 🚀 Installation & Setup

### 1. Prerequisites
- **Python 3.10+** (Tested on Python 3.10 – 3.14)
- **Git**

### 2. Clone and Setup Environment
```bash
# Clone the repository
git clone <repository_url>
cd trading

# Create and activate virtual environment
python -m venv .venv

# On Windows:
.venv\Scripts\activate

# On Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
```

---

## ⚙️ Configuration Reference (`.env`)

```ini
# Google Gemini AI Configuration
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash

# Groww API & TOTP Credentials
GROWW_API_KEY=your_groww_api_key
GROWW_API_SECRET=your_groww_api_secret
GROWW_TOTP_SECRET=your_totp_base32_secret
GROWW_CLIENT_ID=your_client_id

# Telegram Decision Support Alerts
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
ENABLE_TELEGRAM=false

# Trading Index & Lot Sizing
SYMBOL=NIFTY
LOT_SIZE=65                      # NIFTY=65, BANKNIFTY=30, FINNIFTY=65, SENSEX=20

# Scalping Parameters & Risk Rules
BREAKOUT_THRESHOLD_PCT=0.05      # Proximity to Day High/Low
MIN_COOLDOWN_SECONDS=180         # 3 minutes cooldown between signals
MIN_SL_FLOOR_PTS=12.0            # Minimum option SL floor
DEFAULT_STOP_LOSS_PTS=14.0       # Default option SL in points
DEFAULT_TARGET_PTS=28.0          # Default option Target in points (1:2 R:R)
RISK_REWARD_RATIO=2.0
MAX_TRADE_HOLD_MINUTES=20        # Time-in-force auto exit
MAX_DAILY_CONSECUTIVE_LOSSES=2   # Circuit breaker threshold
MAX_DAILY_RUPEE_LOSS=2500.0      # INR Hard loss cap
MAX_SIGNALS_PER_DAY=5            # Daily maximum signals cap
ENFORCE_MARKET_HOURS=false       # Enable strict 09:30-15:15 IST filtering

# Execution & Simulation
DRY_RUN=true                     # true: Paper trading mode; false: Live execution
USE_MOCK_FEED_IF_OFFLINE=true    # Fallback to simulated feed if market is closed
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
```

---

## 💻 Usage & Execution

### 1. Launch the Live Trading & Dashboard Server
```bash
python main.py
```
- **Web Dashboard**: [http://localhost:8000](http://localhost:8000)
- **WebSocket Feed**: `ws://localhost:8001`

### 2. Run the Quantitative Backtest Replay
```bash
python backtester.py
```
Runs a simulated chronological backtest, outputs performance metrics to the console, and updates files in `outputs/`.

---

## 📡 API & WebSocket Telemetry

### REST Endpoints (`http://localhost:8000`)

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/` | `GET` | Serves the HTML5 live monitoring dashboard. |
| `/api/status` | `GET` | Fetches current spot price, day high/low, VWAP, EMA, and engine gate status. |
| `/api/signals` | `GET` | Retrieves the 50 most recent generated signals. |
| `/api/trades` | `GET` | Retrieves recent executed/paper trades with PnL. |
| `/api/metrics` | `GET` | Returns aggregated accuracy metrics (Win Rate %, Profit Factor, Expectancy). |
| `/api/daily-accuracy` | `GET` | Returns day-by-day accuracy breakdown. |
| `/api/trigger-analysis` | `POST` | Forces an immediate on-demand evaluation cycle. |

### WebSocket Telemetry (`ws://localhost:8001`)

- **`INIT` Event**: Emitted upon connection, providing initial index metrics, OI walls, and recent signals.
- **`TICK` Event**: Streamed every second with spot price, VWAP, EMA 9/21, ATR, PCR, Max Call/Put wall, current regime, and active setup payload.
- **`NEW_SIGNAL` Event**: Broadcast when a qualified Grade A/A+ setup is generated.

---

## 📊 Backtesting & Statutory Cost Modeling

The backtester calculates realistic net returns by deducting Indian statutory taxes per lot:
- **Brokerage**: ₹20 flat per executed order (₹40 round-trip)
- **Securities Transaction Tax (STT)**: 0.15% on option premium sell turnover (revised standard)
- **Exchange Turnover Charges**: 0.03503% (NSE)
- **GST**: 18% on (Brokerage + Exchange Charges + SEBI Fees)
- **SEBI Turnover Charges**: ₹10 per Crore
- **Stamp Duty**: 0.003% on buy turnover

Results are exported to `outputs/`:
- `backtest_results.json`: Summary statistics (Win Rate, Profit Factor, Max Drawdown, Expectancy).
- `accuracy_report.csv`: Tabular metric summary.
- `trades_history.csv` & `signals_history.csv`: Full trade-by-trade logs.

---

## 🧪 Test Suite

Run the full 39-test quantitative regression suite verifying market structure, Black-Scholes Greeks, database persistence, circuit breakers, true MTF aggregation, and look-ahead protection:

```bash
pytest tests/test_engine.py -v
```

All **39 / 39 tests** pass cleanly with test database isolation.

---

## ⚠️ Disclaimer

> [!CAUTION]
> **For Educational & Decision Support Purposes Only.**
> 
> Options trading involves substantial risk of loss and is not suitable for every investor. The signals, scores, and analytics generated by this software are decision support aids. Always perform your own due diligence and risk management before executing live financial transactions.

> Upgrade V2: strict live-data/authentication controls, options-aware scoring, current cost parameters, event-risk vetoes, and statistically honest backtest reporting.
