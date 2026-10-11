# Architectural Intent Briefing
`src/requests/sessions.py:158` — commit `09fd857eb1` — model `gemma-4-26b-a4b-it`

## Verified facts
* The line is part of the `should_strip_auth` method within the `SessionRedirectMixin` class in `src/requests/sessions.py`.
* The line performs a comparison between the hostname of the original URL (`old_parsed.hostname`) and the hostname of the new redirect URL (`new_parsed.hostname`).
* This line is part of a logic block that determines whether the `Authorization` header should be removed during a redirect.
* This specific line was part of a revert commit (`09fd857e`) that removed a feature called "trusted domains."
* Prior to this commit, the conditional statement also checked if the new hostname was present in a `self.trusted_domains` list.

## Possible intent (hypotheses)
* **Hypothesis:** The "trusted domains" feature was reverted to ensure that `Authorization` headers are strictly stripped whenever a redirect moves to a different hostname, preventing potential credential leakage. (Based on the commit message "Revert 'Add trusted domains'" and the context of the `should_strip_auth` method).

## Unknowns / missing evidence
* The specific reason why the "Add trusted domains" feature was deemed problematic or unnecessary is not documented in the provided commit message.
* The original implementation details and the motivation for the "Add trusted domains" feature (commit `fbe48fbb7c5b96cde3a16453311e45f723516afd`) are missing.

## Risks to investigate before changing this code
* If this logic is modified to allow the `Authorization` header to persist across different hostnames, there is a risk of sensitive credentials being sent to unintended third-party domains during a redirect.
* Investigate whether there are legitimate requirements for cross-domain authentication persistence and if those should be handled by the user rather than the library.

## Evidence strength
Partial. The diff and commit message clearly show *what* was reverted (the trusted domains check), but they do not explain *why* the feature was reverted.
