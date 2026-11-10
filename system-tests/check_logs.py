"""After the tests: did the service log a server error while they ran?

Usage: check_logs.py <file of `docker compose logs` output for the service containers>

Fails on any 5xx response in the access log, any error-level line and any stray
traceback. Tests only see the responses they asked for; this catches the
background write that failed and the handler that answered 200 after logging
an exception.
"""

import json
import sys

# Two refusals the service logs at error level although it answers 4xx; the
# tests trigger both on purpose (a replayed refresh token, a taken slug).
EXPECTED = {
    ("app.error.unhandled", "refresh_token_rotation_failed"),
    ("app.error.unhandled", "A workspace with this slug already exists"),
}

problems, requests = [], 0
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    start = line.find("{")
    try:
        entry = json.loads(line[start:]) if start >= 0 else None
    except ValueError:
        entry = None
    if not isinstance(entry, dict):
        if "Traceback (most recent call last)" in line:
            problems.append(f"traceback outside the structured log: {line.strip()[:200]}")
        continue
    if entry.get("event") == "http.access":
        requests += 1
        if entry.get("http.status", 0) >= 500:
            problems.append(f"{entry['http.status']} from {entry.get('http.method')} {entry.get('http.route')}")
    elif entry.get("level") in ("error", "critical"):
        if (entry.get("event"), entry.get("error")) not in EXPECTED:
            problems.append(f"{entry['level']}: {entry.get('event')} {str(entry.get('error', ''))[:200]}")

# Guards the check itself: an empty or unparsed log must not read as a clean one.
if requests < 100:
    problems.append(f"only {requests} access-log lines found; is this the service log?")

for problem in sorted(set(problems)):
    print(f"service log: {problem}", file=sys.stderr)
print(f"service log: {requests} requests, {len(set(problems))} problem(s)")
sys.exit(1 if problems else 0)
