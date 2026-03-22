---
applyTo: ".github/workflows/**"
---
# GitHub Actions Guidelines

- All workflow jobs must pin action versions (e.g. actions/checkout@v4, not @main).
- The CI workflow must always include both a test job (pytest) and a lint job (ruff check).
- CodeQL scanning must remain enabled for Python on pull_request and push to main.
- Never add secrets directly to workflow files — use repository secrets and reference via ${{ secrets.NAME }}.
- Coverage threshold must stay at 80% minimum (--cov-fail-under=80).
