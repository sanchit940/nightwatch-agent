"""Shop counter API used by the NightWatch lab.

Endpoints:
  GET /         increment and return the visit counter (Redis)
  GET /healthz  200 when Redis answers PING, else 503 with the reason
  GET /metrics  Prometheus metrics
"""

import hashlib
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import redis
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
PORT = int(os.environ.get("PORT", "8000"))
DATA_DIR = os.environ.get("DATA_DIR", "/data")
ACCESS_LOG_MAX_BYTES = int(os.environ.get("ACCESS_LOG_MAX_BYTES", str(8 * 1024 * 1024)))
# Rounds of PBKDF2 used to sign the session token returned to the client.
TOKEN_ROUNDS = int(os.environ.get("TOKEN_ROUNDS", "20000"))
# Size of the local hot-key cache preloaded at start (0 disables preloading).
CACHE_WARM_MB = int(os.environ.get("CACHE_WARM_MB", "0"))
HOSTNAME = socket.gethostname()

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
log = logging.getLogger("api")

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "path", "code"])
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
REDIS_ERRORS = Counter("redis_errors_total", "Redis call failures", ["op"])
WRITE_ERRORS = Counter("access_log_write_errors_total", "Failed writes to the access log")
CACHE_BYTES = Gauge("local_cache_bytes", "Bytes held in the local hot-key cache")

KNOWN_PATHS = {"/", "/healthz", "/metrics"}

rdb = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    socket_connect_timeout=1.0,
    socket_timeout=1.0,
    retry_on_timeout=False,
)

_local_cache = []
_log_lock = threading.Lock()


def describe_redis_error(exc):
    text = str(exc)
    low = text.lower()
    if "name or service not known" in low or "temporary failure in name resolution" in low or "name does not resolve" in low:
        reason = "redis dns lookup failed"
    elif "connection refused" in low:
        reason = "redis connection refused"
    elif "timeout" in low or "timed out" in low:
        reason = "redis timeout"
    else:
        reason = "redis error"
    return f'{reason} host={REDIS_HOST} port={REDIS_PORT} error="{text}"'


def write_access_record(record):
    path = os.path.join(DATA_DIR, "access.log")
    with _log_lock:
        try:
            if os.path.exists(path) and os.path.getsize(path) > ACCESS_LOG_MAX_BYTES:
                os.replace(path, path + ".1")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
            fh.flush()


def sign_token(value):
    return hashlib.pbkdf2_hmac("sha256", str(value).encode(), HOSTNAME.encode(), TOKEN_ROUNDS).hex()[:16]


def warm_cache():
    target = CACHE_WARM_MB * 1024 * 1024
    chunk = 8 * 1024 * 1024
    loaded = 0
    log.info("cache warm-up started target_mb=%d", CACHE_WARM_MB)
    while loaded < target:
        _local_cache.append(os.urandom(64) * (chunk // 64))
        loaded += chunk
        CACHE_BYTES.set(loaded)
        if loaded % (64 * 1024 * 1024) == 0:
            log.info("cache warm-up progress loaded_mb=%d target_mb=%d", loaded // (1024 * 1024), CACHE_WARM_MB)
        time.sleep(0.25)
    log.info("cache warm-up finished loaded_mb=%d", loaded // (1024 * 1024))


class Handler(BaseHTTPRequestHandler):
    server_version = "shop-api/1.4"
    sys_version = ""

    def log_message(self, fmt, *args):  # silence default stderr access log
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return code

    def do_GET(self):
        start = time.perf_counter()
        path = self.path.split("?", 1)[0]
        try:
            if path == "/":
                code = self.handle_index()
            elif path == "/healthz":
                code = self.handle_healthz()
            elif path == "/metrics":
                code = self._send(200, generate_latest(), CONTENT_TYPE_LATEST)
            else:
                code = self._send(404, json.dumps({"error": "not found"}))
        except Exception as exc:  # last-resort guard
            log.exception("unhandled error path=%s", path)
            code = self._send(500, json.dumps({"error": str(exc)}))
        dur = time.perf_counter() - start
        label = path if path in KNOWN_PATHS else "other"
        REQUESTS.labels("GET", label, str(code)).inc()
        LATENCY.labels(label).observe(dur)
        if path != "/metrics":
            client = self.headers.get("X-Real-IP") or self.client_address[0]
            log.info("request method=GET path=%s status=%d duration_ms=%.1f client=%s", path, code, dur * 1000, client)

    def handle_index(self):
        try:
            hits = rdb.incr("hits")
        except redis.RedisError as exc:
            REDIS_ERRORS.labels("incr").inc()
            msg = describe_redis_error(exc)
            log.error(msg)
            return self._send(503, json.dumps({"error": "backend unavailable"}))
        token = sign_token(hits)
        try:
            write_access_record({"ts": time.time(), "hits": hits, "host": HOSTNAME})
        except OSError as exc:
            WRITE_ERRORS.inc()
            log.error('access log write failed path=%s error="%s"', os.path.join(DATA_DIR, "access.log"), exc)
            return self._send(500, json.dumps({"error": "internal error"}))
        return self._send(200, json.dumps({"hits": hits, "host": HOSTNAME, "token": token}))

    def handle_healthz(self):
        try:
            rdb.ping()
        except redis.RedisError as exc:
            REDIS_ERRORS.labels("ping").inc()
            msg = describe_redis_error(exc)
            log.error("healthz failed: %s", msg)
            return self._send(503, json.dumps({"status": "fail", "reason": msg}))
        return self._send(200, json.dumps({"status": "ok"}))


def main():
    log.info("starting shop-api host=%s port=%d redis=%s:%d data_dir=%s", HOSTNAME, PORT, REDIS_HOST, REDIS_PORT, DATA_DIR)
    if CACHE_WARM_MB > 0:
        threading.Thread(target=warm_cache, name="cache-warm", daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True

    def shutdown(signum, _frame):
        log.info("received %s, shutting down", signal.Signals(signum).name)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    server.serve_forever()
    log.info("stopped")


if __name__ == "__main__":
    main()
