import argparse
import os
import re
import sys

from dotenv import load_dotenv

from decoder import get_line_history
from github_context import get_github_context

PRIMARY_MODEL = "gemma-4-31b-it"
FALLBACK_MODEL = "gemma-4-26b-a4b-it"
STRENGTH_ORDER = {"Limited": 0, "Partial": 1, "Strong": 2}

SYSTEM_PROMPT = """You are a code-archaeology assistant. You write an "Architectural Intent Briefing"
about ONE line of code, using ONLY the evidence packet provided.

The packet is split into blocks labelled [E1], [E2], ... Hard rules:
- Every bullet under "Verified facts" MUST end with the tag(s) of the block(s) that prove it, e.g. [E2] or [E2, E3].
  If you cannot cite a block, it is not a verified fact.
- Every bullet under "Possible intent (hypotheses)" must start with "Hypothesis:" and end with the tags it is based on.
- Only use tags that appear in the packet's "Valid evidence tags" line. Never invent tags.
- Never invent developer intentions, bugs, PRs, issues, people, or discussions.
- Anything not literally stated in the evidence is NOT a fact.
- Never say code is safe to remove or change. List risks to investigate instead.
- If evidence is thin, say so plainly. A short honest briefing beats a speculative long one.
- If an "Automatic warnings" block exists, mention it under Unknowns (the blame commit may not be the origin of the logic).
- If the blame commit is a revert, the reverted commit's message/discussion is given in separate blocks. The reason the original feature was added is NOT automatically the reason it was reverted; say so.
- Evidence strength must not exceed the "Maximum allowed evidence strength" in the packet.
- "Strong" requires evidence that explains why THIS line/change exists. A PR describing a whole feature, or a missing diff, caps it at "Partial".
- The "LINE CONTENT" field is the exact line being investigated. Do not claim it is truncated.

Output exactly these Markdown sections:
## Verified facts
## Possible intent (hypotheses)
## Unknowns / missing evidence
## Risks to investigate before changing this code
## Evidence strength
(Strong, Partial, or Limited, with a one-sentence justification.)
"""


# ---------- evidence strength ceiling ----------

def _body_lines(message):
    lines = [l.strip() for l in message.splitlines()][1:]  # skip subject
    skip = re.compile(r"^(This reverts commit|Signed-off-by:|Co-authored-by:)", re.I)
    return [l for l in lines if l and not skip.match(l)]


def _has_text(items):
    return any(
        p.get("body") or p.get("comments") or p.get("reviews") or p.get("review_comments")
        for p in items
    )


def evidence_ceiling(history, gh, gh_rev=None):
    """Deterministic cap so the model can't over-claim."""
    items = gh.get("pull_requests", []) + gh.get("referenced_issues", [])
    diff = history["diff"].strip()
    diff_ok = bool(diff) and not diff.startswith("Diff unavailable")

    rev_evidence = False
    rev = history.get("reverted")
    if rev and "error" not in rev and gh_rev is not None:
        rev_items = gh_rev.get("pull_requests", []) + gh_rev.get("referenced_issues", [])
        rev_evidence = bool(_body_lines(rev["message"])) or _has_text(rev_items)
    elif rev and "error" not in rev:
        rev_evidence = bool(_body_lines(rev["message"]))

    if _has_text(items) and diff_ok and not history.get("warnings"):
        return "Strong"
    if _has_text(items) or items or _body_lines(history["message"]) or rev_evidence:
        return "Partial"
    return "Limited"


# ---------- evidence blocks ----------

def _clip(text, n):
    return text if len(text) <= n else text[:n] + "\n...[truncated]"


def _fmt_item(it):
    kind = it.get("kind", "pull request")
    out = (f"#{it['number']} [{kind}] {it['title']} ({it['state']})\n"
           f"URL: {it['url']}\nBody: {it.get('body') or '(empty)'}\n")
    for key in ("comments", "reviews", "review_comments"):
        for c in it.get(key, []):
            out += f"{key}: {c}\n"
    return out


def _gh_blocks(add, gh, label):
    add(f"GitHub lookup status ({label} commit)",
        f"Repo: {gh.get('repo')}\nNote: {gh.get('note') or 'none'}\n"
        f"Warnings: {gh.get('warnings') or 'none'}")
    for it in gh.get("pull_requests", []):
        add(f"GitHub pull request linked to the {label} commit", _fmt_item(it))
    for it in gh.get("referenced_issues", []):
        add(f"GitHub item merely referenced by number ({label} commit; relationship NOT verified)",
            _fmt_item(it))


def build_blocks(history, gh, gh_rev):
    blocks = []

    def add(title, text):
        blocks.append({"id": f"E{len(blocks) + 1}", "title": title, "text": text})

    blame_text = (f"{history['blame']}\nLINE CONTENT: {history['code_line']}\n"
                  f"FILE PATH AT THAT COMMIT: {history['file_at_commit']} "
                  f"(line {history['line_at_commit']})")
    if history.get("ignored_revs"):
        blame_text += f"\nBlame ignored revisions: {', '.join(history['ignored_revs'])}"
    add("Git blame result", blame_text)

    add("Commit metadata and message",
        f"Commit: {history['commit']}\nAuthor: {history['author']}\n"
        f"Date: {history['date']}\nFiles changed in commit: {history['files_changed']}\n"
        f"Message:\n{history['message']}")
    add("Diff of this commit for this file", _clip(history["diff"], 8000))
    add("File before this commit", _clip(history["previous_version"], 5000))
    add("File at this commit", _clip(history["version_at_commit"], 5000))

    if history.get("warnings"):
        add("Automatic warnings about this commit",
            "\n".join(f"- {w}" for w in history["warnings"]))

    rev = history.get("reverted")
    if rev:
        if "error" in rev:
            add("Reverted commit (unavailable)", f"{rev['commit']}: {rev['error']}")
        else:
            add("Commit that this commit REVERTED",
                f"Commit: {rev['commit']}\nAuthor: {rev['author']}\n"
                f"Date: {rev['date']}\nMessage:\n{rev['message']}")

    _gh_blocks(add, gh, "main")
    if gh_rev is not None:
        _gh_blocks(add, gh_rev, "reverted")
    return blocks


def render_evidence(blocks, ceiling, history):
    head = (f"Maximum allowed evidence strength: {ceiling}\n"
            f"Valid evidence tags: {', '.join(b['id'] for b in blocks)}\n"
            f"TARGET: {history['file']} line {history['line']}\n")
    body = "\n".join(f"[{b['id']}] {b['title']}\n{b['text']}\n" for b in blocks)
    return head + "\n" + body


def investigate(repo, file, line, use_github=True, ignore_revs=None):
    history = get_line_history(repo, file, line, ignore_revs=ignore_revs)
    if "error" in history:
        return {"error": history["error"]}

    if use_github:
        gh = get_github_context(repo, history["commit"], history["message"])
    else:
        gh = {"note": "GitHub lookup disabled by user.", "pull_requests": [],
              "referenced_issues": [], "warnings": [], "repo": None}

    gh_rev = None
    rev = history.get("reverted")
    if use_github and rev and "error" not in rev:
        gh_rev = get_github_context(repo, rev["commit"], rev["message"])

    ceiling = evidence_ceiling(history, gh, gh_rev)
    blocks = build_blocks(history, gh, gh_rev)
    return {
        "history": history, "gh": gh, "gh_rev": gh_rev, "ceiling": ceiling,
        "blocks": blocks, "evidence": render_evidence(blocks, ceiling, history),
    }


# ---------- Gemma call ----------

def call_gemma(evidence):
    from google import genai
    from google.genai import types

    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing. Put it in .env (see README).")
    client = genai.Client(api_key=key)
    models = [os.getenv("GEMMA_MODEL", PRIMARY_MODEL)]
    if FALLBACK_MODEL not in models:
        models.append(FALLBACK_MODEL)

    last_err = None
    for model in models:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=evidence,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.2,
                    thinking_config=types.ThinkingConfig(
                        thinking_level=os.getenv("GEMMA_THINKING", "high")
                    ),
                ),
            )
            text = (resp.text or "").strip()
            if not text:
                raise RuntimeError("Model returned an empty response.")
            return model, text
        except Exception as e:  # try the next model
            last_err = f"{model}: {e}"
            print(f"[warn] {last_err}", file=sys.stderr)
    raise RuntimeError(f"All Gemma models failed. Last error: {last_err}")


# ---------- automated checks on the model output ----------

BULLET_RE = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+")


def split_sections(markdown):
    sections, current = {}, None
    for line in markdown.splitlines():
        if line.startswith("## "):
            current = line[3:].strip().lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _section(sections, prefix):
    for k, v in sections.items():
        if k.startswith(prefix):
            return v
    return []


def _bullets(lines):
    items = []
    for line in lines:
        if BULLET_RE.match(line):
            items.append(BULLET_RE.sub("", line, count=1).strip())
        elif line.strip() and items:
            items[-1] += " " + line.strip()
    return items


def _tags(text):
    found = set()
    for chunk in re.findall(r"\[([^\]]*)\]", text):
        found.update(re.findall(r"\bE\d+\b", chunk))
    return found


def check_citations(briefing, valid_ids, ceiling):
    valid = set(valid_ids)
    sections = split_sections(briefing)
    report = {
        "facts_total": 0, "facts_cited": 0, "uncited_facts": [],
        "hyp_total": 0, "hyp_cited": 0, "invalid_tags": [],
        "claimed_strength": None, "final_strength": "Limited", "strength_capped": False,
    }
    bad = set()
    for prefix, name in (("verified", "facts"), ("possible", "hyp")):
        for b in _bullets(_section(sections, prefix)):
            tags = _tags(b)
            invalid = tags - valid
            bad |= invalid
            report[f"{name}_total"] += 1
            if tags and not invalid:
                report[f"{name}_cited"] += 1
            elif name == "facts":
                report["uncited_facts"].append(b)
    report["invalid_tags"] = sorted(bad)

    text = " ".join(_section(sections, "evidence strength"))
    m = re.search(r"\b(Strong|Partial|Limited)\b", text)
    if m:
        claimed = m.group(1)
        report["claimed_strength"] = claimed
        if STRENGTH_ORDER[claimed] > STRENGTH_ORDER[ceiling]:
            report["final_strength"] = ceiling
            report["strength_capped"] = True
        else:
            report["final_strength"] = claimed
    return report


def format_checks(report):
    lines = [
        "## Automated checks",
        f"- Verified facts with valid evidence citations: "
        f"{report['facts_cited']}/{report['facts_total']}",
        f"- Hypotheses with valid evidence citations: "
        f"{report['hyp_cited']}/{report['hyp_total']}",
    ]
    if report["invalid_tags"]:
        lines.append(f"- Citations to evidence blocks that do not exist: {', '.join(report['invalid_tags'])}")
    for f in report["uncited_facts"]:
        lines.append(f"- UNCITED/INVALID fact (treat with suspicion): {f}")
    if report["claimed_strength"] is None:
        lines.append("- The model gave no recognisable evidence strength; treated as Limited.")
    elif report["strength_capped"]:
        lines.append(f"- Model claimed {report['claimed_strength']} but was capped to "
                     f"{report['final_strength']} by the evidence ceiling.")
    else:
        lines.append(f"- Final evidence strength: {report['final_strength']}")
    return "\n".join(lines)


def generate_briefing(inv):
    model, text = call_gemma(inv["evidence"])
    report = check_citations(text, [b["id"] for b in inv["blocks"]], inv["ceiling"])
    return model, text, report


# ---------- CLI ----------

def main():
    load_dotenv()
    ap = argparse.ArgumentParser(description="Context-Aware Git Blame Decoder")
    ap.add_argument("repo")
    ap.add_argument("file")
    ap.add_argument("line", type=int)
    ap.add_argument("--no-github", action="store_true", help="skip GitHub PR/issue lookup")
    ap.add_argument("--ignore-rev", action="append", default=[],
                    help="commit to skip in blame (repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="print the evidence packet, don't call the API")
    ap.add_argument("--out", help="save the briefing to this Markdown file")
    args = ap.parse_args()

    inv = investigate(args.repo, args.file, args.line,
                      use_github=not args.no_github, ignore_revs=args.ignore_rev)
    if "error" in inv:
        sys.exit(f"Error: {inv['error']}")

    if args.dry_run:
        print(inv["evidence"])
        return

    print("NOTE: the evidence packet (diffs, file contents, PR text) is sent to Google's hosted "
          "Gemini API. Do not use on confidential code.\n", file=sys.stderr)
    try:
        model, briefing, report = generate_briefing(inv)
    except RuntimeError as e:
        sys.exit(f"Error: {e}")

    h = inv["history"]
    result = (f"# Architectural Intent Briefing\n"
              f"`{h['file']}:{h['line']}` — commit `{h['commit'][:10]}` — model `{model}` — "
              f"evidence ceiling `{inv['ceiling']}`\n\n"
              f"{briefing}\n\n{format_checks(report)}\n")
    print(result)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(result)
        print(f"Saved to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()