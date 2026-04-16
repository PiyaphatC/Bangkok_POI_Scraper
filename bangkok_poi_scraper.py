"""
Bangkok POI Scraper - Google Maps (No API Key)
Uses Playwright to browse Google Maps like a human.
Output: Shapefile with fields [Name, Type, Rating, Lat, Lon]

Requirements:
    pip install playwright geopandas shapely pandas
    playwright install chromium

Usage:
    python bangkok_poi_scraper.py
"""

import re
import time
import asyncio
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from playwright.async_api import async_playwright
from tqdm import tqdm

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
OUTPUT_FILE = "bangkok_poi"   # Output shapefile name

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

SCROLL_TIMES  = 20    # How many times to scroll the result list (more = more results)
SCROLL_DELAY  = 1.5   # Seconds between each scroll
CLICK_DELAY   = 2.0   # Seconds to wait after clicking a place (for URL to update)
HEADLESS      = True  # Set True to run browser in background (invisible)

# Bangkok districts — script searches each POI type in every district
BANGKOK_AREAS = [
    # Central
    "Sukhumvit Bangkok",
    "Silom Bangkok",
    "Siam Bangkok",
    "Pathumwan Bangkok",
    "Ratchathewi Bangkok",
    "Bang Rak Bangkok",
    "Chinatown Bangkok",
    "Samphanthawong Bangkok",
    "Phra Nakhon Bangkok",
    "Dusit Bangkok",
    # North
    "Chatuchak Bangkok",
    "Ari Bangkok",
    "Lat Phrao Bangkok",
    "Ratchada Bangkok",
    "Huai Khwang Bangkok",
    "Wang Thonglang Bangkok",
    "Bueng Kum Bangkok",
    "Saphan Sung Bangkok",
    "Min Buri Bangkok",
    "Khlong Sam Wa Bangkok",
    "Don Mueang Bangkok",
    "Lak Si Bangkok",
    "Bang Khen Bangkok",
    "Sai Mai Bangkok",
    # East
    "On Nut Bangkok",
    "Phra Khanong Bangkok",
    "Bang Na Bangkok",
    "Prawet Bangkok",
    "Suan Luang Bangkok",
    "Bang Kapi Bangkok",
    "Ladkrabang Bangkok",
    "Nong Chok Bangkok",
    "Khlong Toei Bangkok",
    "Watthana Bangkok",
    # South
    "Yan Nawa Bangkok",
    "Sathon Bangkok",
    "Rat Burana Bangkok",
    "Bang Khun Thian Bangkok",
    "Nong Khaem Bangkok",
    "Phasi Charoen Bangkok",
    "Chom Thong Bangkok",
    # West (Thonburi side)
    "Thonburi Bangkok",
    "Khlong San Bangkok",
    "Bangkok Noi Bangkok",
    "Bangkok Yai Bangkok",
    "Taling Chan Bangkok",
    "Bang Phlat Bangkok",
    "Lat Ya Bangkok",
    "Bang Bon Bangkok",
]


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────
def extract_coords_from_url(url):
    """Extract lat, lon from Google Maps URL like .../@13.7563,100.5018,17z..."""
    match = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
    if match:
        return float(match.group(1)), float(match.group(2))
    return None, None


# ─────────────────────────────────────────
# SCRAPER
# ─────────────────────────────────────────
async def scrape_area(page, poi_type, area):
    """Search one POI type in one Bangkok area and return hrefs."""
    search_query = f"{poi_type} in {area}"
    search_url   = f"https://www.google.com/maps/search/{search_query.replace(' ', '+')}"

    print(f"  Searching: {search_query}")
    await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(3)

    result_list_selector = 'div[role="feed"]'
    try:
        await page.wait_for_selector(result_list_selector, timeout=10000)
        for i in range(SCROLL_TIMES):
            await page.eval_on_selector(
                result_list_selector,
                "el => el.scrollBy(0, 800)"
            )
            await asyncio.sleep(SCROLL_DELAY)
    except Exception:
        pass

    cards = await page.query_selector_all('a[href*="/maps/place/"]')
    hrefs = []
    for card in cards:
        href = await card.get_attribute("href")
        if href and href not in hrefs:
            hrefs.append(href)

    print(f"    → {len(hrefs)} links found")
    return hrefs


async def scrape_poi_type(page, poi_type, all_seen_hrefs):
    """Search a POI type across all Bangkok areas and collect results."""
    records = []
    all_hrefs = []

    # Collect links from every district first
    area_bar = tqdm(BANGKOK_AREAS, desc=f"[{poi_type}] Scanning districts", unit="area")
    for area in area_bar:
        hrefs = await scrape_area(page, poi_type, area)
        for h in hrefs:
            if h not in all_seen_hrefs and h not in all_hrefs:
                all_hrefs.append(h)
        area_bar.set_postfix({"links": len(all_hrefs)})

    # Mark all collected hrefs as seen globally
    all_seen_hrefs.update(all_hrefs)

    print(f"\n  Total unique new links for [{poi_type}]: {len(all_hrefs)}")

    for idx, href in enumerate(tqdm(all_hrefs, desc=f"[{poi_type}] Visiting places", unit="place")):
        try:
            # Navigate directly to the place URL instead of clicking
            await page.goto(href, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(CLICK_DELAY)

            current_url = page.url

            # ── Extract Lat / Lon from URL ──
            lat, lon = extract_coords_from_url(current_url)
            if lat is None:
                await page.go_back()
                await asyncio.sleep(1)
                continue

            # ── Extract Name ────────────────
            name = None
            try:
                name_el = await page.query_selector('h1.DUwDvf, h1[class*="fontHeadlineLarge"]')
                if name_el:
                    name = await name_el.inner_text()
            except Exception:
                pass

            # ── Extract Rating ──────────────
            rating = None
            try:
                rating_el = await page.query_selector('div.F7nice span[aria-hidden="true"]')
                if rating_el:
                    rating_text = await rating_el.inner_text()
                    rating = float(rating_text.strip())
            except Exception:
                pass

            # ── Extract Type / Category ─────
            category = poi_type
            try:
                cat_el = await page.query_selector('button[jsaction*="category"]')
                if cat_el:
                    category = await cat_el.inner_text()
            except Exception:
                pass

            if name:
                records.append({
                    "name":   name.strip(),
                    "type":   category.strip(),
                    "rating": rating,
                    "lat":    lat,
                    "lon":    lon,
                })
                print(f"    [{idx+1}] {name.strip()[:40]:<40} | {rating} | {lat:.4f}, {lon:.4f}")

            # Go back to results list
            await page.go_back()
            await asyncio.sleep(1.5)

        except Exception as e:
            print(f"    [SKIP] Card {idx+1}: {e}")
            try:
                await page.go_back()
                await asyncio.sleep(1)
            except Exception:
                pass

    return records


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
async def main():
    all_records = []
    seen_names  = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(
            locale="en-US",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        all_seen_hrefs = set()
        poi_bar = tqdm(POI_TYPES, desc="Overall POI types", unit="type", position=0)
        for poi_type in poi_bar:
            poi_bar.set_description(f"Overall [{poi_type}]")
            records = await scrape_poi_type(page, poi_type, all_seen_hrefs)
            for r in records:
                key = (r["name"].lower(), round(r["lat"], 4), round(r["lon"], 4))
                if key not in seen_names:
                    seen_names.add(key)
                    all_records.append(r)

        await browser.close()

    print(f"\nTotal unique POIs collected: {len(all_records)}")

    if not all_records:
        print("No data collected. Try setting HEADLESS = False to debug.")
        return

    # ── Build GeoDataFrame ──────────────────
    df = pd.DataFrame(all_records)
    df = df.dropna(subset=["lat", "lon"])

    geometry = [Point(row.lon, row.lat) for row in df.itertuples()]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
    gdf = gdf[["name", "type", "rating", "lat", "lon", "geometry"]]

    # ── Export Shapefile ────────────────────
    output_path = f"{OUTPUT_FILE}.shp"
    gdf.to_file(output_path)
    print(f"Shapefile saved: {output_path}")
    print(f"\nSample output:")
    print(gdf.head(10).to_string(index=False))


if __name__ == "__main__":
    asyncio.run(main())
