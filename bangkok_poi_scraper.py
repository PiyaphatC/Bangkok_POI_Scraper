"""
Bangkok POI Scraper v2 - Google Maps (No API Key)
Grid-based search using Bangkok boundary shapefile.
Uses Playwright to browse Google Maps like a human.
Output: CSV with columns [name, type, lat, lon, rating, review_count]

Requirements:
    pip install playwright geopandas shapely pandas tqdm
    playwright install chromium

Usage:
    python bangkok_poi_scraper.py
"""

import re
import asyncio
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from shapely.ops import unary_union
from playwright.async_api import async_playwright
from tqdm import tqdm


# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
SHAPEFILE_PATH = "Bangkok shapefile/BMA_ADMIN_SUB_DISTRICT.shp"
SHAPEFILE_CRS  = "EPSG:32647"   # UTM Zone 47N — matches projected coords in the .shp
OUTPUT_CSV     = "bangkok_poi.csv"

POI_TYPES = [
    "restaurant",
    "cafe",
    "hotel",
    "shopping mall",
    "tourist attraction",
    "museum",
    "park",
    "hospital",
    "school",
    "bank",
]

GRID_SPACING_M = 1000   # Distance between grid search centres in metres (1 km)
SEARCH_ZOOM    = 15     # Google Maps zoom level — 15 ≈ ~1 km radius visible on screen
SCROLL_TIMES   = 15     # Number of scrolls per result list (more → more results)
SCROLL_DELAY   = 1.5    # Seconds between scrolls
NAV_DELAY      = 2.0    # Seconds to wait after navigating to a place page
HEADLESS       = True   # Set False to watch the browser (useful for debugging)


# ─────────────────────────────────────────
# GRID GENERATION
# ─────────────────────────────────────────
def generate_grid_points(shapefile_path: str, shapefile_crs: str, spacing_m: float):
    """
    Build a regular grid of WGS84 (lat, lon) points that fall inside the
    Bangkok boundary.  The grid is constructed in the projected CRS so that
    spacing is uniform in metres, then reprojected to WGS84 for use in URLs.
    """
    gdf = gpd.read_file(shapefile_path)
    gdf = gdf.set_crs(shapefile_crs, allow_override=True)

    # Dissolve all 180 sub-districts into one Bangkok boundary polygon
    bangkok = unary_union(gdf.geometry)
    minx, miny, maxx, maxy = bangkok.bounds

    xs = [minx + i * spacing_m for i in range(int((maxx - minx) / spacing_m) + 2)]
    ys = [miny + j * spacing_m for j in range(int((maxy - miny) / spacing_m) + 2)]

    pts_proj = [Point(x, y) for x in xs for y in ys if bangkok.contains(Point(x, y))]

    pts_gdf = gpd.GeoDataFrame(geometry=pts_proj, crs=shapefile_crs)
    pts_gdf = pts_gdf.to_crs("EPSG:4326")

    grid = [(round(g.y, 6), round(g.x, 6)) for g in pts_gdf.geometry]
    tqdm.write(f"Grid ready: {len(grid)} search points at {spacing_m:.0f} m spacing")
    return grid


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────
def extract_coords_from_url(url: str):
    """Pull lat, lon out of a Google Maps URL like .../@13.7563,100.5018,17z..."""
    match = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
    if match:
        return float(match.group(1)), float(match.group(2))
    return None, None


def parse_review_count(text: str):
    """Convert strings like '(1,234)' or '1234 reviews' to an int."""
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


# ─────────────────────────────────────────
# SCRAPER — PHASE 1: collect hrefs
# ─────────────────────────────────────────
async def collect_hrefs_for_cell(page, poi_type: str, lat: float, lon: float):
    """
    Navigate to Google Maps search centred on (lat, lon) for one POI type
    and return all unique /maps/place/ hrefs found after scrolling.
    """
    url = (
        f"https://www.google.com/maps/search/"
        f"{poi_type.replace(' ', '+')}/"
        f"@{lat},{lon},{SEARCH_ZOOM}z"
    )
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    await asyncio.sleep(3)

    try:
        await page.wait_for_selector('div[role="feed"]', timeout=10_000)
        for _ in range(SCROLL_TIMES):
            await page.eval_on_selector('div[role="feed"]', "el => el.scrollBy(0, 800)")
            await asyncio.sleep(SCROLL_DELAY)
    except Exception:
        pass  # No results feed — search returned nothing

    cards = await page.query_selector_all('a[href*="/maps/place/"]')
    seen = set()
    hrefs = []
    for card in cards:
        href = await card.get_attribute("href")
        if href and href not in seen:
            seen.add(href)
            hrefs.append(href)
    return hrefs


# ─────────────────────────────────────────
# SCRAPER — PHASE 2: visit each place
# ─────────────────────────────────────────
async def visit_place(page, href: str, poi_type: str):
    """
    Navigate directly to a place page and extract all available fields.
    Returns a record dict, or None if coordinates cannot be found.
    """
    await page.goto(href, wait_until="domcontentloaded", timeout=60_000)
    await asyncio.sleep(NAV_DELAY)

    lat, lon = extract_coords_from_url(page.url)
    if lat is None:
        return None

    # ── Name ──────────────────────────────────────────────────────────────────
    name = None
    try:
        el = await page.query_selector('h1.DUwDvf, h1[class*="fontHeadlineLarge"]')
        if el:
            name = (await el.inner_text()).strip()
    except Exception:
        pass
    if not name:
        return None

    # ── Rating ────────────────────────────────────────────────────────────────
    rating = None
    try:
        el = await page.query_selector('div.F7nice span[aria-hidden="true"]')
        if el:
            rating = float((await el.inner_text()).strip())
    except Exception:
        pass

    # ── Review count ──────────────────────────────────────────────────────────
    review_count = None
    try:
        # The review count span has an aria-label like "1,234 reviews"
        el = await page.query_selector('div.F7nice span[aria-label]')
        if el:
            label = await el.get_attribute("aria-label")
            if label:
                review_count = parse_review_count(label)
    except Exception:
        pass

    # ── Category ──────────────────────────────────────────────────────────────
    category = poi_type
    try:
        el = await page.query_selector('button[jsaction*="category"]')
        if el:
            category = (await el.inner_text()).strip()
    except Exception:
        pass

    return {
        "name":         name,
        "type":         category,
        "lat":          lat,
        "lon":          lon,
        "rating":       rating,
        "review_count": review_count,
    }


# ─────────────────────────────────────────
# SCRAPER — per POI type
# ─────────────────────────────────────────
async def scrape_poi_type(page, poi_type: str, grid_points: list, all_seen_hrefs: set):
    """
    Phase 1: sweep every grid cell and harvest unique /maps/place/ hrefs.
    Phase 2: visit each href and extract place details.
    """
    # ── Phase 1: harvest hrefs ────────────────────────────────────────────────
    new_hrefs = []
    bar = tqdm(
        grid_points,
        desc=f"  Phase 1 grid scan",
        unit="cell",
        position=1,
        leave=False,
        dynamic_ncols=True,
    )
    for lat, lon in bar:
        hrefs = await collect_hrefs_for_cell(page, poi_type, lat, lon)
        for h in hrefs:
            if h not in all_seen_hrefs and h not in new_hrefs:
                new_hrefs.append(h)
        bar.set_postfix(links=len(new_hrefs))

    all_seen_hrefs.update(new_hrefs)
    tqdm.write(f"  [{poi_type}] grid scan done — {len(new_hrefs)} unique place links")

    # ── Phase 2: visit each place ─────────────────────────────────────────────
    records = []
    bar2 = tqdm(
        new_hrefs,
        desc=f"  Phase 2 visiting",
        unit="place",
        position=1,
        leave=False,
        dynamic_ncols=True,
    )
    for idx, href in enumerate(bar2):
        try:
            rec = await visit_place(page, href, poi_type)
            if rec:
                records.append(rec)
                bar2.set_postfix(
                    saved=len(records),
                    rating=rec["rating"],
                    name=rec["name"][:20],
                )
        except Exception as e:
            tqdm.write(f"  [SKIP {idx+1}] {e}")

    tqdm.write(f"  [{poi_type}] visiting done — {len(records)} records saved")
    return records


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
async def main():
    grid_points = generate_grid_points(SHAPEFILE_PATH, SHAPEFILE_CRS, GRID_SPACING_M)

    all_records = []
    seen_keys   = set()   # (name_lower, lat_4dp, lon_4dp) — cross-type dedup

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(
            locale="en-US",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        all_seen_hrefs: set = set()

        outer_bar = tqdm(
            POI_TYPES,
            desc="Overall progress",
            unit="type",
            position=0,
            dynamic_ncols=True,
        )
        for poi_type in outer_bar:
            outer_bar.set_description(f"Overall [{poi_type}]")
            records = await scrape_poi_type(page, poi_type, grid_points, all_seen_hrefs)
            for r in records:
                key = (r["name"].lower(), round(r["lat"], 4), round(r["lon"], 4))
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_records.append(r)
            outer_bar.set_postfix(total_pois=len(all_records))

        await browser.close()

    tqdm.write(f"\nTotal unique POIs collected: {len(all_records)}")

    if not all_records:
        tqdm.write("No data collected. Try setting HEADLESS = False to debug.")
        return

    df = pd.DataFrame(all_records, columns=["name", "type", "lat", "lon", "rating", "review_count"])
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    tqdm.write(f"\nCSV saved → {OUTPUT_CSV}")
    tqdm.write(df.head(10).to_string(index=False))


if __name__ == "__main__":
    asyncio.run(main())
