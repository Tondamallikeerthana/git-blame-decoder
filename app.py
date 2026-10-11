import html
import os
import re
import subprocess
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from ai_briefing import (
    SYSTEM_PROMPT,
    format_checks,
    generate_briefing,
    investigate,
    split_sections,
)

load_dotenv()
st.set_page_config(page_title="IntentTrace", layout="wide")

BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp", ".pdf", ".zip", ".gz",
    ".tar", ".whl", ".pyc", ".so", ".dll", ".exe", ".ttf", ".woff", ".woff2", ".mp3",
    ".mp4", ".mov", ".bin", ".jar", ".class", ".lock",
}
OWNER_REPO = re.compile(r"^(?:https?://github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
WINDOW = 27

CSS = """
<style>
#MainMenu, footer, header {visibility: hidden;}
.block-container {padding-top: 1.2rem; max-width: 1600px;}
.brand {display: flex; align-items: center; gap: .75rem; margin-bottom: .9rem;}
.brand .logo {width: 38px; height: 38px; border-radius: 10px; background: #7c5cff; color: #fff;
  display: flex; align-items: center; justify-content: center; font-weight: 800;}
.brand .name {font-size: 1.3rem; font-weight: 700; line-height: 1.1;}
.brand .sub {font-size: .8rem; color: #8b93a7;}
.step {display: flex; align-items: center; gap: .6rem; margin: .5rem 0 .15rem;}
.step .num {width: 22px; height: 22px; border-radius: 50%; background: #7c5cff; color: #fff;
  font-size: .75rem; font-weight: 700; display: flex; align-items: center; justify-content: center;}
.step .ttl {font-weight: 600;}
.sub-note {color: #8b93a7; font-size: .78rem; margin: 0 0 .35rem 0;}
.panel-h {font-weight: 600; margin-bottom: .5rem;}
.panel-h .muted {color: #8b93a7; font-weight: 400; font-size: .85rem;}
.code {background: #0f1220; border: 1px solid #232844; border-radius: 10px; height: 560px;
  overflow: auto; font-family: ui-monospace, Consolas, monospace; font-size: 13px; padding: 6px 0;}
.ln {display: flex; white-space: pre; line-height: 1.55; tab-size: 4;}
.ln .no {flex: 0 0 56px; text-align: right; padding-right: 14px; color: #5b6486; user-select: none;}
.ln.hl {background: #2a2466; border-left: 3px solid #7c5cff;}
.ln.hl .no {color: #c4b8ff;}
.badge {display: inline-block; padding: .2rem .7rem; border-radius: 999px; font-size: .8rem;
  font-weight: 600; margin-bottom: .4rem;}
.badge.limited {background: #3a2430; color: #ff9aa8;}
.badge.partial {background: #3a3220; color: #ffd166;}
.badge.strong {background: #173a2a; color: #5ee6a0;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------- helpers ----------

def resolve_repo(text):
    """Local folder, or GitHub owner/repo (cloned into .repos/)."""
    text = text.strip().strip('"')
    p = Path(text)
    if p.is_dir():
        return str(p), None
    m = OWNER_REPO.match(text)
    if not m:
        return None, "Not a folder, and not a GitHub 'owner/repo'."
    owner, name = m.groups()
    dest = Path(".repos") / f"{owner}__{name}"
    if dest.is_dir():
        return str(dest), None
    dest.parent.mkdir(exist_ok=True)
    try:
        r = subprocess.run(
            ["git", "clone", "--quiet", f"https://github.com/{owner}/{name}.git", str(dest)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=900, check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired:
        return None, "Clone timed out. Try a smaller repository."
    if r.returncode != 0:
        return None, f"Could not clone {owner}/{name}: {r.stderr.strip()[:200]}"
    return str(dest), None


@st.cache_data(show_spinner=False)
def list_files(repo):
    r = subprocess.run(
        ["git", "-C", repo, "-c", "core.quotepath=false", "ls-files"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if r.returncode != 0:
        return []
    return [f for f in r.stdout.splitlines() if Path(f).suffix.lower() not in BINARY_EXT]


@st.cache_data(show_spinner=False)
def read_lines(repo, rel):
    data = Path(repo, rel).read_bytes()
    if b"\x00" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace").splitlines()


def step(n, title, sub=""):
    note = f'<div class="sub-note">{sub}</div>' if sub else ""
    st.markdown(
        f'<div class="step"><span class="num">{n}</span><span class="ttl">{title}</span></div>{note}',
        unsafe_allow_html=True,
    )


def code_html(lines, start, end, hl):
    rows = []
    for i in range(start, end + 1):
        txt = html.escape(lines[i - 1]).replace("$", "&#36;")
        cls = "ln hl" if i == hl else "ln"
        rows.append(f'<div class="{cls}"><span class="no">{i}</span><span>{txt}</span></div>')
    return '<div class="code">' + "".join(rows) + "</div>"


def section_body(secs, prefix):
    for k, v in secs.items():
        if k.startswith(prefix):
            return "\n".join(v).strip() or "_(none)_"
    return "_(section missing)_"


def run_model(inv, backend):
    """Run Gemma and return a result dict. Never raises."""
    try:
        model, text, report = generate_briefing(inv, backend)
        return {"model": model, "briefing": text, "report": report,
                "error": None, "attempts": []}
    except Exception as e:
        return {"model": None, "briefing": None, "report": None,
                "error": str(e) or type(e).__name__,
                "attempts": getattr(e, "attempts", [])}


def friendly_reason(error, attempts):
    blob = error + " " + " ".join(attempts)
    if "GEMINI_API_KEY" in error or "rejected the API key" in error:
        return error
    if "Ollama" in error:
        return error
    if "429" in blob or "RESOURCE_EXHAUSTED" in blob:
        return "The model service says the rate limit was reached. Wait a minute and retry."
    if any(k in blob for k in ("500", "INTERNAL", "503", "UNAVAILABLE")):
        return "The model service returned a server error. This is often temporary."
    return f"Unexpected error: {error}"


def request_retry():
    st.session_state["do_retry"] = True


# ---------- header ----------

st.markdown(
    '<div class="brand"><div class="logo">IT</div><div>'
    '<div class="name">IntentTrace</div>'
    '<div class="sub">Context-Aware Git Blame Decoder: git blame tells you who. '
    'This tells you what is known about why, and what is not.</div></div></div>',
    unsafe_allow_html=True,
)

c1, c2, c3 = st.columns([1.05, 1.6, 1.6], gap="medium")

# ---------- left: steps ----------

repo = None
file_path = None
line = 1
lines = []

with c1:
    step(1, "Select Repository", "Local folder, or GitHub owner/repo (it will be cloned)")
    repo_text = st.text_input("Repository", value="demo-repo", label_visibility="collapsed")
    repo_err = None
    if repo_text.strip():
        with st.spinner("Preparing repository (a first clone can take a minute)..."):
            repo, repo_err = resolve_repo(repo_text)
    if repo_err:
        st.error(repo_err)

    if repo:
        files = list_files(repo)
        step(2, "Select File", "Filter, then pick a file")
        filt = st.text_input("Filter files", placeholder="e.g. sessions.py",
                             label_visibility="collapsed")
        matches = [f for f in files if filt.lower() in f.lower()][:300]
        if not matches:
            st.warning("No files match.")
        else:
            default_idx = next((i for i, f in enumerate(matches) if f.endswith(".py")), 0)
            with st.container(height=210):
                file_path = st.radio("Files", matches, index=default_idx,
                                     label_visibility="collapsed", key=f"f|{repo}|{filt}")

        if file_path:
            lines = read_lines(repo, file_path)
            if lines is None:
                st.warning("That looks like a binary file. Pick another.")
                file_path = None
            else:
                step(3, "Select Line", f"This file has {len(lines)} lines")
                line = st.number_input(
                    "Line", min_value=1, max_value=max(len(lines), 1), value=1, step=1,
                    key=f"l|{repo}|{file_path}", label_visibility="collapsed",
                )

    with st.expander("Options"):
        backend_label = st.radio("Gemma 4 backend", ["Hosted (Gemini API)", "Local (Ollama)"])
        backend = "local" if backend_label.startswith("Local") else "hosted"
        use_github = st.checkbox("Look up GitHub PRs/issues", value=True)
        ignore_text = st.text_input("Skip commits when blaming (hashes, comma-separated)")
        dry = st.checkbox("Evidence only (no Gemma call)", value=False)
        if backend == "local":
            st.success("Gemma 4 runs on your machine through Ollama. The Gemma step sends "
                       "nothing to Google.")
        else:
            st.warning("Hosted Gemma 4 runs through Google's Gemini API. The evidence packet "
                       "(diffs, file contents, PR text) leaves your machine. "
                       "Do not use on confidential code.")

    go = st.button("Generate Analysis", type="primary", use_container_width=True,
                   disabled=not file_path)

# ---------- middle: code viewer ----------

with c2:
    if file_path and lines:
        n = len(lines)
        start = max(1, min(int(line) - 13, n - WINDOW + 1))
        end = min(n, start + WINDOW - 1)
        st.markdown(
            f'<div class="panel-h">{html.escape(file_path)} '
            f'<span class="muted">- lines {start}-{end} of {n}</span></div>'
            + code_html(lines, start, end, int(line)),
            unsafe_allow_html=True,
        )
    elif file_path:
        st.info("This file is empty.")
    else:
        st.info("Choose a repository and a file on the left.")

# ---------- right: analysis ----------

with c3:
    st.markdown('<div class="panel-h">Analysis Output</div>', unsafe_allow_html=True)

    if go and file_path:
        ignore = [x.strip() for x in ignore_text.split(",") if x.strip()]
        try:
            with st.spinner("Collecting git + GitHub evidence..."):
                inv = investigate(repo, file_path, int(line), use_github=use_github,
                                  ignore_revs=ignore)
        except Exception as e:
            inv = {"error": f"Unexpected error while collecting evidence: {e}"}
        if "error" in inv:
            st.session_state.pop("res", None)
            st.error(inv["error"])
        else:
            res = {"inv": inv, "model": None, "briefing": None, "report": None,
                   "error": None, "attempts": []}
            if not dry:
                with st.spinner("Gemma 4 is analysing the evidence..."):
                    res.update(run_model(inv, backend))
            st.session_state["res"] = res

    if st.session_state.pop("do_retry", False) and st.session_state.get("res"):
        cur = st.session_state["res"]
        with st.spinner("Retrying (the Git evidence is reused)..."):
            cur.update(run_model(cur["inv"], backend))

    res = st.session_state.get("res")
    if not res:
        st.info("Pick a file and line, then press Generate Analysis.")
    else:
        inv = res["inv"]
        h = inv["history"]
        strength = res["report"]["final_strength"] if res["report"] else inv["ceiling"]
        st.markdown(f'<span class="badge {strength.lower()}">{strength} evidence</span>',
                    unsafe_allow_html=True)
        st.caption(f"{h['file']} - Line {h['line']} - Commit {h['commit'][:8]} - "
                   f"{h['author']} - {h['date'][:10]}")
        for w in h["warnings"]:
            st.warning(w)
        if h.get("reverted"):
            st.info("This commit is a revert. The reverted commit was also collected as evidence.")

        if res["error"]:
            with st.container(border=True):
                st.markdown("**AI analysis temporarily unavailable**")
                st.write(friendly_reason(res["error"], res["attempts"]))
                st.caption("Your Git and GitHub evidence is still available in the Evidence tab.")
                st.button("Retry analysis", on_click=request_retry, key="retry_btn")
                with st.expander("Troubleshoot"):
                    st.markdown(
                        "- Press **Retry**. Server errors are often temporary.\n"
                        "- Or switch to **Local (Ollama)** under Options."
                    )
                    st.code("\n".join(res["attempts"]) or res["error"], language=None)

        tab_names = ["Briefing", "Evidence"] + (["Checks"] if res["report"] else [])
        tabs = st.tabs(tab_names)

        with tabs[0]:
            if not res["briefing"]:
                if res["error"]:
                    st.write("The briefing will appear here once the analysis succeeds.")
                else:
                    st.write("Evidence-only mode: no briefing was requested.")
            else:
                st.caption(f"Model: {res['model']}")
                secs = split_sections(res["briefing"])
                for title, prefix in (
                    ("Verified facts", "verified"),
                    ("Possible intent (hypotheses)", "possible"),
                    ("Unknowns / missing evidence", "unknowns"),
                    ("Risks to investigate", "risks"),
                ):
                    with st.container(border=True):
                        st.markdown(f"**{title}**")
                        st.markdown(section_body(secs, prefix))
                with st.expander("Copy as Markdown"):
                    raw = (f"# Architectural Intent Briefing\n"
                           f"`{h['file']}:{h['line']}` - commit `{h['commit'][:10]}` - "
                           f"model `{res['model']}`\n\n{res['briefing']}\n\n"
                           f"{format_checks(res['report'])}\n")
                    st.code(raw, language="markdown")

        with tabs[1]:
            st.caption("Git and GitHub sources used for the analysis")
            for b in inv["blocks"]:
                with st.expander(f"[{b['id']}] {b['title']}", expanded=False):
                    st.code(b["text"], language="diff" if "diff" in b["title"].lower() else None)
            with st.expander("Advanced details"):
                st.markdown("**System prompt sent to Gemma**")
                st.code(SYSTEM_PROMPT, language=None)
                st.markdown("**Full evidence packet**")
                st.code(inv["evidence"], language=None)

        if res["report"]:
            with tabs[2]:
                st.caption("Citation validation")
                st.markdown(format_checks(res["report"]))