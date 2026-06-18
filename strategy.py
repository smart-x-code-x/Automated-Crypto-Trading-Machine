"""
╔══════════════════════════════════════════════════════════════╗
║           SoDEX Trading Bot — Strategy Engine               ║
╚══════════════════════════════════════════════════════════════╝

Advanced Strategy:
  • SMA Crossover + RSI + Momentum + Risk Management.
"""

from typing import List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import pandas as pd
import numpy as np

import config


class Signal(Enum):
    """Trading signal types."""
    BUY = "BUY"
    SELL = "SELL"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    HOLD = "HOLD"


@dataclass
class PositionState:
    """Track the current position state."""
    in_position: bool = False
    entry_price: float = 0.0
    quantity: float = 0.0
    highest_since_entry: float = 0.0

    def open(self, price: float, qty: float):
        """Open a new position."""
        self.in_position = True
        self.entry_price = price
        self.quantity = qty
        self.highest_since_entry = price

    def close(self):
        """Close the current position."""
        self.in_position = False
        self.entry_price = 0.0
        self.quantity = 0.0
        self.highest_since_entry = 0.0

    def update_high(self, price: float):
        """Track the highest price since entry."""
        if price > self.highest_since_entry:
            self.highest_since_entry = price

    @property
    def unrealized_pnl_pct(self) -> float:
        """Calculate unrealized P&L percentage."""
        if not self.in_position or self.entry_price == 0:
            return 0.0
        return (self.highest_since_entry - self.entry_price) / self.entry_price


@dataclass
class StrategyState:
    """Full strategy state."""
    position: PositionState = field(default_factory=PositionState)
    prev_sma_short: Optional[float] = None
    prev_sma_long: Optional[float] = None


def extract_close_prices(klines: list, current_price: Optional[float] = None) -> pd.DataFrame:
    """
    Extract closing prices from SoDEX kline data and return a DataFrame.
    """
    data = []
    for k in klines:
        close = k.get("close") or k.get("closePrice") or k.get("c")
        timestamp = k.get("t") or k.get("startTime")
        if close is not None:
            try:
                data.append({"timestamp": timestamp, "close": float(close)})
            except (ValueError, TypeError):
                continue
    df = pd.DataFrame(data)
    if not df.empty:
        # Sort by timestamp ascending (oldest first)
        df = df.sort_values(by="timestamp").reset_index(drop=True)
        # Inject live sub-minute tick into array for hyper-frequency tracking
        if current_price is not None:
            import time
            df.loc[len(df)] = {"timestamp": int(time.time()*1000), "close": current_price}
    return df


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate SMA, RSI, and Momentum using pandas."""
    if df.empty:
        return df

    sma_short_pd = config.AGG_SMA_SHORT if config.AGGRESSIVE_MODE else config.SMA_SHORT_PERIOD
    sma_long_pd = config.AGG_SMA_LONG if config.AGGRESSIVE_MODE else config.SMA_LONG_PERIOD

    # SMA
    df['sma_short'] = df['close'].rolling(window=sma_short_pd).mean()
    df['sma_long'] = df['close'].rolling(window=sma_long_pd).mean()

    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=config.RSI_PERIOD).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=config.RSI_PERIOD).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    # Replace inf and nan with 50 (neutral) for safety
    df['rsi'] = df['rsi'].replace([np.inf, -np.inf], 100).fillna(50)

    # Momentum (Price Rate of Change)
    # Instead of categorical UP/DOWN, calculate exact percentage jump over 2 periods
    df['momentum'] = df['close'].pct_change(periods=2)

    return df


def evaluate_signal(
    klines: list,
    current_price: float,
    state: StrategyState,
) -> Tuple[Signal, dict]:
    """
    Evaluate the Advanced Strategy and return a trading signal.

    Logic:
      1. STOP LOSS: triggers if unrealized PNL drops below limit.
      2. TAKE PROFIT: triggers if unrealized PNL exceeds limit.
      3. BUY: SMA short > SMA long AND Momentum is UP AND RSI < OVERBOUGHT.
      4. SELL: SMA short < SMA long AND Momentum is DOWN AND RSI > OVERSOLD.

    Returns:
        Tuple: (Signal Enum, Strategy Metrics Dict)
    """
    df = extract_close_prices(klines, current_price)
    
    sma_long_pd = config.AGG_SMA_LONG if config.AGGRESSIVE_MODE else config.SMA_LONG_PERIOD

    if df.empty or len(df) < sma_long_pd:
        return Signal.HOLD, get_empty_metrics(df.shape[0])

    df = calculate_indicators(df)
    latest = df.iloc[-1]

    sma_short = latest['sma_short']
    sma_long = latest['sma_long']
    rsi = latest['rsi']
    momentum = latest['momentum']

    # Configuration mappings based on mode
    rsi_overbought = config.AGG_RSI_OVERBOUGHT if config.AGGRESSIVE_MODE else config.RSI_OVERBOUGHT
    rsi_oversold = config.AGG_RSI_OVERSOLD if config.AGGRESSIVE_MODE else config.RSI_OVERSOLD
    tp_pct = config.AGG_TAKE_PROFIT_PCT if config.AGGRESSIVE_MODE else config.TAKE_PROFIT_PCT
    sl_pct = config.AGG_STOP_LOSS_PCT if config.AGGRESSIVE_MODE else config.STOP_LOSS_PCT

    # Default to neutral metrics
    metrics = {
        "sma_short": round(sma_short, 2) if not pd.isna(sma_short) else None,
        "sma_long": round(sma_long, 2) if not pd.isna(sma_long) else None,
        "rsi": round(rsi, 2) if not pd.isna(rsi) else None,
        "momentum": momentum,
        "trend": "BULLISH ▲" if sma_short > sma_long else "BEARISH ▼",
        "in_position": state.position.in_position,
        "entry_price": state.position.entry_price if state.position.in_position else None,
        "unrealized_pnl_pct": 0.0,
        "signal_strength": "WEAK",
        "data_points": len(df),
        "min_required": sma_long_pd,
        "chart_data": df.tail(100).to_dict(orient="records")  # For plotting 
    }

    signal = Signal.HOLD

    # ── Numeric Volatility & Momentum Filter (Aggressive Scalping)
    if config.AGGRESSIVE_MODE:
        # Relax volatility requirements entirely allowing bare-minimum fractions to pass
        if abs(momentum) < 0.0000001:
            return Signal.HOLD, metrics


    # ── Risk Management: Stop-Loss & Take-Profit ──────────────
    if state.position.in_position:
        state.position.update_high(current_price)
        pnl_pct = (current_price - state.position.entry_price) / state.position.entry_price
        metrics["unrealized_pnl_pct"] = pnl_pct

        if pnl_pct <= -sl_pct:
            return Signal.STOP_LOSS, metrics
        
        if pnl_pct >= tp_pct:
            return Signal.TAKE_PROFIT, metrics

    # ── Entry/Exit Signals ──────────────────────────────────
    if not pd.isna(sma_short) and not pd.isna(sma_long):
        if config.AGGRESSIVE_MODE:
            # High-Frequency scalping: Bypass everything except sub-minute directionality
            if not state.position.in_position:
                if sma_short >= sma_long and rsi < rsi_overbought and momentum > 0:
                    signal = Signal.BUY
                    if rsi < 40:
                        metrics["signal_strength"] = "STRONG"

            elif state.position.in_position:
                if sma_short < sma_long and rsi > rsi_oversold and momentum < 0:
                    signal = Signal.SELL
                    if rsi > 70:
                        metrics["signal_strength"] = "STRONG"
        else:
            # Standard Mode: Wait for absolute Golden/Death cross events
            if state.prev_sma_short is not None and state.prev_sma_long is not None:
                # Golden Cross
                prev_below = state.prev_sma_short <= state.prev_sma_long
                curr_above = sma_short > sma_long

                if prev_below and curr_above and not state.position.in_position:
                    if momentum > 0 and rsi < rsi_overbought:
                        signal = Signal.BUY

                # Death Cross
                prev_above = state.prev_sma_short >= state.prev_sma_long
                curr_below = sma_short < sma_long

                if prev_above and curr_below and state.position.in_position:
                    if momentum < 0 and rsi > rsi_oversold:
                        signal = Signal.SELL

    # Save current SMA values for next iteration
    state.prev_sma_short = sma_short
    state.prev_sma_long = sma_long

    return signal, metrics


def get_empty_metrics(data_points: int) -> dict:
    """Return empty metrics when data is insufficient."""
    return {
        "sma_short": None,
        "sma_long": None,
        "rsi": None,
        "momentum": None,
        "trend": "WAIT",
        "in_position": False,
        "entry_price": None,
        "unrealized_pnl_pct": 0.0,
        "data_points": data_points,
        "min_required": config.AGG_SMA_LONG if config.AGGRESSIVE_MODE else config.SMA_LONG_PERIOD,
        "chart_data": []
    }
