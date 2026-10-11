import os
import re
import subprocess

import requests

API = "https://api.github.com"
MAX_TEXT = 2000


def _clip(text, n=MAX_TEXT):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "\n...[truncated]"


def _get(path, params=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(f"{API}{path}", headers=headers, params=params, timeout=15)
    except requests.RequestException as e:
        return None, f"Network error: {e}"
    if r.status_code == 200:
        return r.json(), None
    if r.status_code in (403, 429) and r.headers.get("X-RateLimit-Remaining") == "0":
        return None, "GitHub rate limit reached (set GITHUB_TOKEN to raise it)."
    if r.status_code == 404:
        return None, "Not found (repo may be private, or the commit was never pushed)."
    return None, f"GitHub API error {r.status_code}"


def get_remote_slug(repo_path):
    r = subprocess.run(
        ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if r.returncode != 0:
        return None
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", r.stdout.strip())
    return (m.group(1), m.group(2)) if m else None


def _discussion(owner, repo, number, is_pr, warnings):
    out = {"comments": [], "reviews": [], "review_comments": []}
    data, err = _get(f"/repos/{owner}/{repo}/issues/{number}/comments", {"per_page": 10})
    if err:
        warnings.append(f"#{number} comments: {err}")
    for c in data or []:
        out["comments"].append(f'{c["user"]["login"]}: {_clip(c["body"], 800)}')
    if is_pr:
        data, err = _get(f"/repos/{owner}/{repo}/pulls/{number}/reviews", {"per_page": 10})
        if err:
            warnings.append(f"#{number} reviews: {err}")
        for c in data or []:
            if c.get("body"):
                out["reviews"].append(f'{c["user"]["login"]} ({c["state"]}): {_clip(c["body"], 800)}')
        data, err = _get(f"/repos/{owner}/{repo}/pulls/{number}/comments", {"per_page": 10})
        if err:
            warnings.append(f"#{number} review comments: {err}")
        for c in data or []:
            out["review_comments"].append(f'{c["user"]["login"]} on {c.get("path")}: {_clip(c["body"], 800)}')
    return out


def get_github_context(repo_path, commit_sha, commit_message):
    ctx = {"available": False, "repo": None, "pull_requests": [],
           "referenced_issues": [], "warnings": [], "note": ""}

    slug = get_remote_slug(repo_path)
    if not slug:
        ctx["note"] = "No GitHub 'origin' remote detected; no PR/issue context collected."
        return ctx
    owner, repo = slug
    ctx["repo"] = f"{owner}/{repo}"

    prs, err = _get(f"/repos/{owner}/{repo}/commits/{commit_sha}/pulls", {"per_page": 5})
    if err:
        ctx["note"] = f"Could not look up PRs for this commit: {err}"
        ctx["warnings"].append(err)
        return ctx
    ctx["available"] = True

    seen = set()
    for pr in (prs or [])[:3]:
        n = pr["number"]
        seen.add(n)
        entry = {
            "number": n, "title": pr["title"], "state": pr["state"],
            "merged": bool(pr.get("merged_at")), "url": pr["html_url"],
            "body": _clip(pr.get("body")),
        }
        entry.update(_discussion(owner, repo, n, True, ctx["warnings"]))
        ctx["pull_requests"].append(entry)

    text = commit_message + "\n" + "\n".join(p["body"] for p in ctx["pull_requests"])
    refs = []
    for m in re.finditer(r"(?<![\w/])#(\d+)\b", text):
        n = int(m.group(1))
        if n not in seen and n not in refs:
            refs.append(n)
    for n in refs[:5]:
        issue, err = _get(f"/repos/{owner}/{repo}/issues/{n}")
        if err:
            ctx["warnings"].append(f"#{n}: {err}")
            continue
        is_pr = "pull_request" in issue
        entry = {
            "number": n, "title": issue["title"], "state": issue["state"],
            "kind": "pull request" if is_pr else "issue",
            "url": issue["html_url"], "body": _clip(issue.get("body")),
        }
        entry.update(_discussion(owner, repo, n, is_pr, ctx["warnings"]))
        ctx["referenced_issues"].append(entry)

    if not ctx["pull_requests"] and not ctx["referenced_issues"]:
        ctx["note"] = "Remote found, but no PR is linked to this commit and no #issue references were found."
    return ctx