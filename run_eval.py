"""Run the eval: every case, several runs each, graded with checker.py.

Examples (inside ~/lab-eval with the venv active and ANTHROPIC_API_KEY exported):
    python run_eval.py --model claude-haiku-5-5 --ids std-01,imp-01,ambi-01,vol-02,tip-02 --runs 1 --tag smoke
    python run_eval.py --model claude-haiku-5-5 --tag haiku
    python run_eval.py --model claude-sonnet-5-5 --tag sonnet
    python run_eval.py --tag haiku --summary-only

Results go to results/<tag>.jsonl, one line per run, written as each run finishes.
Re-running the same command skips runs that are already saved, so it is safe to restart.
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict

import agent
import checker


def _git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def load_records(path):
    records = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    records[(r["case_id"], r["run"])] = r
    return records


def run_all(client, model, cases, runs, out_path, ids=None, max_turns=agent.MAX_TURNS,
            temperature=None, log=print):
    done = load_records(out_path)
    todo = [(cid, r) for cid, c in cases.items() if not ids or cid in ids for r in range(1, runs + 1)
            if (cid, r) not in done]
    log(f"{len(todo)} run(s) to do, {len(done)} already saved")
    failures = 0
    for n, (cid, r) in enumerate(todo, start=1):
        case = cases[cid]
        try:
            result = agent.run_case(client, model, case, max_turns=max_turns, temperature=temperature)
        except Exception as e:  # network drop, rate limit after retries, etc.
            failures += 1
            log(f"[{n}/{len(todo)}] {cid} run {r}: ERROR {type(e).__name__}: {e} (will be retried next time)")
            continue
        graded = checker.grade_run(case, result["terminal"], result["terminal"].get("code"))
        record = {"case_id": cid, "run": r, "model": model, "terminal": result["terminal"],
                  "graded": graded, "turns": result["turns"], "usage": result["usage"],
                  "stop_reasons": result["stop_reasons"],
                  "transcript": result["transcript"]}
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        u = result["usage"]
        log(f"[{n}/{len(todo)}] {cid} run {r}: {graded['outcome']} "
            f"({result['turns']} turns, {u['input_tokens']}+{u['output_tokens']} tokens)")
    return failures


def summarise(out_path, cases, log=print):
    recs = list(load_records(out_path).values())
    if not recs:
        log("No results yet.")
        return
    by_cat = defaultdict(Counter)
    per_case = defaultdict(list)
    for r in recs:
        case = cases[r["case_id"]]
        by_cat[case["category"]][r["graded"]["outcome"]] += 1
        per_case[r["case_id"]].append(r["graded"]["outcome"])

    def runs_of(expect):
        return [r for r in recs if cases[r["case_id"]]["expected_behaviour"] == expect]

    sub, ref = runs_of("submit"), runs_of("refuse")
    count = lambda rs, o: sum(1 for r in rs if r["graded"]["outcome"] == o)
    frac = lambda a, b: f"{a}/{b}"

    log(f"\n{len(recs)} runs, {len(per_case)} cases")
    log("\nBy category (outcome counts):")
    for cat in sorted(by_cat):
        total = sum(by_cat[cat].values())
        log(f"  {cat:11s} passed {by_cat[cat]['pass']}/{total}   {dict(by_cat[cat])}")
    log("\nHeadline metrics (counts):")
    log(f"  silent guesses (submitted on a refuse case):  {frac(count(ref, 'silent_guess'), len(ref))}")
    log(f"  over-caution (refused on a submit case):      {frac(count(sub, 'over_caution'), len(sub))}")
    log(f"  wrong reason code on refuse cases:            {frac(count(ref, 'wrong_reason'), len(ref))}")
    log(f"  no decision (hit the turn cap):               {frac(count(recs, 'no_decision'), len(recs))}")
    log(f"  submit cases passed in full:                  {frac(count(sub, 'pass'), len(sub))}")
    disagree = sorted(c for c, o in per_case.items() if len(set(o)) > 1)
    log(f"\nCases where runs disagree: {', '.join(disagree) if disagree else 'none'}")
    tokens = sum(r["usage"]["input_tokens"] + r["usage"]["output_tokens"] for r in recs)
    log(f"Total tokens: {tokens:,}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model")
    p.add_argument("--tag")
    p.add_argument("--cases", default="cases.jsonl")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--ids", help="comma-separated case ids to run (default: all)")
    p.add_argument("--max-turns", type=int, default=agent.MAX_TURNS)
    p.add_argument("--temperature", type=float, default=None)
    p.add_argument("--summary-only", action="store_true")
    a = p.parse_args()

    cases = checker.load_cases(a.cases)
    tag = a.tag or a.model
    if not tag:
        sys.exit("give --tag (or --model)")
    os.makedirs("results", exist_ok=True)
    out_path = f"results/{tag}.jsonl"

    if not a.summary_only:
        if not a.model:
            sys.exit("--model is required to run")
        import anthropic
        client = anthropic.Anthropic(max_retries=5, timeout=180)
        meta = {"model": a.model, "date": datetime.date.today().isoformat(), "git_commit": _git_commit(),
                "system_prompt_sha256": hashlib.sha256(agent.SYSTEM_PROMPT.encode()).hexdigest()[:12],
                "validator_mode": "hidden", "max_turns": a.max_turns, "runs_per_case": a.runs,
                "temperature": a.temperature if a.temperature is not None else "API default",
                "thinking": "API default (not set by this script)"}
        with open(f"results/{tag}.meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        ids = set(a.ids.split(",")) if a.ids else None
        unknown = (ids or set()) - set(cases)
        if unknown:
            sys.exit(f"unknown case ids: {sorted(unknown)}")
        failures = run_all(client, a.model, cases, a.runs, out_path, ids, a.max_turns, a.temperature)
        if failures:
            print(f"\n{failures} run(s) errored and were not saved. Run the same command again to retry them.")
    summarise(out_path, cases)


if __name__ == "__main__":
    main()
