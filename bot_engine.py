import threading
import time
import pandas as pd
import logging
import requests

import config
import sodex_api
from ws_client import BinanceWSClient
from strategy import Signal, StrategyState, evaluate_signal
from trader import create_trader
from logger_setup import TradeLogger


class BotEngine:
    """Background engine that polls the SoDEX API and executes trades."""
    
    def __init__(self):
        self.is_running = False
        self.thread = None
        self.lock = threading.Lock()
        
        self.logger = TradeLogger()
        self.strategy_state = StrategyState()
        self.trader = create_trader(self.logger, self.strategy_state)
        self.ws_client = BinanceWSClient()
        
        # Shared state for the Streamlit UI to read from
        self.state = {
            "current_price": 0.0,
            "signal": "HOLD",
            "cycle": 0,
            "metrics": {},
            "balances": self.trader.get_balances(),
            "last_trade_time": 0.0,
            "last_error": None
        }

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.ws_client.start()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.is_running = False
        self.ws_client.stop()
        if self.thread:
            self.thread.join(timeout=2)

    def set_aggressive_mode(self, enabled: bool):
        with self.lock:
            config.AGGRESSIVE_MODE = enabled

    def get_state(self):
        with self.lock:
            return self.state.copy()

    def _run_loop(self):
        consecutive_errors = 0
        max_consecutive_errors = 10
        
        while self.is_running:
            try:
                cycle_data = {
                    "last_error": None
                }
                
                # 1. Fetch current price
                current_price = 0.0
                dex_price = 0.0
                
                # Fetch pure DEX oracle sequentially to track deviations
                ticker = sodex_api.get_ticker(config.SYMBOL)
                if ticker:
                    dex_price = float(ticker.get("lastPx", ticker.get("lastPrice", ticker.get("last", 0))))
                
                if config.AGGRESSIVE_MODE:
                    # WebSocket ultra-fast oracle proxy mapping
                    current_price = self.ws_client.get_price()
                    
                    # Validate deviation against local DEX execution targets securely
                    if current_price > 0 and dex_price > 0:
                        deviation = abs(current_price - dex_price) / dex_price
                        if deviation > config.MAX_PRICE_DEVIATION_PCT:
                            self.logger.log_warning(f"Price deviation ({deviation*100:.2f}%) exceeds limit! WS Oracle: {current_price}, DEX: {dex_price}. Dropping execution cycle completely to protect ledger.")
                            # Yield interval sequence instantly
                            time.sleep(1)
                            continue
                
                if current_price <= 0:
                    current_price = dex_price
                
                if current_price <= 0:
                    raise ValueError(f"Invalid or missing price from oracles.")

                # 2. Fetch trailing klines
                klines = sodex_api.get_klines(
                    symbol=config.SYMBOL,
                    interval=config.KLINE_INTERVAL,
                    limit=max(config.AGG_SMA_LONG, config.SMA_LONG_PERIOD, config.RSI_PERIOD) + 5
                )

                # 3. Evaluate Strategy
                signal, metrics = evaluate_signal(klines, current_price, self.strategy_state)
                
                # 4. Check Cooldowns and Execute Trade
                current_time = time.time()
                recent_trade = (current_time - self.state["last_trade_time"]) < config.AGG_COOLDOWN_SEC

                if signal != Signal.HOLD:
                    # Block active buys/sells if cooling down in aggressive mode
                    if config.AGGRESSIVE_MODE and recent_trade and signal in [Signal.BUY, Signal.SELL]:
                        self.logger.log_info(f"Signal {signal} blocked by {config.AGG_COOLDOWN_SEC}s cooldown limit.")
                    else:
                        trade_record = self.trader.process_signal(signal, current_price, metrics)
                        if trade_record:
                            self.logger.log_info(f"Trade executed: {trade_record.signal} at {trade_record.price}")
                            self.state["last_trade_time"] = current_time
                        
                balances = self.trader.get_balances()

                # Update State safely for UI
                with self.lock:
                    self.state["current_price"] = current_price
                    self.state["signal"] = signal.value
                    self.state["cycle"] += 1
                    self.state["metrics"] = metrics
                    self.state["balances"] = balances
                    self.state["last_error"] = None

                consecutive_errors = 0
                
            except Exception as e:
                consecutive_errors += 1
                self.logger.log_error(f"Engine Loop Error: {e}")
                with self.lock:
                    self.state["last_error"] = str(e)
                
                if consecutive_errors >= max_consecutive_errors:
                    self.is_running = False
                    with self.lock:
                        self.state["last_error"] = "Max consecutive errors reached. Engine stopped."
                    break

            # Sleep dynamically based on active mode configuration
            poll_interval = config.AGG_POLL_INTERVAL_SEC if config.AGGRESSIVE_MODE else config.POLL_INTERVAL_SEC
            
            for _ in range(poll_interval * 10):
                if not self.is_running:
                    break
                time.sleep(0.1)

# Singleton instance
engine = BotEngine()
