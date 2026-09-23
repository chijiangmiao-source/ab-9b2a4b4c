"""One-shot verification gate for the track-pair audit service.

Runs inside the Compose ``verify`` service (and locally with
``AUDIT_BASE_URL=http://127.0.0.1:8080``). It performs:

1. a build/syntax check (byte-compilation and import of the service);
2. the unit-test suite;
3. API/HTTP smoke tests against a running instance:
   - health endpoint readiness,
   - a nested-vs-disjoint co-optimal solution,
   - a cheap crossing decoy that must lose to cardinality,
   - a legal empty-candidate request (unique empty solution),
   - an illegal endpoint reference (field-path error, no audit leakage).

Exit status is 0 only if every step passes.
"""

from __future__ import annotations

import json
import os
import py_compile
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_URL = os.environ.get("AUDIT_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
HEALTH_TIMEOUT_S = float(os.environ.get("HEALTH_TIMEOUT_S", "30"))

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def http_json(method: str, path: str, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def wait_for_health() -> bool:
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(BASE_URL + "/healthz", timeout=3) as r:
                payload = json.loads(r.read().decode("utf-8"))
                if r.status == 200 and payload.get("status") == "ready":
                    return True
        except (OSError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    print(f"health check did not become ready: {last_error}")
    return False


def step_build_check() -> None:
    print("== build check: byte-compiling sources ==")
    ok = True
    for relative in ("app/solver.py", "app/server.py", "scripts/verify.py"):
        path = os.path.join(ROOT, relative)
        try:
            py_compile.compile(path, doraise=True)
        except py_compile.PyCompileError as exc:
            ok = False
            print(exc)
    check("byte-compile all modules", ok)

    print("== build check: import service modules ==")
    try:
        subprocess.run(
            [sys.executable, "-c", "import solver, server"],
            cwd=os.path.join(ROOT, "app"),
            check=True,
            capture_output=True,
        )
        check("import solver and server", True)
    except subprocess.CalledProcessError as exc:
        check("import solver and server", False, exc.stderr.decode())


def step_unit_tests() -> None:
    print("== unit tests ==")
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    tail = "\n".join(proc.stdout.strip().splitlines()[-3:])
    check(
        "unit-test suite exits zero",
        proc.returncode == 0,
        tail or proc.stderr,
    )


def hits(n):
    return [{"id": f"h{i}", "position": i} for i in range(1, n + 1)]


def pair(cid, a, b, residual):
    return {
        "id": cid,
        "endpoints": [f"h{a}", f"h{b}"],
        "residual": residual,
    }


def step_smoke_tests() -> None:
    print("== API/HTTP smoke tests ==")
    check("GET /healthz reports ready", wait_for_health())

    # 1. Nested and disjoint matchings are co-optimal: count 2, every edge
    #    optional, nested solution canonical under its smaller first id.
    status, body = http_json(
        "POST",
        "/audit",
        {
            "hits": hits(4),
            "candidates": [
                pair("p1", 1, 4, 0),
                pair("p2", 2, 3, 0),
                pair("p3", 1, 2, 0),
                pair("p4", 3, 4, 0),
            ],
        },
    )
    ok = (
        status == 200
        and body["solution_count"] == "2"
        and body["paired_hit_count"] == 4
        and body["total_residual"] == 0
        and body["canonical_pair_ids"] == ["p1", "p2"]
        and sorted(body["candidate_audit"]["optional"])
        == ["p1", "p2", "p3", "p4"]
        and body["candidate_audit"]["required"] == []
        and body["candidate_audit"]["never"] == []
    )
    check("nested co-optimal solution: count/canonical/audit", ok,
          f"status={status} body={body}")

    # 2. Zero-residual crossing edges must not beat the costlier disjoint
    #    pair set, because cardinality dominates residual.
    status, body = http_json(
        "POST",
        "/audit",
        {
            "hits": hits(4),
            "candidates": [
                pair("x1", 1, 3, 0),
                pair("x2", 2, 4, 0),
                pair("a", 1, 2, 100),
                pair("b", 3, 4, 100),
            ],
        },
    )
    ok = (
        status == 200
        and body["canonical_pair_ids"] == ["a", "b"]
        and body["total_residual"] == 200
        and sorted(body["candidate_audit"]["never"]) == ["x1", "x2"]
    )
    check("cheap crossing decoy loses to cardinality", ok,
          f"status={status} body={body}")

    # 3. Empty candidate list is a legal, unique empty solution.
    status, body = http_json(
        "POST", "/audit", {"hits": hits(4), "candidates": []}
    )
    ok = (
        status == 200
        and body["solution_count"] == "1"
        and body["paired_hit_count"] == 0
        and body["canonical_pairs"] == []
        and body["unpaired_hit_ids"] == ["h1", "h2", "h3", "h4"]
    )
    check("empty candidates yield unique empty solution", ok,
          f"status={status} body={body}")

    # 4. Unknown endpoint reference: error with field path, no audit fields.
    status, body = http_json(
        "POST",
        "/audit",
        {
            "hits": hits(4),
            "candidates": [pair("x", 1, 99, 0)],
        },
    )
    ok = (
        status == 422
        and set(body.keys()) == {"error"}
        and body["error"].get("path") == "candidates[0].endpoints"
        and isinstance(body["error"].get("message"), str)
        and "solution_count" not in body
        and "canonical_pairs" not in body
    )
    check("illegal reference: field-path error without audit leakage", ok,
          f"status={status} body={body}")

    # 5. Malformed JSON is rejected cleanly over HTTP.
    request = urllib.request.Request(
        BASE_URL + "/audit",
        data=b"{not json",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            bad_status = response.status
    except urllib.error.HTTPError as exc:
        bad_status = exc.code
    check("malformed JSON rejected with 400", bad_status == 400,
          f"status={bad_status}")


def main() -> int:
    print(f"verifying track-audit service at {BASE_URL}")
    step_build_check()
    step_unit_tests()
    step_smoke_tests()

    print()
    if failures:
        print(f"VERIFY FAILED: {len(failures)} check(s): {failures}")
        return 1
    print("VERIFY OK: all build, unit-test and API/HTTP smoke checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
