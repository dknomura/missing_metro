# ITS Level of Traffic Stress
## Setup
Using uv, follow [set up instructions](https://docs.astral.sh/uv/getting-started/installation/)

Run these commands from `notebooks/lts_cv`.

```bash
# Install dependencies
uv sync --locked

# Set up ipykernel for jupyter env, when running jupyter notebook set "LTS_CV (Polaris)" as the kernel 
uv run python -m ipykernel install --user \
    --name lts-cv \
    --display-name "LTS_CV (Polaris)"
```

## Python Source and Notebook Generation

`lts_vlm.py` is the editable source of truth. It uses Jupytext's percent format:
`# %%` starts a code cell and `# %% [markdown]` starts a Markdown cell. The paired
`lts_vlm.ipynb` is the notebook used for interactive execution. Make code changes
in the Python file, not in notebook JSON.

Generate or update the notebook without executing any cells:

```bash
uv run --locked jupytext --to ipynb --update lts_vlm.py
```

This also works if the notebook does not exist. `--update` preserves existing
outputs for unchanged cells when updating an existing notebook. Reload an open
notebook from disk after generation; an older browser copy can otherwise overwrite
the generated cells. Python source files do not contain notebook outputs.

Check that the source survives a Jupytext round trip:

```bash
uv run --locked jupytext --to ipynb --test lts_vlm.py
```

## Run the Notebook

Open `lts_vlm.ipynb` in the existing JupyterLab session and select the
**LTS_CV (Polaris)** kernel registered during setup. If running locally without a
JupyterLab server, this optional command launches one with JupyterLab supplied as
a temporary uv dependency:

```bash
uv run --locked --with jupyterlab jupyter lab lts_vlm.ipynb
```

### Mapillary Coverage Only

The coverage workflow is independent of the VLM. Do not use **Run All** for this
workflow: the earlier VLM cells load a large GPU model.

1. Confirm that `data/ladot_lts/LADOT_StressFreeStreets_091020.gdb` is available.
2. Keep `MAPILLARY_ACCESS_TOKEN` in this directory's `.env` file or environment.
   Do not put its value in either source file. Cached-only runs do not need a token.
3. Scroll to **Mapillary directional imagery coverage**. Run every cell in that
   section, in order, starting with its imports and configuration. These cells
   have the `coverage` tag in the Python source and generated notebook.
4. The offline regression cell should report **28 regression groups passed**.
   It makes no network requests.
5. Click **Load cached map** for an offline preview, or **Acquire all + compute**
   to download published image tiles and calculate coverage. **Requests = 0**
   processes the full supplied LADOT dataset; a positive value limits the logical
   zoom-14 tile budget. Retry attempts do not consume the budget.
   Rerun to resume unfinished tiles. Check **Refresh cached queries** only when you
   want to replace existing snapshots.
6. Toggle street and intersection layers; filter by coverage, status, or LTS.
   Click features for statistics and Mapillary links. Select a `street:<uid>` or
   `intersection:<uid>` in **Feature** and click **Inspect** to load that feature's
   image locations and camera headings.

Coverage uses imagery from any capture date, not verified visibility or current
street conditions. Streets require both viewing directions at the same sampled
locations, including on one-way streets. Panoramas can supply both directions.

Intersections are imagery-only. Images 5–40 m from each LADOT intersection point are
classified by camera heading: within ±30° of the bearing toward the point is an
**approach** view; within ±30° of the outward bearing is an **exit** view.
Panoramas count as both. Each intersection reports unique approach and exit image
counts plus the number of distinct 45° compass sectors (N, NE, E, …). There is no
leg denominator and no OSM/Overpass topology. Centers within 5 m are excluded to
avoid ambiguous assignment.

Imagery is discovered through every intersecting Mapillary zoom-14 `image` tile,
matching the official SDK's approach. The default `mly1_computed_public` index
provides computed positions and computed headings together. A tile is complete only
after it downloads and decodes successfully; failed, malformed, or budget-skipped
tiles stay unknown. A Graph bbox response below its 2,000-result cap is **not**
treated as complete, and legacy Graph bbox caches are preserved only for diagnosis.
Tile caches are versioned by method, pose source, zoom, and tile coordinate under
`data/mapillary_coverage/v1/mapillary_tiles_v2/`; the old Graph cache namespace is
ignored. Each tile cache records its retrieval timestamp. `run.json` records the
discovery method, pose source, requested/completed/failed/skipped tile counts, and
the completed, failed, and skipped tile coordinates.

For the supplied LADOT dataset, the buffered study area resolves to **117** zoom-14
tiles. A fresh complete run therefore needs about **117** logical queries before
retry attempts; denser imagery does not add tiles. Completed tiles are reused, so
reruns only request unfinished work. No Overpass/OSM requests are made.

### Reload Speed and Computed Snapshots

The full cached area contains about **2.76 million image records**. The first
computation takes roughly **25–35 minutes**; most of that is assigning images to
LADOT streets. Updating the status bar now shows each stage, including the long
assignment stage.

A successful computation writes `results/streets.geojson`,
`results/intersections.geojson`, `results/evidence.csv`, `results/issues.csv`, and
`run.json` with a `cache_key` and `rules_version`. **Load cached map** reuses those
snapshots and only rebuilds the image lookup needed for evidence inspection, so a
compatible reload takes about **1–2 minutes** instead of recomputing.

Snapshots are invalidated automatically when any tile cache file changes (new,
refreshed, or removed tiles) or when matching settings or the rules version change.
**Acquire all + compute** deliberately recomputes and refreshes the snapshots. If
no compatible snapshot exists, **Load cached map** falls back to a full computation
with stage progress.

Downloads and computed GeoJSON/CSV snapshots are stored under
`data/mapillary_coverage/v1/`, which is ignored by Git. No images or VLM models are
downloaded by the coverage section. Incomplete acquisition stays gray/unknown
rather than becoming 0%. Street matching uses only LADOT geometry: when two distinct
LADOT features are nearly equidistant from an image, the match is flagged ambiguous
and excluded. Roads absent from the LADOT layer cannot be independently identified,
so some cross-street imagery near a LADOT street may still be unmatched or cause a
warning.

### If Buttons Appear as `VBox(...)` Text

This is the Python text fallback for an ipywidgets control, not HTML for the map.
The kernel needs `ipywidgets`, and the **JupyterLab server environment** needs the
prebuilt `jupyterlab_widgets` frontend. Installing packages only in the kernel's
`.venv` does not fix a server running under another Python environment.

The current Polaris server uses the Python below. Install the frontend with the
Python that actually launches your JupyterLab server:

```bash
/opt/ohpc/pub/apps/anaconda/miniconda3/bin/python3.13 -m pip install --user "jupyterlab_widgets==3.0.17"
/opt/ohpc/pub/apps/anaconda/miniconda3/bin/python3.13 -m jupyter labextension list
```

The extension list should include `@jupyter-widgets/jupyterlab-manager` as enabled
and OK. Save work, restart the Jupyter server/job, refresh the browser, and rerun
the coverage cells. Restarting only the notebook kernel does not reload the
server's frontend extensions.

To use the map immediately without widget buttons, run the coverage section and
then execute one of these commands in a new notebook cell:

```python
# No API calls: render what is already cached.
coverage_build_map(download=False)
```

```python
# Acquire image tiles with a limited logical-query budget, then render.
coverage_build_map(download=True, max_requests=50)
```

These return a normal Folium HTML map with pan/zoom, layer toggles, and feature
popups. Python-backed filters and evidence selection still require the widget
frontend. The map is also saved as
`data/mapillary_coverage/v1/results/coverage_map.html`, which can be opened in a
browser. Use `coverage_state` to inspect the computed tables. Explicitly pass
`max_requests=None` for an unlimited download run; the no-button helper otherwise
defaults to at most 50 logical queries, excluding retry attempts.

### Directional Views for LTS 4 Segments

After the coverage section, run the cells tagged `mapillary_segments`. They select
the **three longest Complete LTS 4 segments with exactly 100% cached coverage**,
ranked by projected geometry length, without recomputing coverage. Fewer than three
qualifying segments stops the section before any image queries or downloads.

Before selecting an image, its **capture-sequence trajectory must follow the road**
in either direction. The check uses all returned metadata, including points outside
the selected image's endpoint setback, rather than using camera orientation as a
proxy for travel. Local context extends up to 60 m along the sequence in each
direction, within 120 seconds of the image. It requires at least three distinct
timestamps, at least 10 m of movement, trajectory-step alignment within 30 degrees,
consistent progress along the road, and nearby positions remaining within the road
matching corridor. Per-step road distance is bounded relative to observed motion
to reject shortcuts across bends. Straight extensions provide context beyond
segment endpoints. Conflicting positions at the same timestamp, missing, sparse,
or stationary evidence are rejected, not guessed.
The query buffer is 60 m so more surrounding sequence context can be returned.

When original GPS positions are available, their sequence trajectory is checked
independently and must also match, with the same inferred direction of travel.
This prevents a misleading computed/SfM track from overriding an original GPS
track that follows a crossing freeway. Insufficient or conflicting original
evidence also rejects the image. With no original GPS, only the available computed
trajectory can be checked; that limitation is retained in the record.

Each photo set contains one **forward and backward** view, with camera
positions grouped within a 25 m window. Successive sets are **at least 25 m apart**,
measured from the last camera in one set to the first in the next; no extra views
are inserted to fill larger gaps. Every camera must be **at least 8 m along the
segment from both endpoints**, treating those endpoints as the intersections.
Directions and left/right road sides follow the stored geometry's start-to-end
direction and local tangent, not legal travel direction. Longitudinal pairs
prefer opposite road sides, with vehicle-forward-facing views preferred when
sequence motion establishes travel direction; same-side forward/backward pairs
are allowed. Within 1 m of the centerline, road side is treated as unknown/center.

Ordinary images must face within 30 degrees of the requested direction. A panorama
with a known heading supplies two distinct **120-degree horizontal, 16:9 perspective
views (1600 x 900 by default)**. Panoramas are preferred when road-side and
vehicle-facing preferences are comparable. Ordinary photos retain their full
original framing: a narrow photo cannot be widened to reveal uncaptured content.
Crops assume the center of the equirectangular source
corresponds to its camera heading. Originals remain in
`/tmp/lts_mapillary/<uid>/originals/<resolution>/`; resolution-specific caches keep
fallback thumbnails from being mistaken for originals. Crops are saved in
`views/`. The map, image records, and model-input PIL images use the directional
views. The final photo table has one row per segment/photo set and columns in
**Forward, Backward** order, with actual camera positions, headings, and trajectory
validation status under each photo. Left/right views are not selected or shown.
Missing views and failed downloads are visible in their cells.

**100% cached coverage is only bilateral imagery coverage**, not proof that all
trajectory-verified forward/backward views exist at each selected station.
Missing directions and
download/projection failures appear in the issues table and each segment's
`metadata.json` (`trajectory_rejections`, `selection_gaps`, `delivery_gaps`,
`directional_complete`). Accepted-image records retain trajectory baseline,
alignment, point count, and inferred travel direction. Graph
queries follow all returned pages, but cannot establish that Mapillary returned
every image. The section reports incomplete delivery rather than claiming that
unavailable imagery satisfies a station.

**Limitation:** a sequence following a perfectly parallel, vertically stacked road
can still have the same horizontal trajectory. This is not an authoritative road-ID
or road-deck matcher; resolving those cases needs additional road-network, elevation,
or visual evidence. A missing neighborhood in the spatial query also remains
insufficient evidence; the code does not assume the full sequence was returned.

Run the offline directional-selection regressions without loading the VLM:

```bash
uv run --locked python ../../tests/test_lts_segment_views.py
```

### VLM Experiments

Run the earlier cells separately when intentionally doing image analysis. Model
initialization requires the corresponding GPU memory and access to the configured
Hugging Face model. Running `python lts_vlm.py` or executing the entire notebook
also runs those model-loading cells; it is not a coverage-only entry point.

## Jupyter Coding Agent
```bash
# Set up jupter.ai
python -m pip install --user "jupyter-ai==3.2.0"

# Set up npm to install global packages to home dir
mkdir -p "$HOME/.local/bin"
npm config set prefix "$HOME/.local" --location=user

# Setup Kilocode agent harness 
# If you want to use another supported agent https://jupyter-ai.readthedocs.io/en/stable/getting-started.html#install-agents
npm install -g @kilocode/cli

# Restart the jupyter job. Then kilo should show up in the jupyter AI chat. 
# Auth chatgpt in Kilocode
kilo auth login --provider codex
# Choose the "ChatGPT Pro/Plus (browser)" option
# When you complete the auth in the browser, it will open up http://localhost:1455/auth/callback?...
# Open a new terminal, run the commands below, and then paste the entire localhost URL to complete auth
read -r -s -p "Paste callback URL: " CALLBACK_URL
echo
curl --silent --show-error --max-time 15 "$CALLBACK_URL"
unset CALLBACK_URL
```
