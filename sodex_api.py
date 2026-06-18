"""
╔══════════════════════════════════════════════════════════════╗
║              SoDEX Trading Bot — API Client                 ║
╚══════════════════════════════════════════════════════════════╝

REST API client for SoDEX DEX.
  • Public endpoints (tickers, klines, orderbook) are unsigned.
  • Authenticated writes (place/cancel orders) use EIP-712 typed signatures.

Reference: https://sodex.com/documentation/api/api
"""

import json
import time
import uuid
import hashlib
import requests
import threading
from typing import Any, Dict, List, Optional

from eth_account import Account
from eth_account.messages import encode_typed_data

import config

# ──────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────

def _keccak256(data: bytes) -> bytes:
    """Compute Keccak-256 hash (the Ethereum variant of SHA-3)."""
    from eth_abi.exceptions import DecodingError  # noqa: F401 — import guard
    from web3 import Web3
    return Web3.keccak(data)


_nonce_lock = threading.Lock()
_last_nonce = int(time.time() * 1000)

def _generate_nonce() -> int:
    """Generate a unique incrementing nonce securely mapped against threading limits."""
    global _last_nonce
    with _nonce_lock:
        current_ts = int(time.time() * 1000)
        if current_ts <= _last_nonce:
            _last_nonce += 1
        else:
            _last_nonce = current_ts
        return _last_nonce


def _generate_cl_ord_id() -> str:
    """Generate a unique client order ID (max 36 alphanum chars)."""
    return uuid.uuid4().hex[:36]


# ──────────────────────────────────────────────────────────────
#  EIP-712 Signing
# ──────────────────────────────────────────────────────────────

def _compute_payload_hash(payload: dict) -> bytes:
    """
    payloadHash = Keccak256(json.Marshal(payload))
    Key order must match Go struct field order.
    Compact JSON — no whitespace.
    """
    compact_json = json.dumps(payload, separators=(",", ":"))
    return _keccak256(compact_json.encode("utf-8"))


def _sign_payload(payload: dict, nonce: int) -> str:
    """
    Sign a payload using EIP-712 typed structured data.

    Returns the typed signature: 0x01 + signature_bytes
    """
    payload_hash = _compute_payload_hash(payload)
    chain_id = config.get_chain_id()

    # EIP-712 typed data structure
    typed_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "ExchangeAction": [
                {"name": "payloadHash", "type": "bytes32"},
                {"name": "nonce", "type": "uint64"},
            ],
        },
        "domain": {
            "name": "spot",
            "version": "1",
            "chainId": chain_id,
            "verifyingContract": "0x0000000000000000000000000000000000000000",
        },
        "primaryType": "ExchangeAction",
        "message": {
            "payloadHash": payload_hash.hex() if isinstance(payload_hash, bytes) else payload_hash,
            "nonce": nonce,
        },
    }

    # Sign with private key
    acct = Account.from_key(config.PRIVATE_KEY)
    signed = acct.sign_typed_data(
        domain_data=typed_data["domain"],
        message_types={"ExchangeAction": typed_data["types"]["ExchangeAction"]},
        message_data=typed_data["message"],
    )

    # Typed signature: prepend byte 0x01f
    sig_hex = signed.signature.hex()
    typed_sig = "0x01" + sig_hex
    return typed_sig


def _auth_headers(signature: str, nonce: int) -> Dict[str, str]:
    """Build authenticated request headers."""
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-API-Key": config.API_KEY,
        "X-API-Sign": signature,
        "X-API-Nonce": str(nonce),
    }


# ──────────────────────────────────────────────────────────────
#  HTTP Client with Retry
# ──────────────────────────────────────────────────────────────

class SoDEXAPIError(Exception):
    """Custom exception for SoDEX API errors."""

    def __init__(self, code: int, message: str, endpoint: str = ""):
        self.code = code
        self.message = message
        self.endpoint = endpoint
        super().__init__(f"[SoDEX API Error {code}] {endpoint}: {message}")


def _request(
    method: str,
    url: str,
    params: Optional[Dict] = None,
    json_body: Optional[Dict] = None,
    headers: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Make an HTTP request with exponential backoff retry.
    Returns the parsed response data.
    """
    if headers is None:
        headers = {"Accept": "application/json"}

    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = requests.request(
                method=method,
                url=url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=15,
            )

            # Rate limit hit
            if resp.status_code == 429:
                wait = config.RETRY_DELAY_SEC * (2 ** attempt)
                print(f"   ⚠ Rate limited. Waiting {wait}s before retry {attempt}/{config.MAX_RETRIES}...")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()

            # SoDEX envelope: check code
            if data.get("code") is not None and data["code"] != 0:
                raise SoDEXAPIError(
                    code=data["code"],
                    message=data.get("error", "Unknown error"),
                    endpoint=url,
                )

            return data

        except requests.exceptions.ConnectionError as e:
            wait = config.RETRY_DELAY_SEC * (2 ** attempt)
            print(f"   ⚠ Connection error: {e}. Retry {attempt}/{config.MAX_RETRIES} in {wait}s...")
            time.sleep(wait)
        except requests.exceptions.Timeout:
            wait = config.RETRY_DELAY_SEC * (2 ** attempt)
            print(f"   ⚠ Timeout. Retry {attempt}/{config.MAX_RETRIES} in {wait}s...")
            time.sleep(wait)

    raise SoDEXAPIError(code=-1, message=f"All {config.MAX_RETRIES} retries exhausted", endpoint=url)


# ══════════════════════════════════════════════════════════════
#  PUBLIC ENDPOINTS (unsigned)
# ══════════════════════════════════════════════════════════════

def get_ticker(symbol: str = config.SYMBOL) -> Dict[str, Any]:
    """
    Fetch 24h ticker statistics for a symbol.

    GET /markets/tickers?symbol=vBTC_vUSDC
    Returns: SpotTicker object with lastPrice, high, low, volume, etc.
    """
    url = f"{config.get_endpoint('rest_spot')}/markets/tickers"
    data = _request("GET", url, params={"symbol": symbol})
    tickers = data.get("data", [])
    if tickers:
        return tickers[0]
    return {}


def get_klines(
    symbol: str = config.SYMBOL,
    interval: str = config.KLINE_INTERVAL,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Fetch candlestick/kline data for SMA calculation.

    GET /markets/{symbol}/klines?interval=5m&limit=50
    Returns: Array of RPCKline objects.
    """
    url = f"{config.get_endpoint('rest_spot')}/markets/{symbol}/klines"
    data = _request("GET", url, params={"interval": interval, "limit": limit})
    return data.get("data", [])


def get_orderbook(symbol: str = config.SYMBOL, limit: int = 10) -> Dict[str, Any]:
    """
    Fetch order book depth.

    GET /markets/{symbol}/orderbook?limit=10
    """
    url = f"{config.get_endpoint('rest_spot')}/markets/{symbol}/orderbook"
    data = _request("GET", url, params={"limit": limit})
    return data.get("data", {})


def get_recent_trades(symbol: str = config.SYMBOL, limit: int = 20) -> List[Dict]:
    """
    Fetch recent public trades.

    GET /markets/{symbol}/trades?limit=20
    """
    url = f"{config.get_endpoint('rest_spot')}/markets/{symbol}/trades"
    data = _request("GET", url, params={"limit": limit})
    return data.get("data", [])


def get_symbols() -> List[Dict[str, Any]]:
    """
    Fetch all available trading symbols and their rules.

    GET /markets/symbols
    """
    url = f"{config.get_endpoint('rest_spot')}/markets/symbols"
    data = _request("GET", url)
    return data.get("data", [])


def get_balances(user_address: str = config.API_KEY) -> Dict[str, Any]:
    """
    Fetch account balances.

    GET /accounts/{userAddress}/balances
    """
    url = f"{config.get_endpoint('rest_spot')}/accounts/{user_address}/balances"
    data = _request("GET", url)
    return data.get("data", {})


def get_order(order_id: str) -> Dict[str, Any]:
    """
    Fetch order status natively.
    """
    url = f"{config.get_endpoint('rest_spot')}/orders/{order_id}"
    try:
        data = _request("GET", url)
        return data.get("data", {})
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════
#  AUTHENTICATED ENDPOINTS (signed writes)
# ══════════════════════════════════════════════════════════════

def _get_symbol_id(symbol: str = config.SYMBOL) -> int:
    """Resolve symbol name → symbolID from the exchange."""
    symbols = get_symbols()
    for s in symbols:
        if s.get("symbol") == symbol or s.get("name") == symbol:
            return s.get("symbolID", s.get("id", 0))
    # Fallback: return 0 (will fail validation server-side)
    return 0


def place_order(
    symbol: str,
    side: str,
    order_type: str = "MARKET",
    quantity: str = "",
    price: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Place a new order via SoDEX Spot REST API.

    POST /trade/orders/batch
    Auth: EIP-712 signed write

    Args:
        symbol:     Trading pair (e.g. "vBTC_vUSDC")
        side:       "BUY" or "SELL"
        order_type: "MARKET" or "LIMIT"
        quantity:   Amount as DecimalString (e.g. "0.001")
        price:      Limit price as DecimalString (required for LIMIT orders)

    Returns: API response with order ID
    """
    if not config.PRIVATE_KEY:
        raise SoDEXAPIError(code=-1, message="PRIVATE_KEY not configured — cannot sign orders")

    nonce = _generate_nonce()
    cl_ord_id = _generate_cl_ord_id()
    symbol_id = _get_symbol_id(symbol)

    # Map side → int (1=buy, 2=sell per SoDEX schema)
    side_int = 1 if side.upper() == "BUY" else 2

    # Map order type → int (1=limit, 2=market per SoDEX schema)
    type_int = 2 if order_type.upper() == "MARKET" else 1

    # Map timeInForce → int (1=GTC, 3=IOC per SoDEX schema)
    tif_int = 3 if order_type.upper() == "MARKET" else 1  # Market→IOC, Limit→GTC

    # Build order item (field order MUST match Go struct)
    order_item = {
        "clOrdID": cl_ord_id,
        "modifier": 0,
        "side": side_int,
        "type": type_int,
        "timeInForce": tif_int,
    }

    # Add price for limit orders
    if price is not None:
        order_item["price"] = price

    # Add quantity
    order_item["quantity"] = quantity

    # Build the signing payload (type + params)
    signing_payload = {
        "type": "newOrder",
        "params": {
            "accountID": config.ACCOUNT_ID,
            "symbolID": symbol_id,
            "orders": [order_item],
        },
    }

    # Sign the payload
    signature = _sign_payload(signing_payload, nonce)

    # Build the request body (params only, without type wrapper)
    request_body = signing_payload["params"]

    url = f"{config.get_endpoint('rest_spot')}/trade/orders/batch"
    headers = _auth_headers(signature, nonce)

    return _request("POST", url, json_body=request_body, headers=headers)


def cancel_order(
    symbol: str,
    order_id: int,
) -> Dict[str, Any]:
    """
    Cancel an existing order.

    DELETE /trade/orders/batch
    Auth: EIP-712 signed write
    """
    if not config.PRIVATE_KEY:
        raise SoDEXAPIError(code=-1, message="PRIVATE_KEY not configured")

    nonce = _generate_nonce()
    symbol_id = _get_symbol_id(symbol)

    signing_payload = {
        "type": "cancelOrder",
        "params": {
            "accountID": config.ACCOUNT_ID,
            "symbolID": symbol_id,
            "orders": [{"orderID": order_id}],
        },
    }

    signature = _sign_payload(signing_payload, nonce)
    request_body = signing_payload["params"]

    url = f"{config.get_endpoint('rest_spot')}/trade/orders/batch"
    headers = _auth_headers(signature, nonce)

    return _request("DELETE", url, json_body=request_body, headers=headers)

def get_order_status(symbol: str, order_id: str) -> Dict[str, Any]:
    """
    Fetch the status of an existing order.
    GET /trade/orders
    Auth: EIP-712 signed read
    """
    if not config.PRIVATE_KEY:
        raise SoDEXAPIError(code=-1, message="PRIVATE_KEY not configured")

    nonce = _generate_nonce()
    symbol_id = _get_symbol_id(symbol)

    signing_payload = {
        "type": "orderStatus",
        "params": {
            "accountID": config.ACCOUNT_ID,
            "symbolID": symbol_id,
            "orderID": str(order_id),
        },
    }

    signature = _sign_payload(signing_payload, nonce)
    
    url = f"{config.get_endpoint('rest_spot')}/trade/orders"
    headers = _auth_headers(signature, nonce)

    return _request("GET", url, params={"orderID": str(order_id), "symbolID": symbol_id, "accountID": config.ACCOUNT_ID}, headers=headers)
