"""
╔══════════════════════════════════════════════════════════════╗
║           SoDEX Trading Bot — Trade Executor                ║
╚══════════════════════════════════════════════════════════════╝

Two execution modes:
  • PaperTrader  — Simulates trades with virtual balances
  • LiveTrader   — Real trades via SoDEX API (EIP-712 signed)

Both share the same interface for seamless switching.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

import config
import sodex_api
from strategy import Signal, StrategyState
from logger_setup import TradeLogger


@dataclass
class TradeRecord:
    """Represents a single trade execution."""
    timestamp: str
    signal: str
    side: str
    price: float
    quantity: float
    total_value: float
    pnl: float
    pnl_pct: float
    mode: str
    balance_usdc: float
    balance_btc: float
    status: str  # "FILLED", "SIMULATED", "FAILED"
    order_id: Optional[str] = None
    error: Optional[str] = None


class BaseTrader(ABC):
    """Abstract base for paper and live traders."""

    def __init__(self, logger: TradeLogger, state: StrategyState):
        self.logger = logger
        self.state = state
        self.trade_history: List[TradeRecord] = []

    @abstractmethod
    def execute_buy(self, price: float, quantity: float) -> TradeRecord:
        """Execute a buy order."""
        pass

    @abstractmethod
    def execute_sell(self, price: float, quantity: float, reason: str = "SIGNAL") -> TradeRecord:
        """Execute a sell order."""
        pass

    @abstractmethod
    def get_balances(self) -> Dict[str, float]:
        """Get current balances."""
        pass

    def process_signal(self, signal: Signal, current_price: float, metrics: Optional[Dict] = None) -> Optional[TradeRecord]:
        """
        Process a strategy signal and execute the appropriate trade.
        Returns the trade record if a trade was executed, else None.
        """
        if metrics is None:
            metrics = {}
            
        if signal == Signal.BUY:
            # Dynamic position sizing based on mode
            port_limit = config.MAX_TRADE_PORTFOLIO_PCT
            
            if config.AGGRESSIVE_MODE:
                if metrics.get("signal_strength") == "STRONG":
                    port_limit = config.AGG_SIZING_STRONG_PCT
                else:
                    port_limit = config.AGG_SIZING_WEAK_PCT
            
            balances = self.get_balances()
            usdc = balances.get("USDC", 0.0)
            
            # Simple fallback if live balance fetching fails
            if usdc <= 0 and config.TRADING_MODE != "PAPER":
                usdc = 1000.0  # Safe mock limit
                
            risk_usd = usdc * port_limit
            quantity = risk_usd / current_price

            # Round quantity to something sensible (e.g., 6 decimals)
            quantity = round(quantity, 6)
            
            if quantity <= 0:
                return None

            record = self.execute_buy(current_price, quantity)
            if record and record.status in ["FILLED", "SUCCESS"]:
                self.state.position.open(current_price, quantity)
            return record

        elif signal == Signal.SELL:
            if self.state.position.in_position:
                qty = self.state.position.quantity
                record = self.execute_sell(current_price, qty, reason="SIGNAL_CROSSOVER")
                if record and record.status in ["FILLED", "SUCCESS"]:
                    self.state.position.close()
                return record

        elif signal == Signal.STOP_LOSS:
            if self.state.position.in_position:
                qty = self.state.position.quantity
                record = self.execute_sell(current_price, qty, reason="STOP_LOSS")
                if record and record.status in ["FILLED", "SUCCESS"]:
                    self.state.position.close()
                return record
                
        elif signal == Signal.TAKE_PROFIT:
            if self.state.position.in_position:
                qty = self.state.position.quantity
                record = self.execute_sell(current_price, qty, reason="TAKE_PROFIT")
                if record and record.status in ["FILLED", "SUCCESS"]:
                    self.state.position.close()
                return record

        return None


# ══════════════════════════════════════════════════════════════
#  PAPER TRADER (Simulation Mode)
# ══════════════════════════════════════════════════════════════

class PaperTrader(BaseTrader):
    """
    Simulates trades with virtual balances.
    No real API calls are made — perfect for strategy testing.
    """

    def __init__(self, logger: TradeLogger, state: StrategyState):
        super().__init__(logger, state)
        self.usdc_balance = config.PAPER_BALANCE_USDC
        self.btc_balance = config.PAPER_BALANCE_BTC
        self.total_trades = 0
        self.winning_trades = 0
        self.total_pnl = 0.0

    def execute_buy(self, price: float, quantity: float) -> TradeRecord:
        """Simulate a buy order."""
        total_cost = price * quantity

        # Check if we have enough USDC
        if self.usdc_balance < total_cost:
            record = TradeRecord(
                timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                signal="BUY",
                side="BUY",
                price=price,
                quantity=quantity,
                total_value=total_cost,
                pnl=0.0,
                pnl_pct=0.0,
                mode="PAPER",
                balance_usdc=self.usdc_balance,
                balance_btc=self.btc_balance,
                status="FAILED",
                error="Insufficient USDC balance",
            )
            self.logger.log_trade(record)
            return record

        # Execute simulated buy
        self.usdc_balance -= total_cost
        self.btc_balance += quantity

        record = TradeRecord(
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            signal="BUY",
            side="BUY",
            price=price,
            quantity=quantity,
            total_value=total_cost,
            pnl=0.0,
            pnl_pct=0.0,
            mode="PAPER",
            balance_usdc=round(self.usdc_balance, 2),
            balance_btc=round(self.btc_balance, 8),
            status="SIMULATED",
            order_id=f"PAPER-{int(time.time()*1000)}",
        )

        self.total_trades += 1
        self.logger.log_trade(record)
        self.trade_history.append(record)
        return record

    def execute_sell(self, price: float, quantity: float, reason: str = "SIGNAL") -> TradeRecord:
        """Simulate a sell order."""
        # Check if we have enough BTC
        if self.btc_balance < quantity:
            quantity = self.btc_balance  # Sell what we have

        if quantity <= 0:
            record = TradeRecord(
                timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                signal=f"SELL ({reason})",
                side="SELL",
                price=price,
                quantity=0,
                total_value=0,
                pnl=0.0,
                pnl_pct=0.0,
                mode="PAPER",
                balance_usdc=self.usdc_balance,
                balance_btc=self.btc_balance,
                status="FAILED",
                error="No BTC to sell",
            )
            self.logger.log_trade(record)
            return record

        total_value = price * quantity

        # Calculate P&L
        entry_price = self.state.position.entry_price
        pnl = (price - entry_price) * quantity if entry_price > 0 else 0.0
        pnl_pct = ((price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0

        # Execute simulated sell
        self.usdc_balance += total_value
        self.btc_balance -= quantity
        self.total_pnl += pnl

        if pnl > 0:
            self.winning_trades += 1

        record = TradeRecord(
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            signal=f"SELL ({reason})",
            side="SELL",
            price=price,
            quantity=quantity,
            total_value=total_value,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 4),
            mode="PAPER",
            balance_usdc=round(self.usdc_balance, 2),
            balance_btc=round(self.btc_balance, 8),
            status="SIMULATED",
            order_id=f"PAPER-{int(time.time()*1000)}",
        )

        self.total_trades += 1
        self.logger.log_trade(record)
        self.trade_history.append(record)
        return record

    def get_balances(self) -> Dict[str, float]:
        return {
            "USDC": round(self.usdc_balance, 2),
            "BTC": round(self.btc_balance, 8),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "total_pnl": round(self.total_pnl, 2),
            "win_rate": round(self.winning_trades / max(1, self.total_trades) * 100, 1),
        }


# ══════════════════════════════════════════════════════════════
#  LIVE TRADER (Real Trading via SoDEX API)
# ══════════════════════════════════════════════════════════════

class LiveTrader(BaseTrader):
    """
    Executes real trades on SoDEX via signed API calls.
    Requires: SODEX_API_KEY, SODEX_PRIVATE_KEY, SODEX_ACCOUNT_ID
    """

    def __init__(self, logger: TradeLogger, state: StrategyState):
        super().__init__(logger, state)
        if not config.API_KEY or not config.PRIVATE_KEY:
            raise ValueError(
                "⛔ LiveTrader requires SODEX_API_KEY and SODEX_PRIVATE_KEY.\n"
                "Set them in .env or environment variables."
            )
        self.total_trades = 0
        self.winning_trades = 0
        self.total_pnl = 0.0

    def execute_buy(self, price: float, quantity: float) -> TradeRecord:
        """Execute a real buy order on SoDEX."""
        for attempt in range(2):
            try:
                result = sodex_api.place_order(
                    symbol=config.SYMBOL,
                    side="BUY",
                    order_type=config.ORDER_TYPE,
                    quantity=str(quantity),
                )

                order_data = result.get("data", [{}])
                order_id = str(order_data[0].get("orderID", "")) if order_data else ""
                
                # Fetch actual SoDEX executed status securely
                parsed_status = str(order_data[0].get("orderStatus", "FILLED")).upper() if order_data else "FILLED"
        return self._place_and_confirm("BUY", price, quantity)

    def execute_sell(self, price: float, quantity: float, reason: str = "SIGNAL") -> TradeRecord:
        """Execute a real sell order on SoDEX."""
                    side="SELL",
                    price=price,
                    quantity=quantity,
                    total_value=price * quantity,
                    pnl=0.0,
                    pnl_pct=0.0,
                    mode="LIVE",
                    balance_usdc=0,
                    balance_btc=0,
                    status="FAILED",
                    error=f"Live Execution Exception: {str(e)}",
                )
                self.logger.log_trade(record)
                return record

    def get_balances(self) -> Dict[str, float]:
        """Fetch real balances from SoDEX."""
        try:
            data = sodex_api.get_balances()
            # Parse balance data from API response
            balances = data.get("balances", [])
            usdc = 0.0
            btc = 0.0
            for b in balances:
                coin = b.get("coin", "").upper()
                if "USDC" in coin:
                    usdc = float(b.get("available", 0))
                elif "BTC" in coin:
                    btc = float(b.get("available", 0))
            return {
                "USDC": round(usdc, 2),
                "BTC": round(btc, 8),
                "total_trades": self.total_trades,
                "winning_trades": self.winning_trades,
                "total_pnl": round(self.total_pnl, 2),
                "win_rate": round(self.winning_trades / max(1, self.total_trades) * 100, 1),
            }
        except Exception:
            return {
                "USDC": 0.0,
                "BTC": 0.0,
                "total_trades": self.total_trades,
                "winning_trades": self.winning_trades,
                "total_pnl": round(self.total_pnl, 2),
                "win_rate": 0.0,
            }


def create_trader(logger: TradeLogger, state: StrategyState) -> BaseTrader:
    """
    Factory function to create the appropriate trader
    based on the configured TRADING_MODE.
    """
    if config.TRADING_MODE == "LIVE":
        return LiveTrader(logger, state)
    return PaperTrader(logger, state)
