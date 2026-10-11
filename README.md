# IntentTrace

IntentTrace is a context-aware Git blame decoder. It starts with a line in a Git repository, gathers the surrounding history and related GitHub context, then produces an evidence-cited briefing about what is known and what remains uncertain about that line.

## What It Does

- Opens a local Git repository or clones a public GitHub repository.
- Lets you choose a tracked file and line to investigate.
- Collects blame, commit metadata, the relevant diff, file versions, rename information, and revert context.
- Optionally looks up linked pull requests, issues, reviews, and comments through the GitHub API.
- Generates a structured briefing with verified facts, hypotheses, unknowns, risks, and evidence strength.
- Checks model citations and caps the final evidence strength when the available evidence is limited.
- Supports the hosted Gemini API or a local Ollama model.

## Requirements

- Python 3.10 or newer.
- Git installed and available on your `PATH`.
- A Gemini API key for hosted AI analysis, or Ollama installed with a compatible model for local analysis.
- Internet access to clone public repositories and, when enabled, query the GitHub API.

## Run Locally

### 1. Get the code

```powershell
git clone https://github.com/Tondamallikeerthana/git-blame-decoder.git
cd git-blame-decoder
```

### 2. Create and activate a virtual environment

Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Python packages

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 4. Configure credentials

Create a `.env` file in the project root for local development:

```dotenv
GEMINI_API_KEY=your-gemini-api-key
GITHUB_TOKEN=your-github-token
```

`GEMINI_API_KEY` is needed for hosted AI analysis. `GITHUB_TOKEN` is optional; it raises the GitHub API rate limit and helps retrieve GitHub context. Use a token with only the access you need. The `.env` file is ignored by Git. Never commit credentials or share them in screenshots, issues, or chat.

### 5. Start the app

```bash
streamlit run app.py
```

Streamlit prints a local URL, usually `http://localhost:8501`. Open it in your browser.

### 6. Analyze a line

Enter a local repository path or a public GitHub repository such as `psf/requests`. Select a file and line, then choose **Generate Analysis**. Hosted Gemini is the default backend. The first clone of a repository can take a while; later runs use the local clone.

The app's initial repository value is `demo-repo`, which is a local example checkout and is intentionally not included in this GitHub repository. After cloning this project, enter a repository you have locally or a public `owner/repo` instead.

## Deploy on Streamlit Community Cloud

1. Sign in to [Streamlit Community Cloud](https://share.streamlit.io/) with the GitHub account that can access this repository.
2. Select **Create app** and choose:
   - Repository: `Tondamallikeerthana/git-blame-decoder`
   - Branch: `main`
   - Main file path: `app.py`
3. In **Advanced settings** (or the deployed app's **Settings → Secrets**), add secrets in TOML format:

   ```toml
   GEMINI_API_KEY = "your-gemini-api-key"
   GITHUB_TOKEN = "your-github-token"
   ```

   `GITHUB_TOKEN` is optional. Add secrets as top-level entries, not beneath a TOML section. Streamlit exposes top-level secrets as environment variables, which is how this app reads them. Do not upload or commit your `.env` file.

4. Deploy the app. If you change secrets later, save them and reboot the app.
5. In the deployed app, replace the default `demo-repo` value with a public GitHub repository, for example `psf/requests`.
6. Select a file and line and generate an analysis. Push future code changes to `main`; Community Cloud will redeploy from that branch.

The app clones GitHub repositories over HTTPS without authentication, so repository cloning is for public repositories. The hosted Gemini option works in the cloud. The local Ollama option expects an Ollama server reachable from the app and will not find Ollama running on your personal computer from Streamlit Cloud.

## Command-Line Usage

The command-line tool analyzes a repository already cloned on your machine:

```bash
git clone https://github.com/psf/requests.git demo-repo
python ai_briefing.py demo-repo src/requests/sessions.py 158
```

The command prints the briefing. Add `--out briefing.md` to save it to a file.

Available options:

| Option | Description |
| --- | --- |
| `--no-github` | Skip GitHub pull request and issue lookups. |
| `--ignore-rev HASH` | Ignore a revision during blame; repeat the option to ignore multiple revisions. |
| `--dry-run` | Print the collected evidence packet without calling a model. |
| `--local` | Generate the briefing with Ollama instead of the hosted Gemini API. |
| `--out PATH` | Save the briefing to a file. |

Example using Ollama:

```bash
ollama pull gemma4:e4b
python ai_briefing.py demo-repo src/requests/sessions.py 158 --local
```

The default Ollama model is `gemma4:e4b`. You can set `OLLAMA_MODEL` or `OLLAMA_HOST` in `.env` to use a different model or server. For hosted Gemini, optional settings are `GEMMA_MODEL` and `GEMMA_THINKING` (default: `gemma-4-31b-it` and `high`).

## Configuration Reference

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `GEMINI_API_KEY` | For hosted analysis | None | Authenticates requests to Google's Gemini API. |
| `GITHUB_TOKEN` | No | None | Authenticates GitHub API lookups and raises the rate limit. |
| `GEMMA_MODEL` | No | `gemma-4-31b-it` | Hosted Gemini model identifier. |
| `GEMMA_THINKING` | No | `high` | Hosted model thinking level; `minimal` may reduce latency. |
| `OLLAMA_MODEL` | For custom local model | `gemma4:e4b` | Model name used by Ollama. |
| `OLLAMA_HOST` | No | `http://localhost:11434` | Ollama server URL. |

## Privacy Notes

- With **Hosted (Gemini API)**, the evidence packet, which can include source excerpts, diffs, commit messages, and GitHub discussion text, is sent to Google's Gemini API. Do not use hosted analysis for confidential code unless your organization's policies allow it.
- **Local (Ollama)** sends the model request to the configured Ollama server. With Ollama running on your own machine, the model request stays local.
- GitHub lookups use the GitHub API. Without a token, public unauthenticated API rate limits apply.
- Keep API keys and tokens out of the repository. If a credential is exposed, revoke it and create a replacement.

## Troubleshooting

### `GEMINI_API_KEY is missing`

For local use, check that `.env` is in the project root and contains `GEMINI_API_KEY=...`. For Streamlit Community Cloud, add `GEMINI_API_KEY = "..."` under the app's **Settings → Secrets**, save, and reboot the app.

### Gemini rejects the key or reports a quota/rate limit

Check that the key is active and has Gemini API access. Review the provider's quota, wait, or try a supported model with `GEMMA_MODEL`. `GEMMA_THINKING=minimal` can reduce reasoning time, with a possible quality tradeoff.

### A repository cannot be cloned

Check the repository spelling and network access. The current app clones public GitHub repositories; private repository cloning is not configured. A first clone of a large repository may take time.

### GitHub context is missing or rate-limited

The repository needs a GitHub `origin` remote for PR/issue lookup. Add a `GITHUB_TOKEN` to local `.env` or Streamlit Cloud Secrets to raise the API rate limit. Git blame and commit evidence can still be collected when GitHub lookups are unavailable.

### Ollama cannot be reached

Start Ollama, pull the configured model, and check `OLLAMA_HOST`. On Streamlit Community Cloud, `localhost` refers to the cloud app container, not your own computer.

### Evidence strength is Limited

This is a conservative result, not necessarily an error. It means the available commit message, diff, or linked discussion does not strongly establish why the line was introduced.

## Project Files

- `app.py` — Streamlit user interface.
- `decoder.py` — Git blame and commit-history collection.
- `github_context.py` — GitHub pull request, issue, and discussion lookups.
- `ai_briefing.py` — Evidence packet construction, model calls, and citation checks.
- `evaluate.py` — Evaluation utilities.
- `requirements.txt` — Python dependencies.