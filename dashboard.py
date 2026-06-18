import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
import os

from bot_engine import BotEngine
import config

# Must be the first Streamlit command
st.set_page_config(
    page_title="SoDEX Auto-Trading Bot",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# -------------------------------------------------------------------
#  Singleton Engine Initialization
# -------------------------------------------------------------------
@st.cache_resource
def get_engine():
    """Ensure only one engine instance exists across Streamlit reruns."""
    return BotEngine()

engine = get_engine()

# -------------------------------------------------------------------
#  Dashboard UI
# -------------------------------------------------------------------

st.title("⚡ SoDEX Advanced Auto-Trading Dashboard")

st.markdown("""
<style>
    /* Dark Theme tweaks and metric boxes */
    div[data-testid="stMetricValue"] {
        font-size: 1.8rem;
    }
</style>
""", unsafe_allow_html=True)

# Sidebar Controls
with st.sidebar:
    st.header("⚙️ Controls")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶️ Start Bot", use_container_width=True, disabled=engine.is_running):
            engine.start()
            st.rerun()
    with col2:
        if st.button("⏹️ Stop Bot", use_container_width=True, disabled=not engine.is_running):
            engine.stop()
            st.rerun()
            
    st.markdown("---")
    
    # Aggressive Mode Toggle
    agg_toggle = st.toggle("🔥 Aggressive Mode (High Frequency)", value=config.AGGRESSIVE_MODE)
    if agg_toggle != config.AGGRESSIVE_MODE:
        engine.set_aggressive_mode(agg_toggle)
        st.rerun()
        
    st.markdown("---")
    
    st.write(f"**Mode:** `{config.TRADING_MODE}`")
    st.write(f"**Status:** {'🟢 ACTIVE (Polling)' if engine.is_running else '🔴 STOPPED'}")
    
    state = engine.get_state()
    cycle = state.get("cycle", 0)
    st.write(f"**Cycle:** #{cycle}")
    
    if state.get("last_error"):
        st.error(f"Error: {state['last_error']}")
        
    st.markdown("---")
    st.write("### Strategy Params")
    st.write(f"- Interval: `{config.KLINE_INTERVAL}`")
    
    p_short = config.AGG_SMA_SHORT if config.AGGRESSIVE_MODE else config.SMA_SHORT_PERIOD
    p_long = config.AGG_SMA_LONG if config.AGGRESSIVE_MODE else config.SMA_LONG_PERIOD
    st.write(f"- SMA Fast/Slow: `{p_short}` / `{p_long}`")
    
    p_rsi = config.RSI_PERIOD
    p_rsi_over = config.AGG_RSI_OVERBOUGHT if config.AGGRESSIVE_MODE else config.RSI_OVERBOUGHT
    p_rsi_under = config.AGG_RSI_OVERSOLD if config.AGGRESSIVE_MODE else config.RSI_OVERSOLD
    st.write(f"- RSI: `{p_rsi_under}`-`{p_rsi_over}`")
    
    p_sl = config.AGG_STOP_LOSS_PCT if config.AGGRESSIVE_MODE else config.STOP_LOSS_PCT
    p_to = config.AGG_TAKE_PROFIT_PCT if config.AGGRESSIVE_MODE else config.TAKE_PROFIT_PCT
    st.write(f"- Stop-Loss: `{p_sl*100:.1f}%`")
    st.write(f"- Take-Profit: `{p_to*100:.1f}%`")
    
    if config.AGGRESSIVE_MODE:
        st.write(f"- Max Risk size: `{config.AGG_SIZING_STRONG_PCT*100:.0f}%` / `{config.AGG_SIZING_WEAK_PCT*100:.0f}%` (Conviction)")
    else:
        st.write(f"- Max Risk size: `{config.MAX_TRADE_PORTFOLIO_PCT*100:.0f}%` of Portfolio")

# Retrieve latest state
price = state.get("current_price", 0.0)
metrics = state.get("metrics", {})
balances = state.get("balances", {})
signal = state.get("signal", "HOLD")

# Extract Strategy Metrics safely
sma_short = metrics.get("sma_short", 0.0) or 0.0
sma_long = metrics.get("sma_long", 0.0) or 0.0
rsi = metrics.get("rsi", 0.0) or 0.0
trend = metrics.get("trend", "WAIT")
momentum = metrics.get("momentum", "WAIT")
in_position = metrics.get("in_position", False)
entry_price = metrics.get("entry_price")

# Top row: Core Metrics
c1, c2, c3, c4 = st.columns(4)
c1.metric("Live Price (BTC)", f"${price:,.2f}")
c2.metric(f"SMA({p_short}) / SMA({p_long})", f"{sma_short:,.0f} / {sma_long:,.0f}", 
          delta="BULLISH" if sma_short > sma_long else "BEARISH", 
          delta_color="normal" if sma_short > sma_long else "inverse")

# Render signal strength string dynamically mapped inside RSI delta space
rsi_delta = f"RSI:{'OB' if rsi > p_rsi_over else ('OS' if rsi < p_rsi_under else 'NEUTRAL')}"
c3.metric("RSI & Momentum", f"{rsi:.1f}  / {(momentum*100):.2f}%" if isinstance(momentum, (int, float)) else f"{rsi:.1f}", 
          delta=rsi_delta,
          delta_color="inverse" if rsi > p_rsi_over or rsi < p_rsi_under else "off")

conviction = metrics.get("signal_strength", "WEAK")
c4.metric("Algorithm Signal", f"{signal}", 
          delta=f"{conviction} CONVICTION", delta_color="normal" if conviction == "STRONG" else "off")

st.markdown("---")

# Middle row: Portfolio Metrics
p1, p2, p3, p4 = st.columns(4)
p1.metric("USDC Balance", f"${balances.get('USDC', 0.0):,.2f}")
p2.metric("BTC Balance", f"{balances.get('BTC', 0.0):.6f}")

total_pnl = balances.get('total_pnl', 0.0)
p3.metric("Realized P&L", f"${total_pnl:,.2f}", f"{total_pnl:,.2f}", 
          delta_color="normal" if total_pnl >= 0 else "inverse")

if in_position:
    pnl_pct = metrics.get("unrealized_pnl_pct", 0) * 100
    p4.metric("Current Position", "LONG", f"{pnl_pct:+.2f}%")
else:
    p4.metric("Current Position", "FLAT", "0.00%")

st.markdown("---")

# -------------------------------------------------------------------
#  Live Charting
# -------------------------------------------------------------------
st.subheader("📊 Real-Time Chart")

chart_data = metrics.get("chart_data", [])
if chart_data:
    df_chart = pd.DataFrame(chart_data)
    
    # Needs timestamp and close
    if "timestamp" in df_chart.columns and "close" in df_chart.columns:
        # Re-calc indicators locally for smooth graph lines over the UI 
        df_chart['timestamp'] = pd.to_datetime(df_chart['timestamp'], unit='ms')
        
        # Create subplot figure: 2 rows (Price + SMA, RSI)
        fig = make_subplots(
            rows=2, cols=1, 
            shared_xaxes=True, 
            vertical_spacing=0.03,
            row_heights=[0.7, 0.3]
        )

        # Main Price Line
        fig.add_trace(go.Scatter(
            x=df_chart['timestamp'], y=df_chart['close'],
            mode='lines', name='Price', line=dict(color='yellow', width=2)
        ), row=1, col=1)
        
        # SMA Short
        if 'sma_short' in df_chart.columns:
            fig.add_trace(go.Scatter(
                x=df_chart['timestamp'], y=df_chart['sma_short'],
                mode='lines', name=f'SMA({p_short})', line=dict(color='cyan', width=1)
            ), row=1, col=1)
            
        # SMA Long
        if 'sma_long' in df_chart.columns:
            fig.add_trace(go.Scatter(
                x=df_chart['timestamp'], y=df_chart['sma_long'],
                mode='lines', name=f'SMA({p_long})', line=dict(color='magenta', width=1)
            ), row=1, col=1)

        # Entry Price Line (if applicable)
        if in_position and entry_price:
            fig.add_hline(y=entry_price, line_dash="dash", line_color="green", 
                          annotation_text=f"Entry: ${entry_price:,.0f}", row=1, col=1)

        # RSI Subplot
        if 'rsi' in df_chart.columns:
            fig.add_trace(go.Scatter(
                x=df_chart['timestamp'], y=df_chart['rsi'],
                mode='lines', name='RSI', line=dict(color='orange', width=2)
            ), row=2, col=1)
            
            # RSI Bands
            fig.add_hline(y=p_rsi_over, line_dash="dash", line_color="red", row=2, col=1)
            fig.add_hline(y=p_rsi_under, line_dash="dash", line_color="green", row=2, col=1)
            fig.add_hrect(y0=p_rsi_under, y1=p_rsi_over, fillcolor="white", opacity=0.05, row=2, col=1)

        fig.update_layout(
            template="plotly_dark",
            height=600,
            margin=dict(l=0, r=0, t=30, b=0),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Waiting for data to generate chart...")

# -------------------------------------------------------------------
#  Trade History
# -------------------------------------------------------------------
st.markdown("---")
st.subheader("📓 Trade History")

if os.path.exists(config.LOG_FILE):
    try:
        df_logs = pd.read_csv(config.LOG_FILE)
        if not df_logs.empty:
            # Reorder columns for better UI layout
            cols = ["timestamp", "signal", "side", "price", "quantity", "pnl", "pnl_pct", "status"]
            df_display = df_logs[cols].tail(50).iloc[::-1]  # Show latest first
            
            # Highlight winning trades logic
            def color_pnl(val):
                if isinstance(val, (int, float)):
                    color = 'green' if val > 0 else 'red' if val < 0 else 'gray'
                    return f'color: {color}'
                return ''
                
            st.dataframe(
                df_display.style.map(color_pnl, subset=['pnl', 'pnl_pct']),
                use_container_width=True, hide_index=True
            )
        else:
            st.info("No trades executed yet.")
    except Exception as e:
        st.warning(f"Could not load log file: {e}")
else:
    st.info("No trades executed yet.")

# Auto-refresh mechanism (Polling Engine periodically if active)
if engine.is_running:
    refresh_rate = config.AGG_POLL_INTERVAL_SEC if config.AGGRESSIVE_MODE else config.POLL_INTERVAL_SEC
    time.sleep(refresh_rate)
    st.rerun()
