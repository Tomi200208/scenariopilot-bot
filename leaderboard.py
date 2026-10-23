"""Daily leaderboard snapshots and our "as if it started now" virtual leaderboard.

The owner's goal is to judge the season from our entry day: points each bot earned since then (spot peer score sum
now minus at entry), among bots that covered at least 80% of the questions scored since then. The first snapshot
on or after the entry day is the baseline.

Run: python leaderboard.py snapshot              (saves snapshots/YYYY-MM-DD.json; one per day)
     python leaderboard.py virtual <entry-date>   (prints the virtual leaderboard since that day)
Environment: METACULUS_TOKEN (any bot or user token can read the leaderboard).
"""
import glob
import json
import os
import sys
import urllib.request
from datetime import date

from dotenv import load_dotenv

PROJECT = 33121  # Fall 2026 FutureEval bot tournament
URL = "https://www.metaculus.com/api/leaderboards/project/%d/" % PROJECT
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots")
OURS = {"ScenarioPilot1": "us, primary", "ScenarioPilot2": "us, control"}


def snapshot():
    load_dotenv()
    req = urllib.request.Request(URL, headers={"Authorization": "Token " + os.environ["METACULUS_TOKEN"], "User-Agent": "scenariopilot-bot"})
    board = next(b for b in json.loads(urllib.request.urlopen(req, timeout=60).read()) if b.get("is_primary_leaderboard"))
    rows = {}
    for e in board["entries"]:  # aggregate rows (e.g. the bots' median) have no user: keep them as benchmarks
        name = e["user"]["username"] if e.get("user") else "%s %s" % (AGG, e.get("aggregation_method"))
        rows[name] = {"score": e["score"], "coverage": e["coverage"], "count": e["contribution_count"]}
    os.makedirs(DIR, exist_ok=True)
    path = os.path.join(DIR, date.today().isoformat() + ".json")
    if os.path.exists(path):  # the day's first capture is the baseline; a later run must not move it
        print("kept the first capture of today:", path)
        return
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False)
    os.replace(path + ".tmp", path)
    print("saved %s: %d bots" % (path, len(rows)))


AGG = "[aggregate]"


def virtual(entry, rows_then, rows_now, min_share=0.8):
    """Bots ranked by points since entry, among those covering >= min_share of the questions scored since then;
    returns (ranked [(rank, name, points, coverage)], benchmarks [(name, points, coverage)]).

    The aggregate rows forecast every question, so their coverage growth is the number of questions scored since
    entry; without them the most-covered bot stands in."""
    delta = {n: (r["score"] - rows_then.get(n, {}).get("score", 0.0), r["coverage"] - rows_then.get(n, {}).get("coverage", 0))
             for n, r in rows_now.items()}
    bench = [(n, s, c) for n, (s, c) in delta.items() if n.startswith(AGG)]
    total = max((c for _, _, c in bench), default=0) or max((c for _, c in delta.values()), default=0)
    kept = sorted(((s, n, c) for n, (s, c) in delta.items() if not n.startswith(AGG) and total and c >= min_share * total), reverse=True)
    return [(i + 1, n, s, c) for i, (s, n, c) in enumerate(kept)], bench


def show(entry):
    files = sorted(glob.glob(os.path.join(DIR, "*.json")))
    base = next((f for f in files if os.path.basename(f)[:10] >= entry), None)
    if base is None or base == files[-1]:
        raise SystemExit("need a snapshot on/after %s and a later one" % entry)
    then, now = (json.load(open(f, encoding="utf-8")) for f in (base, files[-1]))
    print("virtual leaderboard since %s (%s -> %s)" % (entry, os.path.basename(base)[:10], os.path.basename(files[-1])[:10]))
    table, bench = virtual(entry, then, now)
    for rank, name, pts, cov in table:
        if rank <= 10 or name in OURS:
            print("%4d  %-28s %+8.1f points over %5.1f question-coverage%s" % (rank, name, pts, cov, "   <- " + OURS[name] if name in OURS else ""))
    for name, pts, cov in bench:
        print("   -  %-28s %+8.1f points over %5.1f question-coverage (benchmark)" % (name, pts, cov))


if __name__ == "__main__":
    then = {"a": {"score": 10, "coverage": 2}, "b": {"score": 0, "coverage": 0}, AGG + " m": {"score": 0, "coverage": 2}}
    now = {"a": {"score": 15, "coverage": 12}, "b": {"score": 9, "coverage": 10}, "c": {"score": 50, "coverage": 3},
           AGG + " m": {"score": 1, "coverage": 12}}
    ranked, bench = virtual("x", then, now)
    assert [r[1] for r in ranked] == ["b", "a"]  # c covered too few of the 10 scored questions; b earned more since entry
    assert [b[0] for b in bench] == [AGG + " m"]  # aggregates are benchmarks, not ranked
    if len(sys.argv) >= 2 and sys.argv[1] == "snapshot":
        snapshot()
    elif len(sys.argv) == 3 and sys.argv[1] == "virtual":
        show(sys.argv[2])
    else:
        raise SystemExit(__doc__)
