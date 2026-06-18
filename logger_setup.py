import sys
import os

# Force UTF-8 output on Windows
if sys.platform == "win32":
    os.system("chcp 65001 > nul 2>&1")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

"""
SoDEX Trading Bot - Trade Logger

Logs all trade activity to:
  - CSV file (trades.log) for analysis
  - Console with Rich formatting for real-time monitoring
"""

import csv
import time
import logging
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.layout import Layout
from rich.live import Live
from rich import box

import config

if TYPE_CHECKING:
    from trader import TradeRecord


# ----------------------------------------------------------------
#  File Logger (CSV)
# ----------------------------------------------------------------

CSV_HEADERS = [
    "timestamp",
    "signal",
    "side",
    "price",
    "quantity",
    "total_value",
    "pnl",
    "pnl_pct",
    "mode",
    "balance_usdc",
    "balance_btc",
    "status",
    "order_id",
    "error",
]


class TradeLogger:
    """Handles trade logging to CSV file and console output."""

    def __init__(self, log_file: str = config.LOG_FILE):
        self.log_file = log_file
        self.console = Console(force_terminal=True)
        self._init_csv()

        # Python standard logging for errors/debug
        self._logger = logging.getLogger("sodex_bot")
        self._logger.setLevel(logging.DEBUG)

        # File handler for error logs
        fh = logging.FileHandler("bot_errors.log", encoding="utf-8")
        fh.setLevel(logging.WARNING)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s"
        ))
        self._logger.addHandler(fh)

    def _init_csv(self):
        """Initialize CSV file with headers if it doesn't exist."""
        if not os.path.exists(self.log_file):
            with open(self.log_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(CSV_HEADERS)

    def log_trade(self, trade: "TradeRecord"):
        """Log a trade to both CSV and console."""
        self._write_csv(trade)
        self._print_trade(trade)

    def _write_csv(self, trade: "TradeRecord"):
        """Append trade record to the CSV log file."""
        try:
            with open(self.log_file, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    trade.timestamp,
                    trade.signal,
                    trade.side,
                    trade.price,
                    trade.quantity,
                    trade.total_value,
                    trade.pnl,
                    trade.pnl_pct,
                    trade.mode,
                    trade.balance_usdc,
                    trade.balance_btc,
                    trade.status,
                    trade.order_id or "",
                    trade.error or "",
                ])
        except Exception as e:
            self._logger.error(f"Failed to write CSV: {e}")

    def _print_trade(self, trade: "TradeRecord"):
        """Print a beautiful trade notification to the console."""
        # Color scheme
        if trade.status == "FAILED":
            color = "red"
            icon = "[X]"
        elif trade.side == "BUY":
            color = "green"
            icon = "[BUY]"
        else:
            color = "red" if trade.pnl < 0 else "cyan"
            icon = "[STOP]" if "STOP_LOSS" in trade.signal else "[SELL]"

        pnl_str = ""
        if trade.side == "SELL":
            pnl_color = "green" if trade.pnl >= 0 else "red"
            pnl_str = f"  |  P&L: [{pnl_color}]${trade.pnl:+.2f} ({trade.pnl_pct:+.2f}%)[/{pnl_color}]"

        self.console.print()
        self.console.print(Panel(
            f"[bold]{icon} {trade.signal}[/bold]\n"
            f"  |  Price: [yellow]${trade.price:,.2f}[/yellow]\n"
            f"  |  Qty:   [white]{trade.quantity:.6f} BTC[/white]\n"
            f"  |  Value: [white]${trade.total_value:,.2f}[/white]\n"
            f"{pnl_str}\n"
            f"  |  Mode:  [dim]{trade.mode}[/dim]  |  Status: [{color}]{trade.status}[/{color}]\n"
            f"  |  USDC:  [yellow]${trade.balance_usdc:,.2f}[/yellow]  |  BTC: [yellow]{trade.balance_btc:.8f}[/yellow]"
            + (f"\n  |  [red]Error: {trade.error}[/red]" if trade.error else ""),
            title=f"[bold {color}]=== TRADE EXECUTED ===[/bold {color}]",
            border_style=color,
            padding=(0, 2),
        ))

    def log_error(self, message: str, exc: Exception = None):
        """Log an error message."""
        self._logger.error(message, exc_info=exc)
        self.console.print(f"  [red]! ERROR: {message}[/red]")

    def log_info(self, message: str):
        """Log an info message."""
        self._logger.info(message)

    def log_warning(self, message: str):
        """Log a warning message."""
        self._logger.warning(message)
        self.console.print(f"  [yellow]! {message}[/yellow]")


# ----------------------------------------------------------------
#  Dashboard Display
# ----------------------------------------------------------------

def render_dashboard(
    current_price: float,
    strategy_info: dict,
    balances: dict,
    signal: str,
    cycle_count: int,
    mode: str,
) -> Panel:
    """
    Render a beautiful dashboard panel for the console.
    """
    # Header
    header = Text()
    header.append("  >> SoDEX Trading Bot ", style="bold white on blue")
    header.append(f"  [{mode}]  ", style=f"bold white on {'green' if mode == 'PAPER' else 'red'}")
    header.append(f"  Cycle #{cycle_count}", style="dim")

    # Price section
    price_text = Text()
    price_text.append(f"\n  [*] BTC Price: ", style="dim")
    price_text.append(f"${current_price:,.2f}", style="bold yellow")

    # SMA values
    sma_text = Text()
    sma_short = strategy_info.get("sma_short")
    sma_long = strategy_info.get("sma_long")

    sma_text.append(f"\n  [~] SMA({strategy_info['sma_short_period']}): ", style="dim")
    sma_text.append(f"${sma_short:,.2f}" if sma_short else "Calculating...",
                     style="cyan" if sma_short else "dim")

    sma_text.append(f"  |  SMA({strategy_info['sma_long_period']}): ", style="dim")
    sma_text.append(f"${sma_long:,.2f}" if sma_long else "Calculating...",
                     style="magenta" if sma_long else "dim")

    # Trend indicator
    trend_text = Text()
    if sma_short and sma_long:
        if sma_short > sma_long:
            trend_text.append(f"\n  [^] Trend: ", style="dim")
            trend_text.append("BULLISH ^", style="bold green")
        else:
            trend_text.append(f"\n  [v] Trend: ", style="dim")
            trend_text.append("BEARISH v", style="bold red")
    else:
        trend_text.append(f"\n  [...] Trend: ", style="dim")
        trend_text.append(f"Collecting data ({strategy_info['data_points']}/{strategy_info['min_required']})",
                          style="yellow")

    # Signal
    signal_text = Text()
    signal_text.append(f"\n  [>] Signal: ", style="dim")
    signal_colors = {"BUY": "bold green", "SELL": "bold red", "STOP_LOSS": "bold red", "HOLD": "dim"}
    signal_text.append(signal, style=signal_colors.get(signal, "dim"))

    # Position status
    pos_text = Text()
    if strategy_info.get("in_position"):
        pos_text.append(f"\n  [P] Position: ", style="dim")
        pos_text.append(f"LONG @ ${strategy_info['entry_price']:,.2f}", style="bold cyan")
        pos_text.append(f"  |  Stop-loss: {strategy_info['stop_loss_pct']:.0f}%", style="dim red")
    else:
        pos_text.append(f"\n  [P] Position: ", style="dim")
        pos_text.append("FLAT (no position)", style="dim")

    # Balances
    bal_text = Text()
    bal_text.append(f"\n  [$] USDC: ", style="dim")
    bal_text.append(f"${balances.get('USDC', 0):,.2f}", style="bold yellow")
    bal_text.append(f"  |  BTC: ", style="dim")
    bal_text.append(f"{balances.get('BTC', 0):.8f}", style="bold yellow")

    # Stats
    stats_text = Text()
    stats_text.append(f"\n  [#] Trades: {balances.get('total_trades', 0)}", style="dim")
    stats_text.append(f"  |  Wins: {balances.get('winning_trades', 0)}", style="dim")
    stats_text.append(f"  |  Win Rate: {balances.get('win_rate', 0):.1f}%", style="dim")

    total_pnl = balances.get('total_pnl', 0)
    pnl_color = "green" if total_pnl >= 0 else "red"
    stats_text.append(f"  |  Total P&L: ", style="dim")
    stats_text.append(f"${total_pnl:+,.2f}", style=f"bold {pnl_color}")

    # Assemble
    content = Text()
    content.append_text(header)
    content.append_text(price_text)
    content.append_text(sma_text)
    content.append_text(trend_text)
    content.append_text(signal_text)
    content.append_text(pos_text)
    content.append_text(bal_text)
    content.append_text(stats_text)
    content.append("\n")

    return Panel(
        content,
        title="[bold cyan]=== SoDEX SMA Crossover Bot ===[/bold cyan]",
        subtitle=f"[dim]Pair: {config.SYMBOL} | Interval: {config.KLINE_INTERVAL} | Poll: {config.POLL_INTERVAL_SEC}s[/dim]",
        border_style="cyan",
        padding=(0, 1),
    )
