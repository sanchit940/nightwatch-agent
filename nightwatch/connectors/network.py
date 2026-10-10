"""Network connector: bounded DNS, TCP, TLS and HTTP tests from a chosen source, and a path trace.

Tests run from the agent host ("agent") or from inside a container, so the agent can test the path
from the real source of the traffic. Each test has a hard timeout and returns one structured result.
"""

from __future__ import annotations

import json
import sys
from typing import Annotated, Literal

from pydantic import Field

from ..runner import run
from .base import Args, RiskLevel, ToolOutput, tool

HOST = Annotated[str, Field(pattern=r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$", description="Hostname or IP address")]
SOURCE = Annotated[str, Field(pattern=r"^(agent|[A-Za-z0-9][A-Za-z0-9_.-]{0,127}|k8s:[a-z0-9-]{1,63}/[a-z0-9.-]{1,253})$",
                               description="Where the test runs: 'agent' (the NightWatch host), a Docker container name (for example nw-toolbox), or a pod as k8s:<namespace>/<pod>")]
URL = Annotated[str, Field(pattern=r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~%/?=&+-]*)?$", max_length=300)]

# Constant probe program. Arguments arrive through argv, never through code text.
PROBE = r'''
import json, socket, ssl, sys, time, urllib.request, urllib.error
op = sys.argv[1]; r = {"op": op}
def now(): return time.monotonic()
try:
    if op == "dns":
        t = now(); infos = socket.getaddrinfo(sys.argv[2], None)
        r.update(ok=True, addresses=sorted({i[4][0] for i in infos}), ms=round((now()-t)*1000, 1))
    elif op == "tcp":
        host, port, to = sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
        t = now()
        try:
            addr = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)[0][4]
        except socket.gaierror as e:
            r.update(ok=False, stage="dns", error=f"dns: {e}"); print(json.dumps(r)); sys.exit()
        s = socket.socket(socket.AF_INET6 if ":" in addr[0] else socket.AF_INET); s.settimeout(to)
        try:
            s.connect(addr); r.update(ok=True, peer=addr[0], ms=round((now()-t)*1000, 1))
        except socket.timeout:
            r.update(ok=False, stage="connect", error="timeout (no SYN-ACK: filtered, dropped or host down)", peer=addr[0], ms=round((now()-t)*1000, 1))
        except ConnectionRefusedError:
            r.update(ok=False, stage="connect", error="connection refused (RST: host reachable, nothing listening on port)", peer=addr[0])
        except OSError as e:
            r.update(ok=False, stage="connect", error=f"{type(e).__name__}: {e}", peer=addr[0])
        finally:
            s.close()
    elif op == "tls":
        host, port, to = sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
        ctx = ssl.create_default_context(); t = now()
        try:
            with socket.create_connection((host, port), timeout=to) as raw:
                with ctx.wrap_socket(raw, server_hostname=host) as s:
                    c = s.getpeercert()
                    na = ssl.cert_time_to_seconds(c["notAfter"])
                    r.update(ok=True, version=s.version(), subject=dict(x[0] for x in c["subject"]), issuer=dict(x[0] for x in c["issuer"]),
                             not_after=c["notAfter"], days_left=round((na-time.time())/86400, 1),
                             san=[v for k, v in c.get("subjectAltName", [])][:10], ms=round((now()-t)*1000, 1))
        except ssl.SSLCertVerificationError as e:
            r.update(ok=False, stage="tls_verify", error=f"{e.verify_message} (code {e.verify_code})")
        except ssl.SSLError as e:
            r.update(ok=False, stage="tls_handshake", error=str(e))
        except OSError as e:
            r.update(ok=False, stage="connect", error=f"{type(e).__name__}: {e}")
    elif op == "http":
        url, to = sys.argv[2], float(sys.argv[3]); t = now()
        req = urllib.request.Request(url, headers={"User-Agent": "nightwatch-probe/1"})
        try:
            resp = urllib.request.urlopen(req, timeout=to); code = resp.status; hdrs = resp.headers; body = resp.read(400)
        except urllib.error.HTTPError as e:
            code = e.code; hdrs = e.headers; body = e.read(400)
        except urllib.error.URLError as e:
            r.update(ok=False, stage="connect", error=str(e.reason)); print(json.dumps(r)); sys.exit()
        except socket.timeout:
            r.update(ok=False, stage="response", error="timeout waiting for response"); print(json.dumps(r)); sys.exit()
        keep = {k: v for k, v in hdrs.items() if k.lower() in ("server", "content-type", "location", "retry-after", "via", "x-upstream")}
        r.update(ok=200 <= code < 400, status=code, ms=round((now()-t)*1000, 1), headers=keep, body=body.decode(errors="replace"))
except Exception as e:
    r.update(ok=False, error=f"{type(e).__name__}: {e}")
print(json.dumps(r))
'''


def _probe(source: str, *args: str, timeout: float = 15) -> dict:
    if source == "agent":
        res = run([sys.executable, "-I", "-c", PROBE, *args], timeout=timeout)
    elif source.startswith("k8s:"):
        from .kubernetes import _ctx
        ns, pod = source[4:].split("/", 1)
        res = run(_ctx() + ["exec", "-n", ns, pod, "--", "python3", "-c", PROBE, *args], timeout=timeout)
    else:
        res = run(["docker", "exec", source, "python3", "-c", PROBE, *args], timeout=timeout)
        if res.exit_code in (126, 127) or "executable file not found" in res.stderr:
            return _busybox(source, *args, timeout=timeout)
    if res.error_code == "TIMEOUT":
        return {"ok": False, "op": args[0], "error": "probe timeout", "error_code": "TIMEOUT"}
    if "No such container" in res.stderr or "is not running" in res.stderr:
        return {"ok": False, "op": args[0], "error": res.stderr.strip()[:300], "error_code": "SOURCE_UNAVAILABLE"}
    try:
        return json.loads(res.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "op": args[0], "error": (res.stderr or res.stdout)[:400], "error_code": "PROBE_ERROR"}


def _busybox(source: str, op: str, *args: str, timeout: float = 15) -> dict:
    """Fallback for containers without python (for example nginx:alpine): busybox tools, fixed argv."""
    if op == "dns":
        res = run(["docker", "exec", source, "nslookup", args[0]], timeout=timeout)
    elif op == "tcp":
        res = run(["docker", "exec", source, "nc", "-z", "-w", str(int(float(args[2]))), args[0], args[1]], timeout=timeout)
    elif op == "http":
        res = run(["docker", "exec", source, "wget", "-S", "-q", "-O", "-", "-T", str(int(float(args[1]))), args[0]], timeout=timeout)
    else:
        return {"ok": False, "op": op, "error": "not supported from this source (no python3)", "error_code": "UNSUPPORTED"}
    return {"ok": res.ok, "op": op, "via": "busybox", "output": (res.stdout + res.stderr)[-1200:]}


def _out(r: dict) -> ToolOutput:
    code = r.get("error_code")
    if not r.get("ok") and not code:
        code = f"HTTP_{r['status']}" if r.get("status") else f"{(r.get('stage') or r.get('op') or 'probe').upper()}_FAILED"
    return ToolOutput(bool(r.get("ok")), json.dumps(r, indent=1), code, data=r)


class DnsArgs(Args):
    source: SOURCE = "agent"
    host: HOST


@tool("net_dns_resolve", "Resolve a hostname from the source (uses the source's resolver, /etc/hosts and Docker/K8s DNS).",
      DnsArgs, RiskLevel.DIAGNOSTIC, "network", targets=lambda a: [f"net:{a.source}->{a.host}"])
def net_dns_resolve(a: DnsArgs) -> ToolOutput:
    return _out(_probe(a.source, "dns", a.host))


class TcpArgs(Args):
    source: SOURCE = "agent"
    host: HOST
    port: int = Field(ge=1, le=65535)
    timeout_s: float = Field(3, ge=0.5, le=10)


@tool("net_tcp_connect", "Open one TCP connection from the source to host:port. Distinguishes refused (RST), timeout (filtered/dropped) and DNS failure.",
      TcpArgs, RiskLevel.DIAGNOSTIC, "network", targets=lambda a: [f"net:{a.source}->{a.host}:{a.port}"])
def net_tcp_connect(a: TcpArgs) -> ToolOutput:
    return _out(_probe(a.source, "tcp", a.host, str(a.port), str(a.timeout_s)))


class TlsArgs(Args):
    source: SOURCE = "agent"
    host: HOST
    port: int = Field(443, ge=1, le=65535)
    timeout_s: float = Field(5, ge=0.5, le=10)


@tool("net_tls_check", "TLS handshake with certificate verification: protocol, subject, issuer, SAN, expiry and days left.",
      TlsArgs, RiskLevel.DIAGNOSTIC, "network", targets=lambda a: [f"net:{a.source}->{a.host}:{a.port}"])
def net_tls_check(a: TlsArgs) -> ToolOutput:
    return _out(_probe(a.source, "tls", a.host, str(a.port), str(a.timeout_s)))


class HttpArgs(Args):
    source: SOURCE = "agent"
    url: URL
    timeout_s: float = Field(5, ge=0.5, le=15)


@tool("net_http_check", "One HTTP GET from the source: status code, latency, key headers, first 400 bytes of the body.",
      HttpArgs, RiskLevel.DIAGNOSTIC, "network", targets=lambda a: [f"net:{a.source}->{a.url}"])
def net_http_check(a: HttpArgs) -> ToolOutput:
    return _out(_probe(a.source, "http", a.url, str(a.timeout_s), timeout=a.timeout_s + 10))


class TraceArgs(Args):
    source: SOURCE = "agent"
    url: URL
    timeout_s: float = Field(5, ge=0.5, le=10)


@tool("net_trace_path",
      "Follow the request path from source to URL step by step: DNS -> TCP -> TLS (https only) -> HTTP. Stops at the first failed step and reports the last confirmed successful step.",
      TraceArgs, RiskLevel.DIAGNOSTIC, "network", timeout_s=60, targets=lambda a: [f"net:{a.source}->{a.url}"])
def net_trace_path(a: TraceArgs) -> ToolOutput:
    from urllib.parse import urlparse
    u = urlparse(a.url)
    host = u.hostname or ""
    port = u.port or (443 if u.scheme == "https" else 80)
    steps: list[dict] = []
    plan = [("dns", ["dns", host]), ("tcp", ["tcp", host, str(port), str(a.timeout_s)])]
    if u.scheme == "https":
        plan.append(("tls", ["tls", host, str(port), str(a.timeout_s)]))
    plan.append(("http", ["http", a.url, str(a.timeout_s)]))
    last_ok = "none"
    for name, args in plan:
        r = _probe(a.source, *args, timeout=a.timeout_s + 10)
        steps.append({"step": name, **r})
        if not r.get("ok"):
            break
        last_ok = name
    result = {"source": a.source, "url": a.url, "last_successful_step": last_ok,
              "failed_step": None if last_ok == "http" else plan[len(steps) - 1][0], "steps": steps}
    return ToolOutput(last_ok == "http", json.dumps(result, indent=1), data=result)
