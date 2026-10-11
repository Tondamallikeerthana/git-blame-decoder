"""Batch evaluation for IntentTrace.
 
1. Generate candidate lines:
     python evaluate.py --suggest demo-repo src/requests/sessions.py 8
2. Run the evaluation (calls Gemma once per case):
     python evaluate.py --cases eval_cases.json
   Options: --local (Ollama), --no-github, --delay 3
 
Outputs: eval_results.csv, EVAL_REPORT.md, eval_briefings/NN.md
The automated numbers only measure citation validity and whether quoted terms
exist in the evidence. They do not prove every claim is correct; use the
"Manual verdict" column for a human review.
"""
import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path
 
from dotenv import load_dotenv
 
from ai_briefing import format_checks, generate_briefing, investigate
 
 
def suggest(repo, file, n):
    path = Path(repo, file)
    if not path.is_file():
        sys.exit(f"File not found: {path}")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    cand = [
        i + 1 for i, l in enumerate(lines)
        if len(l.strip()) > 15 and not l.strip().startswith(("#", '"""', "'''", "//", "*"))
    ]
    if not cand:
        sys.exit("No suitable lines found in that file.")
    step = max(1, len(cand) // max(n, 1))
    picks = cand[::step][:n]
    return [{"repo": repo, "file": file, "line": ln, "code": lines[ln - 1].strip()[:80]}
            for ln in picks]
 
 
def run_case(case, backend, use_github, out_dir, idx):
    row = {
        "id": idx, "repo": case["repo"], "file": case["file"], "line": case["line"],
        "code": case.get("code", ""), "commit": "", "ceiling": "", "final_strength": "",
        "facts": 0, "facts_cited": 0, "hyp": 0, "hyp_cited": 0,
        "invalid_tags": "", "ungrounded_terms": "", "strength_capped": "",
        "timeline_len": "", "model": "", "seconds": "", "error": "",
        "manual_verdict": "", "notes": "",
    }
    t0 = time.time()
    try:
        inv = investigate(case["repo"], case["file"], int(case["line"]), use_github=use_github)
    except Exception as e:
        row["error"] = f"evidence: {e}"
        return row
    if "error" in inv:
        row["error"] = f"evidence: {inv['error']}"
        return row
 
    h = inv["history"]
    row["commit"] = h["commit"][:8]
    row["ceiling"] = inv["ceiling"]
    row["timeline_len"] = len(h.get("timeline") or [])
    try:
        model, text, rep = generate_briefing(inv, backend)
    except Exception as e:
        row["error"] = f"model: {str(e)[:200]}"
        row["seconds"] = round(time.time() - t0, 1)
        return row
 
    row.update(
        final_strength=rep["final_strength"], facts=rep["facts_total"],
        facts_cited=rep["facts_cited"], hyp=rep["hyp_total"], hyp_cited=rep["hyp_cited"],
        invalid_tags=" ".join(rep["invalid_tags"]),
        ungrounded_terms=" | ".join(rep["ungrounded"]),
        strength_capped="yes" if rep["strength_capped"] else "no",
        model=model, seconds=round(time.time() - t0, 1),
    )
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{idx:02d}.md").write_text(
        f"# Case {idx}: {h['file']}:{h['line']} ({h['commit'][:8]})\n\n"
        f"Code: `{h['code_line'].strip()}`\n\nModel: {model}\n\n{text}\n\n{format_checks(rep)}\n",
        encoding="utf-8",
    )
    return row
 
 
def write_outputs(rows):
    fields = list(rows[0].keys())
    with open("eval_results.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
 
    ok = [r for r in rows if not r["error"]]
    facts = sum(r["facts"] for r in ok)
    cited = sum(r["facts_cited"] for r in ok)
    hyp = sum(r["hyp"] for r in ok)
    hyp_cited = sum(r["hyp_cited"] for r in ok)
    dist = Counter(r["final_strength"] for r in ok)
    flagged = [r for r in ok if r["ungrounded_terms"]]
    capped = [r for r in ok if r["strength_capped"] == "yes"]
    pct = lambda a, b: f"{100 * a / b:.0f}%" if b else "n/a"
 
    md = ["# IntentTrace evaluation report", "",
          f"Cases run: {len(rows)}  |  Completed: {len(ok)}  |  Failed: {len(rows) - len(ok)}", "",
          "## Summary (automated)", "",
          f"- Verified facts with a valid evidence citation: {cited}/{facts} ({pct(cited, facts)})",
          f"- Hypotheses with a valid evidence citation: {hyp_cited}/{hyp} ({pct(hyp_cited, hyp)})",
          f"- Cases where a quoted term was not found in the evidence: {len(flagged)}/{len(ok)}",
          f"- Cases where the model's strength claim was capped by code: {len(capped)}/{len(ok)}",
          f"- Final strength distribution: " +
          (", ".join(f"{k}: {v}" for k, v in sorted(dist.items())) or "n/a"), "",
          "## Per case", "",
          "| # | File:Line | Commit | Ceiling | Final | Facts cited | Ungrounded terms | Manual verdict |",
          "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["error"]:
            md.append(f"| {r['id']} | {r['file']}:{r['line']} | - | - | FAILED | - | {r['error'][:60]} | |")
        else:
            md.append(f"| {r['id']} | {r['file']}:{r['line']} | {r['commit']} | {r['ceiling']} | "
                      f"{r['final_strength']} | {r['facts_cited']}/{r['facts']} | "
                      f"{r['ungrounded_terms'] or '-'} | |")
    md += ["", "## How to read this", "",
           "- Citation validity only checks that a cited block exists. It does not prove the block supports the claim.",
           "- 'Ungrounded terms' are identifiers, #numbers, CVE ids or hashes in the briefing that do not appear verbatim in the evidence. They are a hallucination signal, not proof.",
           "- Fill the 'Manual verdict' column yourself (correct / partly wrong / wrong) after reading eval_briefings/NN.md and the evidence.",
           ""]
    Path("EVAL_REPORT.md").write_text("\n".join(md), encoding="utf-8")
 
 
def main(argv=None):
    load_dotenv()
    ap = argparse.ArgumentParser(description="IntentTrace batch evaluation")
    ap.add_argument("--suggest", nargs=3, metavar=("REPO", "FILE", "N"),
                    help="write eval_cases.json with N evenly spaced code lines from a file")
    ap.add_argument("--cases", default="eval_cases.json")
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--no-github", action="store_true")
    ap.add_argument("--delay", type=float, default=3.0, help="seconds between cases")
    args = ap.parse_args(argv)
 
    if args.suggest:
        repo, file, n = args.suggest
        cases = suggest(repo, file, int(n))
        Path("eval_cases.json").write_text(json.dumps(cases, indent=2), encoding="utf-8")
        print(f"Wrote eval_cases.json with {len(cases)} cases:")
        for c in cases:
            print(f"  line {c['line']}: {c['code']}")
        print("Edit the file to remove weak lines (docstrings, blank-ish lines), then run:")
        print("  python evaluate.py --cases eval_cases.json")
        return
 
    path = Path(args.cases)
    if not path.is_file():
        sys.exit(f"{path} not found. Create it with --suggest first.")
    cases = json.loads(path.read_text(encoding="utf-8"))
    backend = "local" if args.local else "hosted"
    rows = []
    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['file']}:{case['line']} ...", flush=True)
        row = run_case(case, backend, not args.no_github, Path("eval_briefings"), i)
        print("   ->", row["error"] or f"{row['final_strength']} (facts {row['facts_cited']}/{row['facts']})")
        rows.append(row)
        if i < len(cases):
            time.sleep(args.delay)
    if rows:
        write_outputs(rows)
        print("Wrote eval_results.csv, EVAL_REPORT.md and eval_briefings/")
 
 
if __name__ == "__main__":
    main()
 