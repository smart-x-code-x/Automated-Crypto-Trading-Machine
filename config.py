"""
╔══════════════════════════════════════════════════════════════╗
║              SoDEX Trading Bot — Configuration              ║
╚══════════════════════════════════════════════════════════════╝

Central configuration for the SoDEX crypto trading bot.
All tuneable parameters live here.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# 🔑  API / Wallet Credentials
# ─────────────────────────────────────────────
# Your EVM wallet address (used as API key on SoDEX)
API_KEY = os.getenv("SODEX_API_KEY", "")

# EVM private key for EIP-712 signing (NEVER commit this!)
PRIVATE_KEY = os.getenv("SODEX_PRIVATE_KEY", "")

# SoDEX account ID (uint64)
ACCOUNT_ID = int(os.getenv("SODEX_ACCOUNT_ID", "0"))

# ─────────────────────────────────────────────
# 🌐  Network / Endpoints
# ─────────────────────────────────────────────
ENVIRONMENT = os.getenv("SODEX_ENV", "mainnet")  # "mainnet" or "testnet"

ENDPOINTS = {
    "mainnet": {
        "rest_spot": "https://mainnet-gw.sodex.dev/api/v1/spot",
        "rest_perps": "https://mainnet-gw.sodex.dev/api/v1/perps",
        "ws_spot": "wss://mainnet-gw.sodex.dev/ws/spot",
        "ws_perps": "wss://mainnet-gw.sodex.dev/ws/perps",
        "chain_id": 286623,
    },
    "testnet": {
        "rest_spot": "https://testnet-gw.sodex.dev/api/v1/spot",
        "rest_perps": "https://testnet-gw.sodex.dev/api/v1/perps",
        "ws_spot": "wss://testnet-gw.sodex.dev/ws/spot",
        "ws_perps": "wss://testnet-gw.sodex.dev/ws/perps",
        "chain_id": 138565,
    },
}


def get_endpoint(key: str) -> str:
    """Return the endpoint URL for the current environment."""
    return ENDPOINTS[ENVIRONMENT][key]


def get_chain_id() -> int:
    """Return the EIP-712 chain ID for the current environment."""
    return ENDPOINTS[ENVIRONMENT]["chain_id"]


# ─────────────────────────────────────────────
# 📊  Trading Pair
# ─────────────────────────────────────────────
SYMBOL = "vBTC_vUSDC"          # SoDEX spot pair
KLINE_INTERVAL = "1m"          # Faster candle interval for SMA calculation

# ─────────────────────────────────────────────
# 📈  Strategy Parameters
# ─────────────────────────────────────────────
SMA_SHORT_PERIOD = 5           # Fast moving average window
SMA_LONG_PERIOD = 20           # Slow moving average window
RSI_PERIOD = 14                # RSI window
RSI_OVERBOUGHT = 70            # Stay out if above
RSI_OVERSOLD = 30              # Stay out if below

STOP_LOSS_PCT = 0.02           # 2% stop-loss threshold
TAKE_PROFIT_PCT = 0.05         # 5% take-profit threshold

# ─────────────────────────────────────────────
# 💰  Trade Execution & Risk Mgmt
# ─────────────────────────────────────────────
ORDER_TYPE = "MARKET"          # "MARKET" or "LIMIT"
MAX_TRADE_PORTFOLIO_PCT = 0.20 # Enter with max 20% of account balance

# Order Execution Enhancements
PRICE_MISMATCH_THRESHOLD_PCT = 0.005 # Max 0.5% allowed divergence between Binance and SoDEX
ORDER_POLL_TIMEOUT_SEC = 2.0   # Max time to wait for order status to resolve
ORDER_POLL_DELAY_SEC = 0.1     # Delay between order status checks

# ─────────────────────────────────────────────
# 🔥  Aggressive Mode (High-Frequency Scalping)
# ─────────────────────────────────────────────
AGGRESSIVE_MODE = False

AGG_SMA_SHORT = 2
AGG_SMA_LONG = 5
AGG_RSI_OVERBOUGHT = 80
AGG_RSI_OVERSOLD = 20
AGG_STOP_LOSS_PCT = 0.001      # 0.1% Stop Loss for immediate flips
AGG_TAKE_PROFIT_PCT = 0.001    # 0.1% Take Profit for micro-wins
AGG_POLL_INTERVAL_SEC = 1      # Ultra fast 1s polling

AGG_MOMENTUM_THRESHOLD_PCT = 0.00005 # 0.005% baseline minimum jump
AGG_PRICE_CHANGE_MIN_PCT = 0.0001    # 0.01% volatility absolute 
AGG_COOLDOWN_SEC = 1                 # 1s cooldown (almost basically instant)

AGG_SIZING_STRONG_PCT = 0.50   # % of portfolio if conviction matches
AGG_SIZING_WEAK_PCT = 0.20     # % of portfolio on bare signals

MAX_PRICE_DEVIATION_PCT = 0.002 # 0.2% variance allowed between WS Oracle and DEX

# ─────────────────────────────────────────────
# 🔄  Bot Loop
# ─────────────────────────────────────────────
POLL_INTERVAL_SEC = 5          # Faster polling for snappy auto-trading
MAX_RETRIES = 3                # API retry attempts on failure
RETRY_DELAY_SEC = 2            # Base delay for exponential backoff

# ─────────────────────────────────────────────
# 🧪  Trading Mode
# ─────────────────────────────────────────────
#   "PAPER"  →  Simulated trades (no real funds)
#   "LIVE"   →  Real trades via signed API calls
TRADING_MODE = os.getenv("TRADING_MODE", "PAPER").upper()

# Paper trading starting balance (in USDC)
PAPER_BALANCE_USDC = 10_000.0
PAPER_BALANCE_BTC = 0.0

# ─────────────────────────────────────────────
# 📝  Logging
# ─────────────────────────────────────────────
LOG_FILE = "trades.log"
LOG_FORMAT = "csv"             # "csv" or "json"
