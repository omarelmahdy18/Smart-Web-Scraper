"""
Automated Product Image & Source Scraper for Egyptian Beauty & Personal Care Catalog

Enterprise Features:
- External JSON configuration (`config.json`) for store registry, synonyms, and paths.
- High-performance asynchronous HTTP processing using `asyncio` and `aiohttp`.
- Bounded concurrency (`asyncio.Semaphore`) and polite request delays (`asyncio.sleep`).
- Command-Line Interface (`argparse`) to override paths, delays, limits, and concurrency.
- Structured logging to both console and persistent `scraper.log` file (UTF-8).
- Visual terminal progress bar powered by `tqdm.asyncio`.
- Strict token and attribute matching (brand, type, size, variant, bundle filtering).
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import pandas as pd
from tqdm.asyncio import tqdm as async_tqdm

# Ensure Windows terminal handles UTF-8 (Arabic & special Unicode characters)
if hasattr(sys.stdout, "reconfigure") and sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure") and sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logger = logging.getLogger("photo_scraper")


# ==============================================================================
# LOGGING & CONFIGURATION SETUP
# ==============================================================================
def setup_logging(log_file_path: str, verbose: bool = False) -> None:
    """Configures console and file logging with UTF-8 support."""
    log_dir = os.path.dirname(os.path.abspath(log_file_path))
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    file_formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(funcName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # Detailed file handler (saves all DEBUG+ events to scraper.log)
    file_handler = logging.FileHandler(log_file_path, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Informative console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)


def load_config(config_path: str) -> Dict[str, Any]:
    """Loads external configuration from JSON file."""
    path = Path(config_path)
    if not path.is_file():
        # Fallback to config.json in the same directory as the script
        script_dir_path = Path(__file__).resolve().parent / config_path
        if script_dir_path.is_file():
            path = script_dir_path
        else:
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_args() -> argparse.Namespace:
    """Parses CLI arguments to allow overriding config.json defaults."""
    parser = argparse.ArgumentParser(
        description="Asynchronous E-Commerce Product Image & Source Scraper."
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default="config.json",
        help="Path to the configuration JSON file (default: config.json).",
    )
    parser.add_argument(
        "-i",
        "--input",
        type=str,
        default=None,
        help="Override input CSV file path.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Override sample output CSV file path.",
    )
    parser.add_argument(
        "-s",
        "--sources-output",
        type=str,
        default=None,
        help="Override detailed sources output CSV file path.",
    )
    parser.add_argument(
        "-d",
        "--delay",
        type=float,
        default=None,
        help="Override polite request delay in seconds (e.g., 2.0).",
    )
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=None,
        help="Override max number of products to process (use 0 for all rows).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Override maximum number of concurrent product scraping tasks.",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Override path to the log file (default from config or scraper.log).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose DEBUG logging in the console.",
    )
    return parser.parse_args()


# ==============================================================================
# ATTRIBUTE EXTRACTION & NORMALIZATION
# ==============================================================================
def normalize_text(text: str) -> str:
    """Normalizes text for consistent token matching."""
    text = text.lower()
    text = re.sub(r"[^\w\s\+]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_size(text: str) -> Optional[str]:
    """Extracts volume or weight (e.g. '250ml', '74g', '50ml')."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(ml|g|gm|oz|l)\b", text, re.IGNORECASE)
    if match:
        val, unit = match.groups()
        unit = unit.lower()
        if unit == "gm":
            unit = "g"
        return f"{val}{unit}"
    return None


def detect_brand(text: str, brand_synonyms: Dict[str, List[str]]) -> Optional[str]:
    """Detects brand from catalog product name using configured synonyms."""
    lower = text.lower()
    for brand, synonyms in brand_synonyms.items():
        for syn in synonyms:
            if re.search(rf"\b{re.escape(syn)}\b", lower, re.IGNORECASE):
                return brand
    return None


def extract_product_type(text: str, type_synonyms: Dict[str, List[str]]) -> List[str]:
    """Identifies core product categories using configured type synonyms."""
    lower = text.lower()
    matched = []
    for cat, synonyms in type_synonyms.items():
        for syn in synonyms:
            if syn in lower:
                matched.append(cat)
                break
    return matched


# ==============================================================================
# ASYNCHRONOUS STORE QUERY ENGINE
# ==============================================================================
async def query_shopify_store(
    session: aiohttp.ClientSession,
    base_url: str,
    query: str,
    delay_sec: float,
    timeout_sec: int,
) -> List[dict]:
    """
    Queries Shopify's public search suggest API asynchronously with polite delay.
    Returns list of matching products with title, handle/url, and direct image.
    """
    await asyncio.sleep(delay_sec)
    encoded_q = urllib.parse.quote(query)
    url = f"{base_url}/search/suggest.json?q={encoded_q}&resources[type]=product"

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout_sec)) as resp:
            if resp.status != 200:
                logger.warning("HTTP %d when querying '%s' on %s", resp.status, query, base_url)
                return []
            data = await resp.json(content_type=None)
            return data.get("resources", {}).get("results", {}).get("products", [])
    except Exception as e:
        logger.warning("Query failed for '%s' on %s: %s", query, base_url, e)
        return []


async def get_product_details(
    session: aiohttp.ClientSession,
    base_url: str,
    product_handle: str,
    delay_sec: float,
    timeout_sec: int,
) -> Optional[dict]:
    """Fetches full product JSON asynchronously for variant/size verification."""
    await asyncio.sleep(delay_sec)
    url = f"{base_url}/products/{product_handle}.json"

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout_sec)) as resp:
            if resp.status != 200:
                logger.debug("HTTP %d fetching product details from %s", resp.status, url)
                return None
            data = await resp.json(content_type=None)
            return data.get("product")
    except Exception as e:
        logger.debug("Failed to fetch product details for '%s' on %s: %s", product_handle, base_url, e)
        return None


# ==============================================================================
# VERIFICATION & MATCHING LOGIC
# ==============================================================================
async def verify_candidate(
    session: aiohttp.ClientSession,
    target_name: str,
    candidate: dict,
    base_url: str,
    brand_hint: Optional[str],
    config: Dict[str, Any],
    delay_sec: float,
    timeout_sec: int,
) -> Tuple[str, Optional[str], Optional[str], str]:
    """
    Evaluates candidate against strict matching criteria.
    Returns: (match_status, photo_url, product_page_url, notes)
    """
    brand_synonyms = config.get("brand_synonyms", {})
    type_synonyms = config.get("type_synonyms", {})
    critical_tokens = config.get("critical_tokens", {})
    bundle_terms = config.get("bundle_terms", ["bundle", "set", "pouch", "مجموعة", "عرض"])

    cand_title = candidate.get("title", "")
    cand_path = candidate.get("url", "").split("?")[0]
    cand_url = f"{base_url}{cand_path}" if cand_path.startswith("/") else f"{base_url}/{cand_path}"
    cand_img = candidate.get("image", "")

    # Clean image URL (normalize protocol-relative URLs and strip query parameters)
    if cand_img:
        if cand_img.startswith("//"):
            cand_img = f"https:{cand_img}"
        cand_img = cand_img.split("?")[0]

    norm_target = normalize_text(target_name)
    norm_cand = normalize_text(cand_title)

    details: Optional[dict] = None
    details_fetched = False

    async def fetch_details_once() -> Optional[dict]:
        nonlocal details, details_fetched
        if not details_fetched:
            handle = cand_path.rstrip("/").split("/")[-1]
            details = await get_product_details(session, base_url, handle, delay_sec, timeout_sec)
            details_fetched = True
        return details

    # 1. Brand Check
    if brand_hint:
        brand_syns = brand_synonyms.get(brand_hint, [brand_hint])
        has_brand_in_title = any(syn in norm_cand for syn in brand_syns)
        if not has_brand_in_title:
            prod_details = await fetch_details_once()
            if prod_details:
                vendor = prod_details.get("vendor", "").lower()
                has_brand_in_vendor = any(syn in vendor for syn in brand_syns)
                if not has_brand_in_vendor:
                    return (
                        "unconfirmed",
                        None,
                        cand_url,
                        f"Brand mismatch: target has '{brand_hint}', found vendor '{vendor}'",
                    )
            else:
                return (
                    "unconfirmed",
                    None,
                    cand_url,
                    f"Brand mismatch: candidate '{cand_title}' does not match brand '{brand_hint}'",
                )

    # 2. Bundle / Set Check: reject bundles/sets if target is a standalone item
    target_has_bundle = any(b in norm_target for b in bundle_terms)
    cand_has_bundle = any(b in norm_cand for b in bundle_terms)
    if cand_has_bundle and not target_has_bundle:
        return (
            "unconfirmed",
            None,
            cand_url,
            f"Bundle/set mismatch: candidate is a set/bundle '{cand_title}'",
        )

    # 3. Critical Attribute Tokens Check
    for attr, syns in critical_tokens.items():
        if attr in norm_target:
            cand_full_text = f"{norm_cand} {cand_path}"
            if not any(s in cand_full_text for s in syns):
                return (
                    "unconfirmed",
                    None,
                    cand_url,
                    f"Missing critical attribute '{attr}' in candidate '{cand_title}'",
                )

    # 4. Product Type Check
    target_types = extract_product_type(target_name, type_synonyms)
    cand_types = extract_product_type(cand_title, type_synonyms)
    if target_types:
        is_type_match = any(t in cand_types or t in norm_cand for t in target_types)
        if not is_type_match:
            return (
                "unconfirmed",
                None,
                cand_url,
                f"Type mismatch: expected {target_types}, found {cand_types}",
            )

    # 5. Size Check
    target_size = extract_size(target_name)
    cand_size = extract_size(cand_title)

    if target_size:
        target_num = re.search(r"\d+", target_size).group()
        target_unit = re.search(r"[a-zA-Z]+", target_size).group()

        prod_details = await fetch_details_once()

        title_matches_size = False
        if cand_size == target_size or (target_num in norm_cand and target_unit in norm_cand):
            title_matches_size = True

        variant_matched = False
        if prod_details:
            variants = prod_details.get("variants", [])
            for v in variants:
                v_title = v.get("title", "").lower()
                if (
                    target_size in v_title
                    or target_num == v_title.strip()
                    or f"{target_num} {target_unit}" in v_title
                ):
                    variant_matched = True
                    if v.get("featured_image") and v["featured_image"].get("src"):
                        cand_img = v["featured_image"]["src"].split("?")[0]
                    break

            if variant_matched and prod_details.get("images"):
                for img_obj in prod_details["images"]:
                    src = img_obj.get("src", "")
                    if f"{target_num}ml" in src.lower() or f"{target_num}" in src.lower():
                        cand_img = src.split("?")[0]
                        break

            if not variant_matched and len(variants) == 1:
                body = (prod_details.get("body_html") or "").lower()
                if target_size in body or f"{target_num} {target_unit}" in body:
                    variant_matched = True

        if not title_matches_size and not variant_matched:
            return (
                "unconfirmed",
                None,
                cand_url,
                f"Size mismatch: expected {target_size}, could not verify on '{cand_title}'",
            )

    # 6. Image Check
    if not cand_img:
        return "unconfirmed", None, cand_url, "Product page found but no valid image link available"

    return "confirmed", cand_img, cand_url, f"Confirmed match with candidate '{cand_title}'"


# ==============================================================================
# CONCURRENT PRODUCT WORKER
# ==============================================================================
async def process_product(
    idx: int,
    total: int,
    product_name: str,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    config: Dict[str, Any],
    delay_sec: float,
    timeout_sec: int,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Processes a single product asynchronously within bounded concurrency."""
    store_registry = config.get("store_registry", {})
    brand_synonyms = config.get("brand_synonyms", {})
    type_synonyms = config.get("type_synonyms", {})

    async with semaphore:
        logger.debug("[%d/%d] Searching: '%s'", idx + 1, total, product_name)
        brand = detect_brand(product_name, brand_synonyms)

        # Determine store sequence: official brand store first if known, then Source Beauty
        stores_to_search: List[Tuple[str, Dict[str, Any]]] = []
        if brand:
            for store_id, store_cfg in store_registry.items():
                if brand in store_cfg.get("brands", []):
                    stores_to_search.append((store_id, store_cfg))

        if "source_beauty" in store_registry and not any(
            sid == "source_beauty" for sid, _ in stores_to_search
        ):
            stores_to_search.append(("source_beauty", store_registry["source_beauty"]))

        best_unconfirmed: Optional[Dict[str, str]] = None

        for store_id, store_cfg in stores_to_search:
            store_name = store_cfg["name"]
            base_url = store_cfg["base_url"]

            # Build query variations: full name, then simplified query without size/symbols
            queries = [product_name]
            simplified = re.sub(r"\b\d+(?:ml|g|gm|oz)\b", "", product_name, flags=re.I)
            simplified = re.sub(r"[\+\&]", " ", simplified)
            simplified = " ".join(simplified.split())
            if simplified != product_name:
                queries.append(simplified)

            # For dedicated brand stores, also try queries without brand prefix or core type
            if brand and store_cfg.get("brands"):
                nobrand = re.sub(rf"\b{re.escape(brand)}\b", "", simplified, flags=re.I).strip()
                if nobrand and nobrand not in queries:
                    queries.append(nobrand)
                for ptype in extract_product_type(product_name, type_synonyms):
                    for syn in type_synonyms.get(ptype, []):
                        if syn not in queries and len(syn) > 2:
                            queries.append(syn)

            for q in queries:
                logger.debug("[%d/%d] Querying [%s]: '%s'", idx + 1, total, store_name, q)
                candidates = await query_shopify_store(session, base_url, q, delay_sec, timeout_sec)
                if not candidates:
                    continue

                for cand in candidates[:3]:
                    status, img_url, page_url, notes = await verify_candidate(
                        session=session,
                        target_name=product_name,
                        candidate=cand,
                        base_url=base_url,
                        brand_hint=brand,
                        config=config,
                        delay_sec=delay_sec,
                        timeout_sec=timeout_sec,
                    )

                    if status == "confirmed":
                        logger.debug(
                            "[%d/%d] CONFIRMED '%s' in %s -> %s",
                            idx + 1,
                            total,
                            product_name,
                            store_name,
                            img_url,
                        )
                        sample_entry = {"name": product_name, "photo_url": img_url or ""}
                        source_entry = {
                            "name": product_name,
                            "photo_url": img_url or "",
                            "product_page_url": page_url or "",
                            "store": store_name,
                            "match_status": "confirmed",
                            "notes": notes,
                        }
                        return sample_entry, source_entry
                    else:
                        if best_unconfirmed is None:
                            best_unconfirmed = {
                                "name": product_name,
                                "photo_url": "",
                                "product_page_url": page_url or "",
                                "store": store_name,
                                "match_status": "unconfirmed",
                                "notes": notes,
                            }

        if best_unconfirmed:
            logger.debug(
                "[%d/%d] UNCONFIRMED '%s': %s",
                idx + 1,
                total,
                product_name,
                best_unconfirmed["notes"],
            )
            return {"name": product_name, "photo_url": ""}, best_unconfirmed

        logger.debug("[%d/%d] NOT FOUND '%s'", idx + 1, total, product_name)
        return (
            {"name": product_name, "photo_url": ""},
            {
                "name": product_name,
                "photo_url": "",
                "product_page_url": "",
                "store": "",
                "match_status": "not_found",
                "notes": "No matching product found on official brand stores or Source Beauty",
            },
        )


# ==============================================================================
# MAIN ASYNC ORCHESTRATOR
# ==============================================================================
async def run_scraper(args: argparse.Namespace) -> None:
    """Loads configuration, initializes async HTTP session, and runs scraping tasks."""
    config = load_config(args.config)
    paths_cfg = config.get("paths", {})
    settings_cfg = config.get("scraper_settings", {})

    # Resolve effective settings (CLI arguments override config.json)
    input_csv_path = args.input or paths_cfg.get("input_csv", "needed_photos.csv")
    output_sample_path = args.output or paths_cfg.get("output_sample_csv", "needed_photos_sample.csv")
    output_sources_path = args.sources_output or paths_cfg.get(
        "output_sources_csv", "needed_photos_sources.csv"
    )
    log_file_path = args.log_file or paths_cfg.get("log_file", "scraper.log")

    delay_sec = (
        args.delay if args.delay is not None else float(settings_cfg.get("request_delay_sec", 2.0))
    )
    pilot_limit = (
        args.limit if args.limit is not None else int(settings_cfg.get("pilot_limit", 25))
    )
    max_concurrency = (
        args.concurrency
        if args.concurrency is not None
        else int(settings_cfg.get("max_concurrent_tasks", 5))
    )
    timeout_sec = int(settings_cfg.get("request_timeout_sec", 10))
    user_agent = settings_cfg.get(
        "user_agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) (Egyptian Beauty Catalog Matcher/2.0)",
    )

    setup_logging(log_file_path, verbose=args.verbose)

    logger.info("=" * 75)
    logger.info("E-COMMERCE BEAUTY & PERSONAL CARE PRODUCT SCRAPER")
    logger.info("=" * 75)
    logger.info("Configuration loaded from: %s", os.path.abspath(args.config))
    logger.info("Log file initialized at:   %s", os.path.abspath(log_file_path))

    if not os.path.exists(input_csv_path):
        logger.error("Input CSV file not found at: %s", input_csv_path)
        raise FileNotFoundError(f"Input file not found at: {input_csv_path}")

    df = pd.read_csv(input_csv_path)
    if pilot_limit and pilot_limit > 0:
        working_df = df.head(pilot_limit).copy()
        logger.info(
            "Loaded %d total rows from '%s'. Processing subset of %d rows (limit=%d).",
            len(df),
            input_csv_path,
            len(working_df),
            pilot_limit,
        )
    else:
        working_df = df.copy()
        logger.info("Loaded %d total rows from '%s'. Processing full dataset.", len(df), input_csv_path)

    logger.info(
        "Settings: concurrency=%d | request_delay=%.2fs | timeout=%ds",
        max_concurrency,
        delay_sec,
        timeout_sec,
    )

    semaphore = asyncio.Semaphore(max_concurrency)
    headers = {"User-Agent": user_agent}
    total_items = len(working_df)

    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = [
            process_product(
                idx=idx,
                total=total_items,
                product_name=str(row["name"]).strip(),
                session=session,
                semaphore=semaphore,
                config=config,
                delay_sec=delay_sec,
                timeout_sec=timeout_sec,
            )
            for idx, (_, row) in enumerate(working_df.iterrows())
        ]

        # Run tasks concurrently with tqdm visual progress bar while preserving order
        results: List[Tuple[Dict[str, str], Dict[str, str]]] = await async_tqdm.gather(
            *tasks,
            desc="Scraping Products",
            unit="product",
        )

    sample_results = [r[0] for r in results]
    sources_results = [r[1] for r in results]

    sample_df = pd.DataFrame(sample_results)
    sources_df = pd.DataFrame(sources_results)

    for out_path in (output_sample_path, output_sources_path):
        out_dir = os.path.dirname(os.path.abspath(out_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

    sample_df.to_csv(output_sample_path, index=False, encoding="utf-8-sig")
    sources_df.to_csv(output_sources_path, index=False, encoding="utf-8-sig")

    confirmed_count = int((sources_df["match_status"] == "confirmed").sum())
    unconfirmed_count = int((sources_df["match_status"] == "unconfirmed").sum())
    not_found_count = int((sources_df["match_status"] == "not_found").sum())

    logger.info("=" * 75)
    logger.info("SCRAPING SUMMARY RESULTS")
    logger.info("=" * 75)
    logger.info("Total Products Processed: %d", len(sources_df))
    logger.info("Confirmed Matches:        %d", confirmed_count)
    logger.info("Unconfirmed Matches:      %d", unconfirmed_count)
    logger.info("Not Found:                %d", not_found_count)
    logger.info("Sample CSV saved to:      %s", output_sample_path)
    logger.info("Sources CSV saved to:     %s", output_sources_path)
    logger.info("Detailed log saved to:    %s", os.path.abspath(log_file_path))
    logger.info("=" * 75)


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(run_scraper(args))
    except KeyboardInterrupt:
        logger.warning("Scraping interrupted by user.")
        sys.exit(130)


if __name__ == "__main__":
    main()
