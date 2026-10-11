import re
import subprocess
from pathlib import Path

BULK_FILE_THRESHOLD = 20
BULK_WORDS = re.compile(
    r"\b(format|formatting|reformat|black|prettier|lint|linting|whitespace|"
    r"rename|renamed|autopep8|isort)\b",
    re.I,
)
REVERT_RE = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.I)


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _parse_commit(stdout):
    parts = stdout.rstrip("\n").split("\n", 3)
    parts += [""] * (4 - len(parts))
    return parts[0], parts[1], parts[2], parts[3].strip()


def get_line_history(repo_path, file_path, line_number, ignore_revs=None):
    repo = Path(repo_path).resolve()
    file = Path(file_path)

    if not repo.is_dir():
        return {"error": f"Repository not found: {repo}"}

    if not file.is_absolute():
        file = repo / file

    try:
        relative_file = file.resolve().relative_to(repo).as_posix()
    except ValueError:
        return {"error": "The file must be inside the repository."}

    if not file.is_file():
        return {"error": f"File not found: {relative_file}"}

    if line_number < 1:
        return {"error": "Line number must be 1 or greater."}

    # --porcelain gives the ORIGINAL file name/line, which survives renames
    range_args = ["-L", f"{line_number},{line_number}", "--", relative_file]
    blame_args = ["blame", "--porcelain"]
    ignored = []
    ignore_file = repo / ".git-blame-ignore-revs"
    if ignore_file.is_file():
        blame_args += ["--ignore-revs-file", str(ignore_file)]
        ignored.append(".git-blame-ignore-revs")
    for rev in ignore_revs or []:
        blame_args += ["--ignore-rev", rev]
        ignored.append(rev)

    result = _git(repo, *blame_args, *range_args)
    if result.returncode != 0 and ignored:
        # bad/unknown ignore revs (e.g. shallow clone): retry plain blame
        ignored = []
        result = _git(repo, "blame", "--porcelain", *range_args)

    if result.returncode != 0:
        return {"error": result.stderr.strip() or "Git blame failed."}

    plines = result.stdout.splitlines()
    if not plines:
        return {"error": "No history found for this line."}

    head = plines[0].split()
    commit = head[0]
    if set(commit) == {"0"}:
        return {"error": "This line has uncommitted changes; no history yet."}
    line_at_commit = int(head[1])

    commit_file = next(
        (l[len("filename "):] for l in plines if l.startswith("filename ")),
        relative_file,
    )
    prev = next((l for l in plines if l.startswith("previous ")), None)
    previous_file = prev.split(" ", 2)[2] if prev else commit_file
    code_line = next((l[1:] for l in plines if l.startswith("\t")), "")
    output = f"{commit[:8]}  {commit_file}:{line_at_commit}  | {code_line}"

    details = _git(repo, "show", "-s", "--format=%H%n%an%n%aI%n%B", commit)
    if details.returncode != 0:
        return {"error": details.stderr.strip() or "Could not read commit."}

    full_hash, author, date, message = _parse_commit(details.stdout)

    # Diff covers both the new and old path so renamed files still show changes
    diff_paths = [commit_file]
    if previous_file != commit_file:
        diff_paths.append(previous_file)
    diff_result = _git(
        repo, "show", "--format=", "--no-ext-diff",
        "--no-renames", "--unified=3", full_hash, "--", *diff_paths,
    )
    diff = (
        diff_result.stdout
        if diff_result.returncode == 0
        else "Diff unavailable: " + diff_result.stderr.strip()
    )

    current_version_result = _git(repo, "show", f"{full_hash}:{commit_file}")
    version_at_commit = (
        current_version_result.stdout
        if current_version_result.returncode == 0
        else "Version unavailable: " + current_version_result.stderr.strip()
    )

    previous_version_result = _git(repo, "show", f"{full_hash}^:{previous_file}")
    previous_version = (
        previous_version_result.stdout
        if previous_version_result.returncode == 0
        else "No previous version found for this file."
    )

    # Bulk-change detection (formatting / rename / giant commits)
    names = _git(repo, "show", "--name-only", "--format=", full_hash)
    files_changed = (
        len([l for l in names.stdout.splitlines() if l.strip()])
        if names.returncode == 0 else 0
    )
    subject = message.splitlines()[0] if message else ""
    warnings = []
    if files_changed >= BULK_FILE_THRESHOLD:
        warnings.append(
            f"This commit touched {files_changed} files, so it may be a bulk change "
            "rather than the real origin of this line."
        )
    if BULK_WORDS.search(subject):
        warnings.append(
            "The commit subject suggests formatting, linting or renaming, so it may "
            "not explain why this line exists."
        )

    # Follow reverts: the reasoning usually lives in the reverted commit
    reverted = None
    m = REVERT_RE.search(message)
    if m:
        rev_hash = m.group(1)
        info = _git(repo, "show", "-s", "--format=%H%n%an%n%aI%n%B", rev_hash)
        if info.returncode == 0:
            h, a, d, msg = _parse_commit(info.stdout)
            reverted = {"commit": h, "author": a, "date": d, "message": msg}
        else:
            reverted = {
                "commit": rev_hash,
                "error": "Reverted commit not available locally (shallow clone?).",
            }

    return {
        "file": relative_file,
        "line": line_number,
        "blame": output,
        "commit": full_hash,
        "author": author,
        "date": date,
        "message": message,
        "diff": diff,
        "previous_version": previous_version,
        "version_at_commit": version_at_commit,
        "file_at_commit": commit_file,
        "line_at_commit": line_at_commit,
        "code_line": code_line,
        "files_changed": files_changed,
        "warnings": warnings,
        "reverted": reverted,
        "ignored_revs": ignored,
    }


if __name__ == "__main__":
    repo_path = input("Path to Git repository: ").strip().strip('"')
    file_path = input("File path (relative to repository): ").strip()
    line_number = int(input("Line number: ").strip())

    result = get_line_history(repo_path, file_path, line_number)

    if "error" in result:
        print("\nError:", result["error"])
    else:
        print("\n--- LINE HISTORY ---")
        for key, value in result.items():
            print(f"{key}: {value}")
