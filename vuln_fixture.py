#!/usr/bin/env python3
"""
vuln_fixture.py - INTENTIONALLY VULNERABLE login endpoint.

Throwaway test target for sqli_probe.py. Not a deployable app. Bind it to
127.0.0.1 only. It exists so the probe's detection logic can be exercised
against a real HTTP surface instead of being assumed correct.

    python vuln_fixture.py --port 8099 --dbms sqlite|mysql-blind
"""
import argparse
import sqlite3
import threading
import time

from flask import Flask, request, redirect

app = Flask(__name__)
DB = "users.db"
LOCK = threading.Lock()
MODE = "sqlite"


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id       INTEGER PRIMARY KEY,
  username TEXT NOT NULL,
  password TEXT NOT NULL
);
"""
SEED = [
    (1, "admin", "$2y$10$abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMN"),
    (2, "jdoe", "5f4dcc3b5aa765d61d8327deb882cf99"),  # md5("password")
]


def db():
    c = sqlite3.connect(DB)
    # SQLite has no SLEEP() builtin. Registering it as a UDF lets the
    # fixture exercise the time-based path with the same payload shape a
    # real MySQL target would receive.
    c.create_function("SLEEP", 1, _sleep)
    c.create_function("pg_sleep", 1, _sleep)
    return c


def _sleep(seconds):
    try:
        time.sleep(min(float(seconds or 0), 30.0))
    except (TypeError, ValueError):
        pass
    return 0


def init():
    c = db()
    c.execute(SCHEMA)
    n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if not n:
        c.executemany("INSERT INTO users (id,username,password) VALUES (?,?,?)",
                      SEED)
    c.commit()
    c.close()


def page(msg, tone="err"):
    colour = "#c0392b" if tone == "err" else "#27ae60"
    # Deliberately varies a token so naive length-only diffing is noisy.
    return (f"<!doctype html><html><head><title>login</title></head><body>"
            f"<h1>Member Login</h1><form method=post action=/login>"
            f"<input name=username><input name=password type=password>"
            f"<button>Sign In</button></form>"
            f"<p style='color:{colour}'>{msg}</p>"
            f"<footer>rendered at revision r1 build 4471</footer></body></html>")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return page("please sign in", "ok")

    u = request.form.get("username", "")
    p = request.form.get("password", "")

    with LOCK:
        try:
            if MODE == "sqlite":
                # Direct string interpolation: textbook injectable sink.
                q = f"SELECT id FROM users WHERE username='{u}' AND password='{p}'"
                row = db().execute(q).fetchone()
            else:
                # Simulates a backend that swallows errors and still sleeps,
                # mirroring the MySQL time-based path with a bounded delay.
                try:
                    q = f"SELECT id FROM users WHERE username='{u}'"
                    db().execute(q).fetchone()
                except sqlite3.Error:
                    pass
                return page("invalid credentials")

            if row:
                return redirect("/dashboard")
            return page("invalid username or password")
        except sqlite3.Error as exc:
            return page(f"database error: {exc}"), 500


@app.route("/dashboard")
def dash():
    return page("Welcome back! You are signed in. [logout]", "ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--mode", default="sqlite",
                    choices=["sqlite", "error-only", "blind"])
    a = ap.parse_args()
    MODE = a.mode
    init()
    print(f"fixture listening on 127.0.0.1:{a.port}  mode={MODE}  db={DB}")
    app.run(host="127.0.0.1", port=a.port, threaded=False, debug=False)
