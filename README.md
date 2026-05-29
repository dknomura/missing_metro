---
title: Missing Metro
emoji: 🗺️
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# Missing Metro Marimo
## Development

Using uv, install instructions [in uv repo](https://github.com/astral-sh/uv)

### Setup

```bash
# Install dependencies
uv sync
```

### Editing notebook
The marimo VSCode extension is buggy, so run the notebook in the browser.

```bash
# To run all of the notebooks in edit mode
uv run marimo edit 

# To run a specific notebook
uv run marimo edit .\notebooks\sb79map.py

# To run a specific notebook in app mode
uv run marimo run .\notebooks\sb79map.py
```

### Running Tests

```bash
uv run pytest tests
```

### Linting and formatting

```bash
uv run ruff check .
uv run ruff format .
```

