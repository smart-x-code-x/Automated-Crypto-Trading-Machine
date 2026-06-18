import sys
import os

# Force UTF-8 output on Windows to prevent encoding errors
if sys.platform == "win32":
    os.system("chcp 65001 > nul 2>&1")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

"""
SoDEX Trading Bot - Main Bot Loop
SMA Crossover Trading Bot | Powered by SoDEX DEX API

 Usage:
   python bot.py              # Run in paper trading mode (default)
   python bot.py --live       # Run in live trading mode (requires API keys)
   python bot.py --testnet    # Use testnet endpoints
"""

import sys  # noqa: E402 (already imported above for UTF-8 fix)
import time
import signal as sys_signal
import argparse
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.progress import SpinnerColumn, TextColumn, Progress, BarColumn

import config
import sodex_api
from strategy import Signal, StrategyState, evaluate_signal, extract_close_prices, get_strategy_summary
from trader import create_trader, TradeRecord
from logger_setup import TradeLogger, render_dashboard


# ──────────────────────────────────────────────────────────────
#  Globals
# ──────────────────────────────────────────────────────────────
console = Console(force_terminal=True)
running = True


def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully."""
    global running
    running = False
    console.print("\n\n  [yellow]⚠ Shutdown signal received. Exiting gracefully...[/yellow]\n")


sys_signal.signal(sys_signal.SIGINT, signal_handler)
sys_signal.signal(sys_signal.SIGTERM, signal_handler)


# ──────────────────────────────────────────────────────────────
#  ASCII Banner
# ──────────────────────────────────────────────────────────────

BANNER = """
[bold cyan]
    +==============================================================+
    |                                                              |
    |     ____   ___  ____  _______  __                            |
    |    / ___| / _ \\|  _ \\| ____\\ \\/ /                            |
    |    \\___ \\| | | | | | |  _|  \\  /                             |
    |     ___) | |_| | |_| | |___ /  \\                             |
    |    |____/ \\___/|____/|_____/_/\\_\\                            |
    |                                                              |
    |         [yellow]SMA Crossover Trading Bot[/yellow]                          |
    |              Powered by SoDEX DEX API                        |
    |                                                              |
    +==============================================================+
[/bold cyan]
"""


def print_startup_info():
    """Print bot configuration summary at startup."""
    console.print(BANNER)
    console.print(Panel(
        f"  [bold]Configuration[/bold]\n"
        f"  +-- Symbol:       [cyan]{config.SYMBOL}[/cyan]\n"
        f"  +-- SMA Short:    [cyan]{config.SMA_SHORT_PERIOD}[/cyan] periods\n"
        f"  +-- SMA Long:     [cyan]{config.SMA_LONG_PERIOD}[/cyan] periods\n"
        f"  +-- Stop-Loss:    [red]{config.STOP_LOSS_PCT * 100:.0f}%[/red]\n"
        f"  +-- Trade Qty:    [yellow]{config.TRADE_QUANTITY} BTC[/yellow]\n"
        f"  +-- Order Type:   [white]{config.ORDER_TYPE}[/white]\n"
        f"  +-- Interval:     [white]{config.KLINE_INTERVAL}[/white]\n"
        f"  +-- Poll Rate:    [white]{config.POLL_INTERVAL_SEC}s[/white]\n"
        f"  +-- Environment:  [{'green' if config.ENVIRONMENT == 'testnet' else 'yellow'}]"
        f"{config.ENVIRONMENT.upper()}[/{'green' if config.ENVIRONMENT == 'testnet' else 'yellow'}]\n"
        f"  +-- Mode:         [{'green' if config.TRADING_MODE == 'PAPER' else 'bold red'}]"
        f"{config.TRADING_MODE}[/{'green' if config.TRADING_MODE == 'PAPER' else 'bold red'}]\n"
        f"  +-- Log File:     [dim]{config.LOG_FILE}[/dim]",
        title="[bold blue]=== Bot Settings ===[/bold blue]",
        border_style="blue",
        padding=(0, 1),
    ))
    console.print()


# ──────────────────────────────────────────────────────────────
#  Main Bot Loop
# ──────────────────────────────────────────────────────────────

def run_bot():
    """Main bot execution loop."""
    global running

    # Parse CLI arguments
    parser = argparse.ArgumentParser(description="SoDEX SMA Crossover Trading Bot")
    parser.add_argument("--live", action="store_true", help="Enable live trading mode")
    parser.add_argument("--testnet", action="store_true", help="Use testnet endpoints")
    parser.add_argument("--interval", type=int, default=config.POLL_INTERVAL_SEC,
                        help="Poll interval in seconds")
    args = parser.parse_args()

    # Apply CLI overrides
    if args.live:
        config.TRADING_MODE = "LIVE"
    if args.testnet:
        config.ENVIRONMENT = "testnet"
    if args.interval:
        config.POLL_INTERVAL_SEC = args.interval

    # Initialize components
    print_startup_info()

    logger = TradeLogger()
    state = StrategyState()
    trader = create_trader(logger, state)

    console.print(f"  [green][OK] Trader initialized: {type(trader).__name__}[/green]")
    console.print(f"  [green][OK] Logger initialized: {config.LOG_FILE}[/green]")
    console.print()

    # ── Pre-flight check ─────────────────────────────────────
    console.print("  [cyan][..] Testing SoDEX API connection...[/cyan]")
    try:
        ticker = sodex_api.get_ticker(config.SYMBOL)
        if ticker:
            price = float(ticker.get("lastPx", ticker.get("lastPrice", ticker.get("last", 0))))
            console.print(f"  [green][OK] Connected! {config.SYMBOL} = ${price:,.2f}[/green]")
        else:
            console.print(f"  [yellow][!] Ticker returned empty. API may be limited.[/yellow]")
            console.print(f"  [yellow]    Bot will continue and retry on each cycle.[/yellow]")
    except Exception as e:
        console.print(f"  [yellow][!] API pre-flight check failed: {e}[/yellow]")
        console.print(f"  [yellow]    Bot will continue and retry on each cycle.[/yellow]")
        logger.log_error(f"Pre-flight API check failed: {e}")

    console.print()
    console.print("  [bold green]>> Bot started! Press Ctrl+C to stop.[/bold green]")
    console.print("  " + "-" * 56)
    console.print()

    # ── Main loop ────────────────────────────────────────────
    cycle = 0
    consecutive_errors = 0
    max_consecutive_errors = 10

    while running:
        cycle += 1
        try:
            # [1] Fetch current ticker price
            ticker = sodex_api.get_ticker(config.SYMBOL)
            if not ticker:
                logger.log_warning(f"Cycle {cycle}: Empty ticker response. Retrying...")
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    console.print(f"  [red][X] {max_consecutive_errors} consecutive errors. Stopping bot.[/red]")
                    break
                time.sleep(config.POLL_INTERVAL_SEC)
                continue

            current_price = float(ticker.get("lastPx", ticker.get("lastPrice", ticker.get("last", 0))))
            if current_price <= 0:
                logger.log_warning(f"Cycle {cycle}: Invalid price ({current_price}). Skipping...")
                time.sleep(config.POLL_INTERVAL_SEC)
                continue

            # [2] Fetch kline data for SMA calculation
            klines = sodex_api.get_klines(
                symbol=config.SYMBOL,
                interval=config.KLINE_INTERVAL,
                limit=config.SMA_LONG_PERIOD + 5,  # Extra buffer
            )
            close_prices = extract_close_prices(klines)

            # [3] Evaluate strategy signal
            signal = evaluate_signal(close_prices, current_price, state)

            # [4] Get strategy summary for dashboard
            strategy_info = get_strategy_summary(close_prices, state)
            balances = trader.get_balances()

            # [5] Render dashboard
            dashboard = render_dashboard(
                current_price=current_price,
                strategy_info=strategy_info,
                balances=balances,
                signal=signal.value,
                cycle_count=cycle,
                mode=config.TRADING_MODE,
            )
            console.print(dashboard)

            # [6] Execute trade if signal is actionable
            if signal != Signal.HOLD:
                trade = trader.process_signal(signal, current_price)
                if trade:
                    console.print(
                        f"  [bold][LOG] Trade logged to {config.LOG_FILE}[/bold]"
                    )

            # Reset error counter on success
            consecutive_errors = 0

        except sodex_api.SoDEXAPIError as e:
            consecutive_errors += 1
            logger.log_error(f"Cycle {cycle}: SoDEX API Error: {e}", e)
            if consecutive_errors >= max_consecutive_errors:
                console.print(f"\n  [red][X] {max_consecutive_errors} consecutive errors. Stopping bot.[/red]")
                break

        except Exception as e:
            consecutive_errors += 1
            logger.log_error(f"Cycle {cycle}: Unexpected error: {e}", e)
            if consecutive_errors >= max_consecutive_errors:
                console.print(f"\n  [red][X] {max_consecutive_errors} consecutive errors. Stopping bot.[/red]")
                break

        # [7] Wait for next cycle
        if running:
            with Progress(
                SpinnerColumn(style="cyan"),
                TextColumn("[dim]Next cycle in...[/dim]"),
                BarColumn(bar_width=30, style="cyan", complete_style="green"),
                TextColumn("[dim]{task.completed}/{task.total}s[/dim]"),
                console=console,
                transient=True,
            ) as progress:
                task = progress.add_task("Waiting", total=config.POLL_INTERVAL_SEC)
                for i in range(config.POLL_INTERVAL_SEC):
                    if not running:
                        break
                    time.sleep(1)
                    progress.update(task, advance=1)

    # ── Shutdown ─────────────────────────────────────────────
    console.print()
    console.print(Panel(
        f"  [bold]Final Stats[/bold]\n"
        f"  +-- Total Cycles:  {cycle}\n"
        f"  +-- Total Trades:  {trader.get_balances().get('total_trades', 0)}\n"
        f"  +-- Total P&L:     ${trader.get_balances().get('total_pnl', 0):+,.2f}\n"
        f"  +-- Win Rate:      {trader.get_balances().get('win_rate', 0):.1f}%\n"
        f"  +-- Log File:      {config.LOG_FILE}",
        title="[bold yellow]=== Bot Stopped ===[/bold yellow]",
        border_style="yellow",
        padding=(0, 1),
    ))
    console.print("\n  [dim]Goodbye![/dim]\n")


# ──────────────────────────────────────────────────────────────
#  Entry Point
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_bot()
