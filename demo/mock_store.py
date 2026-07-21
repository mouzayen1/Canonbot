"""A tiny sandbox "Target-like" store for the buying-bot demo.

Everything here is fake: fake product, fake inventory, fake checkout, fake
payment. It exists only so a bot has something to race against. There is NO
anti-bot protection, because the point is to demonstrate *performance*
techniques, not to defeat a real retailer.

Endpoints (all JSON):
  GET  /product            -> {name, price, available, stock}
  POST /cart               -> {cart_token}          (409 if not yet released)
  POST /checkout {token}   -> {order_id, status}    (409 if sold out)

The product is "out of stock" until a scheduled drop time, then a limited number
of units are available until they sell out.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class StoreState:
    def __init__(self, stock: int = 1, drop_delay: float = 1.5, latency: float = 0.01):
        self.lock = threading.Lock()
        self.stock = stock
        self.total = stock
        self.latency = latency            # simulated per-request network delay
        self.drop_at = time.monotonic() + drop_delay
        self._cart_seq = 0
        self.carts: set[str] = set()
        self.orders: list[str] = []

    def released(self) -> bool:
        return time.monotonic() >= self.drop_at

    def available(self) -> bool:
        # The "in stock" signal a bot polls for. It flips true at the drop and
        # stays true, so both bots see it — the scarce resource is checkout, not
        # the signal. (Models "the page shows in-stock; first to check out wins".)
        return self.released()

    def make_cart(self) -> str | None:
        with self.lock:
            if not self.released():
                return None
            self._cart_seq += 1
            token = f"cart-{self._cart_seq}"
            self.carts.add(token)
            return token

    def checkout(self, token: str | None) -> tuple[str | None, str]:
        with self.lock:
            if not self.released():
                return None, "not_released"
            if token not in self.carts:
                return None, "invalid_cart"
            if self.stock <= 0:
                return None, "sold_out"
            self.stock -= 1
            order_id = f"order-{len(self.orders) + 1}"
            self.orders.append(order_id)
            return order_id, "confirmed"


def _make_handler(state: StoreState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # enables keep-alive so a Session can reuse the socket

        def log_message(self, *args):  # keep the demo output clean
            pass

        def _send(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            time.sleep(state.latency)
            if self.path.startswith("/product"):
                self._send(200, {
                    "name": "Sandbox PowerShot (FAKE)",
                    "price": 549.99,
                    "available": state.available(),
                    "stock": max(0, state.stock),
                })
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self):
            time.sleep(state.latency)
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            if self.path.startswith("/cart"):
                token = state.make_cart()
                if token is None:
                    self._send(409, {"error": "out_of_stock"})
                else:
                    self._send(200, {"cart_token": token})
            elif self.path.startswith("/checkout"):
                try:
                    data = json.loads(raw or b"{}")
                except ValueError:
                    data = {}
                order_id, status = state.checkout(data.get("cart_token"))
                code = 200 if order_id else 409
                self._send(code, {"order_id": order_id, "status": status})
            else:
                self._send(404, {"error": "not_found"})

    return Handler


def start_store(state: StoreState, port: int = 0) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(state))
    httpd.daemon_threads = True
    # Stay quiet when a client drops a keep-alive connection at round's end.
    httpd.handle_error = lambda request, client_address: None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
