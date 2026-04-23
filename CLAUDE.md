# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

This is a Bangkok POI (Point of Interest) scraper that collects location data from Google Maps **without an API key**, using Playwright to simulate human browsing. The data supports transportation planning research, with the target output being a structured dataset (CSV or Shapefile) containing:

| Column | Description |
|---|---|
| Name | Place name |
| Type | POI category |
| Lat | Latitude |
| Lon | Longitude |
| Other Information | Rating, review count, or other extracted metadata |

## Setup

```bash
pip install playwright geopandas shapely pandas tqdm
playwright install chromium
```

## Running

```bash
python bangkok_poi_scraper.py
```

Currently outputs a Shapefile (`bangkok_poi.shp`). The goal is CSV output — when modifying the output format, change the final export block near the bottom of `main()`.

## Boundary Shapefile

`Bangkok shapefile/BMA_ADMIN_SUB_DISTRICT.shp` — 180 sub-districts, projected in **EPSG:32647** (UTM Zone 47N). The file has no embedded CRS so `set_crs("EPSG:32647", allow_override=True)` is required when reading it. Dissolved to a single boundary polygon for grid generation.

## Architecture

The script is a single-file async Playwright scraper with four logical layers:

**1. Config block (top of file)**
- `POI_TYPES` — list of search categories
- `GRID_SPACING_M` — grid cell size in metres (default 1 000 m → 1 580 points inside Bangkok)
- `SEARCH_ZOOM` — Google Maps zoom level used in search URLs (15 ≈ 1 km visible radius)
- Tuning knobs: `SCROLL_TIMES`, `SCROLL_DELAY`, `NAV_DELAY`, `HEADLESS`

**2. Grid generation (`generate_grid_points`)**
- Reads and dissolves the Bangkok sub-district shapefile into one polygon (UTM 47N)
- Lays a regular grid at `GRID_SPACING_M` metre intervals, keeps only points inside the boundary
- Reprojects kept points to WGS84 and returns `[(lat, lon), ...]`

**3. Two-phase scraping (`collect_hrefs_for_cell` → `visit_place`)**
- Phase 1 (`collect_hrefs_for_cell`): Navigates to `google.com/maps/search/{poi_type}/@{lat},{lon},{zoom}z`, scrolls the results feed, harvests `/maps/place/` hrefs. No place details extracted here.
- Phase 2 (`visit_place`): Navigates directly to each href, extracts name (`h1.DUwDvf`), rating (`div.F7nice span[aria-hidden]`), review count (`div.F7nice span[aria-label]`), category (`button[jsaction*="category"]`), and lat/lon from the URL regex `@(-?\d+\.\d+),(-?\d+\.\d+)`.

**4. Deduplication in `main()`**
- `all_seen_hrefs` (set) — prevents visiting the same URL twice across grid cells and POI types
- `seen_keys` (set of `(name.lower(), round(lat,4), round(lon,4))`) — prevents duplicate records in the final CSV

## Key Behaviors to Know

- **Coordinate extraction** relies on the Google Maps URL pattern `/@lat,lon,zoom`. If Google changes this pattern, `extract_coords_from_url()` returns `(None, None)` and the place is silently skipped.
- **HEADLESS = False** renders a visible browser — essential for debugging when results are unexpectedly low.
- Google Maps DOM selectors (`h1.DUwDvf`, `div.F7nice`, `button[jsaction*="category"]`) are brittle and change without notice — the most likely source of silent data loss.
- Output is `utf-8-sig` encoded CSV so Excel opens it correctly with Thai characters.

## Extending the Scraper

To add new fields (e.g., address, phone number, opening hours), add extraction logic inside `visit_place()` using the same `try/except` pattern, include the new key in the returned dict, and add the column name to the `pd.DataFrame(all_records, columns=[...])` call in `main()`.
