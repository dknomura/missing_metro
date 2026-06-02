# Python Environment Rule
- Python environment is managed by uv
- NEVER run system `python` or `python3` commands directly
- ALWAYS use `uv run python` to execute scripts and test code
- ALWAYS use `uv run` for script execution and testing
- Package management must use `uv add` instead of `pip install`
- Use `uv pip list` or `uv run pip list` to view installed packages 
- The virtual environment is located at ./venv/

# Pandas Lib Rules
- Prefer vectorization over loops or .apply()
- If you just want to see the schema and some of the data then use `pd.read_file(filename, rows=25)` to reduce the time to open a file

## Agent skills

### Issue tracker

Issues live as GitHub issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Default label vocabulary — five canonical roles mapped one-to-one. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
