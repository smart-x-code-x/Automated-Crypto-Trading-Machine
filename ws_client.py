import threading
import json
import websocket
import logging

class BinanceWSClient:
    """Background WebSocket streaming real-time Binance prices thread-safely."""
    def __init__(self, symbol: str = "btcusdt"):
        self.symbol = symbol.lower()
        self.url = f"wss://stream.binance.com:9443/ws/{self.symbol}@trade"
        self.is_running = False
        self.current_price = 0.0
        
        self.lock = threading.Lock()
        self.ws = None
        self.thread = None
        self.logger = logging.getLogger("WSClient")

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.ws = websocket.WebSocketApp(
            self.url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )
        self.thread = threading.Thread(target=self.ws.run_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.is_running = False
        if self.ws:
            self.ws.close()
        if self.thread:
            self.thread.join(timeout=2)

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            price = float(data.get("p", 0.0))
            if price > 0:
                with self.lock:
                    self.current_price = price
        except Exception:
            pass

    def _on_error(self, ws, error):
        self.logger.error(f"WebSocket Error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        self.logger.warning("WebSocket Closed.")
        
    def get_price(self) -> float:
        with self.lock:
            return self.current_price
