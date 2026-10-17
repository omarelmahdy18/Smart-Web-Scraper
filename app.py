"""
Smart Web Scraper GUI built with Streamlit, Requests, BeautifulSoup, and Pandas.

Standalone Desktop Edition Features:
- Clean, distraction-free UI (hides Streamlit's Deploy button, Hamburger Menu, Header, and Footer).
- Streamlined non-technical Sidebar with a "Polite Delay Between Pages" slider, clear instructions,
  and a prominent "Shutdown Application" button that cleanly terminates the background server.
- Supports two intelligent scraping modes:
  * Option A: Single Product Page (Extract Metadata / JSON-LD & Open Graph)
  * Option B: Category/Collection Page (Auto-Detect Repeating Product Grid + Automatic Pagination)
"""

import json
import os
import re
import signal
import time
import urllib.parse
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup, Tag


# ==============================================================================
# PAGE CONFIGURATION & UI CLEANUP (CSS INJECTION)
# ==============================================================================
st.set_page_config(
    page_title="Smart Web Scraper",
    page_icon="🧠",
    layout="wide",
)

# Hide Streamlit default UI chrome (Deploy button, MainMenu, Header bar, Footer)
HIDE_STREAMLIT_STYLE = """
<style>
    /* Hide top-right Deploy button */
    .stDeployButton,
    .stAppDeployButton,
    [data-testid="stDeployButton"],
    [data-testid="stAppDeployButton"] {
        display: none !important;
        visibility: hidden !important;
    }

    /* Hide main hamburger menu */
    #MainMenu,
    [data-testid="stMainMenu"] {
        display: none !important;
        visibility: hidden !important;
    }

    /* Hide top header bar & toolbar decoration */
    header,
    [data-testid="stHeader"],
    [data-testid="stToolbar"],
    [data-testid="stDecoration"] {
        display: none !important;
        visibility: hidden !important;
        height: 0 !important;
    }

    /* Hide default Streamlit footer */
    footer,
    [data-testid="stFooter"] {
        display: none !important;
        visibility: hidden !important;
    }

    /* Reduce excessive top padding now that the header bar is hidden */
    .block-container {
        padding-top: 2rem !important;
    }
</style>
"""
st.markdown(HIDE_STREAMLIT_STYLE, unsafe_allow_html=True)

MODE_SINGLE = "Single Product Page (Extract Metadata/JSON-LD)"
MODE_GRID = "Category/Collection Page (Extract Product Grid)"

# Internal network constants (abstracted away from non-technical users)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT_SEC = 15

DEFAULT_CHECKED_FIELDS = {
    "Name",
    "Title",
    "Brand",
    "Price",
    "Currency",
    "Image",
    "URL",
    "Availability",
    "SKU",
}

PRICE_REGEX = re.compile(
    r"(?:egp|le|l\.e|usd|\$|€|£|sar|aed|ج\.م|جنيه)?\s*\d[\d,\.]*",
    re.IGNORECASE,
)


# ==============================================================================
# NETWORK & PAGINATION URL HELPERS
# ==============================================================================
def fetch_html(
    url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> str:
    """Fetches HTML from the target URL with realistic browser headers."""
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    }
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.text


def build_paginated_url(base_url: str, page_num: int) -> str:
    """
    Constructs a paginated URL by appending or updating `page=<page_num>`.
    Uses `?page=` if the URL has no query parameters, or `&page=` if query parameters already exist.
    """
    parsed = urllib.parse.urlparse(base_url)
    query_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    query_params["page"] = [str(page_num)]
    new_query = urllib.parse.urlencode(query_params, doseq=True)

    return urllib.parse.urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )


def _clean_text(val: Any) -> str:
    """Converts a raw value into a clean single-line string."""
    if val is None:
        return ""
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        text = BeautifulSoup(val, "html.parser").get_text(separator=" ", strip=True)
        return re.sub(r"\s+", " ", text).strip()
    return ""


# ==============================================================================
# OPTION A: SINGLE PRODUCT AUTO-DETECTION (JSON-LD + OPEN GRAPH)
# ==============================================================================
def _extract_image_from_ld(img_field: Any, base_url: str) -> str:
    """Normalizes Schema.org `image` field (str, list, or ImageObject dict) into a URL."""
    if isinstance(img_field, str):
        return urllib.parse.urljoin(base_url, img_field.strip())
    if isinstance(img_field, list) and img_field:
        return _extract_image_from_ld(img_field[0], base_url)
    if isinstance(img_field, dict):
        url_val = img_field.get("url") or img_field.get("contentUrl") or ""
        return urllib.parse.urljoin(base_url, str(url_val).strip()) if url_val else ""
    return ""


def _extract_brand_from_ld(brand_field: Any) -> str:
    """Normalizes Schema.org `brand` field (str, Brand/Organization dict, or list)."""
    if isinstance(brand_field, str):
        return _clean_text(brand_field)
    if isinstance(brand_field, dict):
        return _clean_text(brand_field.get("name") or brand_field.get("@id") or "")
    if isinstance(brand_field, list) and brand_field:
        return _extract_brand_from_ld(brand_field[0])
    return ""


def _parse_product_node(node: Dict[str, Any], base_url: str) -> Dict[str, str]:
    """Extracts standardized product/schema fields from a JSON-LD node."""
    record: Dict[str, str] = {}

    name = _clean_text(node.get("name") or node.get("headline"))
    if name:
        record["Name"] = name

    brand = _extract_brand_from_ld(node.get("brand") or node.get("manufacturer"))
    if brand:
        record["Brand"] = brand

    offers = node.get("offers")
    offer_dict: Dict[str, Any] = {}
    if isinstance(offers, dict):
        offer_dict = offers
    elif isinstance(offers, list) and offers:
        first_offer = offers[0]
        if isinstance(first_offer, dict):
            offer_dict = first_offer

    price = _clean_text(
        offer_dict.get("price")
        or offer_dict.get("lowPrice")
        or offer_dict.get("highPrice")
        or node.get("price")
    )
    if price:
        record["Price"] = price

    currency = _clean_text(offer_dict.get("priceCurrency") or node.get("priceCurrency"))
    if currency:
        record["Currency"] = currency

    availability = _clean_text(offer_dict.get("availability"))
    if availability:
        record["Availability"] = availability.rstrip("/").split("/")[-1]

    image_url = _extract_image_from_ld(node.get("image") or node.get("thumbnailUrl"), base_url)
    if image_url:
        record["Image"] = image_url

    description = _clean_text(node.get("description"))
    if description:
        record["Description"] = description

    sku = _clean_text(node.get("sku") or node.get("mpn") or node.get("productID"))
    if sku:
        record["SKU"] = sku

    category = _clean_text(node.get("category"))
    if category:
        record["Category"] = category

    rating_obj = node.get("aggregateRating")
    if isinstance(rating_obj, dict):
        rating_val = _clean_text(rating_obj.get("ratingValue"))
        if rating_val:
            record["Rating"] = rating_val

    page_url = _clean_text(node.get("url") or offer_dict.get("url"))
    if page_url:
        record["URL"] = urllib.parse.urljoin(base_url, page_url)

    return record


def _collect_jsonld_nodes(data: Any) -> List[Dict[str, Any]]:
    """Recursively flattens JSON-LD structures (@graph, ItemList, arrays) into candidate dicts."""
    collected: List[Dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            collected.extend(_collect_jsonld_nodes(item))
    elif isinstance(data, dict):
        if "@graph" in data and isinstance(data["@graph"], list):
            collected.extend(_collect_jsonld_nodes(data["@graph"]))

        node_type = str(data.get("@type", "")).lower()
        if "itemlist" in node_type and isinstance(data.get("itemListElement"), list):
            for el in data["itemListElement"]:
                if isinstance(el, dict):
                    inner_item = el.get("item")
                    if isinstance(inner_item, dict):
                        collected.append(inner_item)
                    else:
                        collected.append(el)
        else:
            collected.append(data)
    return collected


def extract_json_ld_records(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """Looks for `application/ld+json` scripts and extracts structured Schema.org/Product records."""
    scripts = soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)})
    product_records: List[Dict[str, str]] = []
    fallback_records: List[Dict[str, str]] = []

    for script in scripts:
        raw_text = script.string or script.get_text()
        if not raw_text or not raw_text.strip():
            continue
        try:
            parsed_json = json.loads(raw_text.strip())
        except json.JSONDecodeError:
            continue

        nodes = _collect_jsonld_nodes(parsed_json)
        for node in nodes:
            node_type = str(node.get("@type", "")).lower()
            parsed = _parse_product_node(node, base_url)
            if not parsed:
                continue

            if "product" in node_type or "Price" in parsed or "Brand" in parsed:
                product_records.append(parsed)
            elif len(parsed) >= 2:
                fallback_records.append(parsed)

    return product_records if product_records else fallback_records


def extract_meta_fallback(soup: BeautifulSoup, base_url: str) -> Dict[str, str]:
    """Fallback extractor using Open Graph (`og:*`), `product:*` meta tags, and standard HTML tags."""
    record: Dict[str, str] = {}

    def get_meta(prop_names: List[str]) -> str:
        for name in prop_names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find(
                "meta", attrs={"name": name}
            )
            if tag and tag.get("content"):
                val = _clean_text(tag.get("content"))
                if val:
                    return val
        return ""

    title = get_meta(["og:title", "twitter:title", "title"])
    if not title:
        h1_tag = soup.find("h1")
        if h1_tag:
            title = _clean_text(h1_tag.get_text())
    if not title and soup.title and soup.title.string:
        title = _clean_text(soup.title.string)
    if title:
        record["Name"] = title

    price = get_meta(["product:price:amount", "og:price:amount", "twitter:data1", "price"])
    if price:
        record["Price"] = price

    currency = get_meta(["product:price:currency", "og:price:currency", "currency"])
    if currency:
        record["Currency"] = currency

    brand = get_meta(["product:brand", "og:brand", "author", "og:site_name"])
    if brand:
        record["Brand"] = brand

    image = get_meta(["og:image", "og:image:secure_url", "twitter:image"])
    if image:
        record["Image"] = urllib.parse.urljoin(base_url, image)

    description = get_meta(["og:description", "twitter:description", "description"])
    if description:
        record["Description"] = description

    availability = get_meta(["product:availability", "og:availability"])
    if availability:
        record["Availability"] = availability

    url_val = get_meta(["og:url"])
    if not url_val:
        canon = soup.find("link", attrs={"rel": "canonical"})
        if canon and canon.get("href"):
            url_val = _clean_text(canon.get("href"))
    record["URL"] = urllib.parse.urljoin(base_url, url_val) if url_val else base_url

    return record


def analyze_single_product_page(html: str, base_url: str) -> Tuple[List[Dict[str, str]], str]:
    """Extracts JSON-LD + Open Graph metadata for Single Product Page mode."""
    soup = BeautifulSoup(html, "html.parser")
    ld_records = extract_json_ld_records(soup, base_url)
    meta_record = extract_meta_fallback(soup, base_url)

    if ld_records:
        if len(ld_records) == 1 and meta_record:
            merged = dict(meta_record)
            merged.update(ld_records[0])
            return [merged], "JSON-LD (Schema.org) + Open Graph"
        return ld_records, f"JSON-LD (Schema.org — {len(ld_records)} items detected)"

    if meta_record:
        return [meta_record], "Open Graph (og:) & HTML Meta Tags Fallback"

    return [], "No Structured Data Found"


# ==============================================================================
# OPTION B: CATEGORY / PRODUCT GRID AUTO-DETECTION ENGINE
# ==============================================================================
def _css_escape_class(cls_name: str) -> str:
    """Safely formats a CSS class token for BeautifulSoup's CSS selector engine."""
    return re.sub(r"([^a-zA-Z0-9_-])", r"\\\1", cls_name)


def _extract_image_src(el: Optional[Tag], base_url: str) -> str:
    """Extracts image URL from an `img` tag or nested `img` (handling lazy-loaded attributes)."""
    if el is None:
        return ""
    img_tag = el if el.name == "img" else el.find("img")
    if img_tag is None:
        return ""

    for attr in ("src", "data-src", "data-lazy-src", "data-original"):
        val = img_tag.get(attr)
        if val and isinstance(val, str) and not val.strip().startswith("data:image/svg"):
            clean_val = val.strip()
            if clean_val.startswith("//"):
                return f"https:{clean_val}"
            return urllib.parse.urljoin(base_url, clean_val)

    srcset = img_tag.get("srcset") or img_tag.get("data-srcset")
    if srcset and isinstance(srcset, str):
        first_src = srcset.split(",")[0].strip().split(" ")[0]
        if first_src:
            if first_src.startswith("//"):
                return f"https:{first_src}"
            return urllib.parse.urljoin(base_url, first_src)

    return ""


def _extract_link_href(el: Optional[Tag], base_url: str) -> str:
    """Extracts `href` from an `a` tag or nested `a` tag."""
    if el is None:
        return ""
    a_tag = el if el.name == "a" else el.find("a", href=True)
    if a_tag and a_tag.get("href"):
        href = str(a_tag.get("href")).strip()
        if href and not href.startswith(("javascript:", "#")):
            return urllib.parse.urljoin(base_url, href)
    return ""


def _detect_child_selectors(containers: List[Tag]) -> Dict[str, str]:
    """
    Inspects sample product containers to determine the most reliable relative CSS selectors
    for Title, Price, Image, and Link.
    """
    sample_containers = containers[:8]

    # 1. Detect Title Selector
    title_candidates = [
        "h2 a",
        "h3 a",
        "h4 a",
        ".card__heading a",
        ".product-card__title",
        ".product-title",
        ".product-item__title",
        ".woocommerce-loop-product__title",
        "h2",
        "h3",
        "h4",
        "[class*='title'] a",
        "[class*='title']",
        "[class*='name'] a",
        "[class*='name']",
        "a[title]",
        "a",
    ]
    best_title_sel = "h2, h3, a"
    for sel in title_candidates:
        matches = 0
        for c in sample_containers:
            try:
                el = c.select_one(sel)
            except Exception:
                continue
            if el:
                txt = el.get_text(" ", strip=True) or str(el.get("title") or "").strip()
                if len(txt) >= 3 and not PRICE_REGEX.fullmatch(txt):
                    matches += 1
        if matches >= max(1, int(len(sample_containers) * 0.6)):
            best_title_sel = sel
            break

    # 2. Detect Price Selector
    price_candidates = [
        ".price-item--sale",
        ".price-item--regular",
        ".price__regular .price-item",
        ".product-price",
        ".price_color",
        ".money",
        ".amount",
        ".price",
        "[class*='price']",
        "span",
    ]
    best_price_sel = ".price, [class*='price']"
    for sel in price_candidates:
        matches = 0
        for c in sample_containers:
            try:
                el = c.select_one(sel)
            except Exception:
                continue
            if el:
                txt = el.get_text(" ", strip=True)
                if txt and re.search(r"\d", txt) and len(txt) <= 60:
                    matches += 1
        if matches >= max(1, int(len(sample_containers) * 0.5)):
            best_price_sel = sel
            break

    # 3. Detect Image Selector
    image_candidates = [
        "img.motion-reduce",
        "img.product-card__image",
        "img.attachment-woocommerce_thumbnail",
        "img",
    ]
    best_img_sel = "img"
    for sel in image_candidates:
        matches = 0
        for c in sample_containers:
            try:
                el = c.select_one(sel)
            except Exception:
                continue
            if el and _extract_image_src(el, "https://example.com"):
                matches += 1
        if matches >= max(1, int(len(sample_containers) * 0.6)):
            best_img_sel = sel
            break

    # 4. Detect Link Selector
    link_candidates = [
        "a[href*='/products/']",
        "a[href*='/product/']",
        "a.full-unstyled-link",
        "h2 a",
        "h3 a",
        "a[href]",
    ]
    best_link_sel = "a"
    for sel in link_candidates:
        matches = 0
        for c in sample_containers:
            try:
                el = c.select_one(sel)
            except Exception:
                continue
            if el and _extract_link_href(el, "https://example.com"):
                matches += 1
        if matches >= max(1, int(len(sample_containers) * 0.6)):
            best_link_sel = sel
            break

    return {
        "title": best_title_sel,
        "price": best_price_sel,
        "image": best_img_sel,
        "link": best_link_sel,
    }


def auto_detect_product_grid(html: str) -> Dict[str, Any]:
    """
    Uses BeautifulSoup to automatically identify repeating product containers on a
    Category/Collection page and infer child CSS selectors for Title, Price, Image, and Link.
    """
    soup = BeautifulSoup(html, "html.parser")

    curated_selectors = [
        "li.grid__item",
        "div.product-card",
        "div.product-item",
        "article.product_pod",
        "li.product",
        "div.product-grid-item",
        "div.card-wrapper",
        "div.product-block",
        "div.product",
        "div.grid__item",
    ]

    keyword_pattern = re.compile(r"(product|item|card|grid|col)", re.IGNORECASE)
    signature_counts: Counter = Counter()

    for tag in soup.find_all(["div", "li", "article"]):
        classes = tag.get("class")
        if not classes or not isinstance(classes, list):
            continue
        for cls in classes:
            if keyword_pattern.search(cls) and not cls.startswith(("js-", "is-", "has-")):
                sig = f"{tag.name}.{_css_escape_class(cls)}"
                signature_counts[sig] += 1

    dynamic_selectors = [sig for sig, count in signature_counts.most_common(25) if count >= 3]

    all_candidate_selectors = []
    for s in curated_selectors + dynamic_selectors:
        if s not in all_candidate_selectors:
            all_candidate_selectors.append(s)

    best_selector = ""
    best_score = -1.0
    best_containers: List[Tag] = []

    for sel in all_candidate_selectors:
        try:
            elements = soup.select(sel)
        except Exception:
            continue

        count = len(elements)
        if count < 2 or count > 250:
            continue

        valid_cards = 0
        cards_with_price = 0
        for el in elements[:15]:
            has_img = bool(el.find("img"))
            has_link = bool(el.find("a", href=True))
            text_content = el.get_text(" ", strip=True)
            has_text = len(text_content) >= 5
            has_price = bool(re.search(r"\d", text_content))

            if has_img and has_link and has_text:
                valid_cards += 1
            if has_price:
                cards_with_price += 1

        sample_size = min(count, 15)
        validity_ratio = valid_cards / sample_size
        price_ratio = cards_with_price / sample_size

        if validity_ratio < 0.6:
            continue

        keyword_bonus = 1.3 if any(k in sel.lower() for k in ("product", "card", "pod")) else 1.0
        score = (validity_ratio * 50 + price_ratio * 30 + min(count, 48)) * keyword_bonus

        if score > best_score:
            best_score = score
            best_selector = sel
            best_containers = elements

    if not best_selector or not best_containers:
        return {
            "detected": False,
            "container_selector": "div.product-card, li.grid__item, article.product_pod",
            "title_selector": "h2, h3, .product-title, a",
            "price_selector": ".price, [class*='price']",
            "image_selector": "img",
            "link_selector": "a",
            "count": 0,
        }

    child_sels = _detect_child_selectors(best_containers)
    return {
        "detected": True,
        "container_selector": best_selector,
        "title_selector": child_sels["title"],
        "price_selector": child_sels["price"],
        "image_selector": child_sels["image"],
        "link_selector": child_sels["link"],
        "count": len(best_containers),
    }


def extract_grid_items(
    html: str,
    base_url: str,
    container_selector: str,
    title_selector: str,
    price_selector: str,
    image_selector: str,
    link_selector: str,
    limit: Optional[int] = None,
) -> Tuple[List[Dict[str, str]], int]:
    """
    Loops through all matching containers on a single page using the confirmed CSS selectors
    and extracts Product Title, Price, Image, and Product Link.
    Returns: (extracted_rows, total_containers_matched)
    """
    if not html or not container_selector.strip():
        return [], 0

    soup = BeautifulSoup(html, "html.parser")
    try:
        containers = soup.select(container_selector.strip())
    except Exception:
        return [], 0

    total_matched = len(containers)
    target_containers = containers[:limit] if limit else containers
    rows: List[Dict[str, str]] = []

    for c in target_containers:
        # 1. Title
        title_val = ""
        if title_selector.strip():
            try:
                t_el = c.select_one(title_selector.strip())
                if t_el:
                    title_val = (
                        _clean_text(t_el.get("title"))
                        if t_el.name == "a"
                        and t_el.get("title")
                        and len(t_el.get_text(strip=True)) < 5
                        else _clean_text(t_el.get_text(" ", strip=True))
                    )
            except Exception:
                pass

        # 2. Price
        price_val = ""
        if price_selector.strip():
            try:
                p_el = c.select_one(price_selector.strip())
                if p_el:
                    price_val = _clean_text(p_el.get_text(" ", strip=True))
            except Exception:
                pass

        # 3. Image
        img_val = ""
        if image_selector.strip():
            try:
                i_el = c.select_one(image_selector.strip())
                img_val = _extract_image_src(i_el, base_url)
            except Exception:
                pass

        # 4. Link
        link_val = ""
        if link_selector.strip():
            try:
                l_el = c.select_one(link_selector.strip())
                link_val = _extract_link_href(l_el, base_url)
            except Exception:
                pass

        if any([title_val, price_val, img_val, link_val]):
            rows.append(
                {
                    "Title": title_val,
                    "Price": price_val,
                    "Image": img_val,
                    "Link": link_val,
                }
            )

    return rows, total_matched


def scrape_paginated_grid(
    base_url: str,
    initial_html: str,
    container_selector: str,
    title_selector: str,
    price_selector: str,
    image_selector: str,
    link_selector: str,
    max_pages: int,
    page_delay_sec: float,
    status_placeholder: Any,
    progress_bar: Any,
) -> Tuple[pd.DataFrame, int, str]:
    """
    Iterates through paginated URLs (`?page=1`, `?page=2`, ... or `&page=1`, `&page=2`, ...)
    and aggregates product rows into a single Pandas DataFrame.
    """
    all_rows: List[Dict[str, str]] = []
    prev_page_signatures: List[Tuple[str, str, str]] = []
    seen_signatures: Set[Tuple[str, str, str]] = set()
    pages_scraped = 0
    stop_reason = f"Reached Max Pages limit ({max_pages})"

    for page_num in range(1, max_pages + 1):
        page_url = build_paginated_url(base_url, page_num)

        status_placeholder.info(
            f"🔄 **Scraping page {page_num} of {max_pages}** (`{page_url}`) — "
            f"extracted **{len(all_rows)}** items so far..."
        )
        progress_bar.progress(min(page_num / max(max_pages, 1), 1.0))

        if page_num == 1 and initial_html and "page=" not in urllib.parse.urlparse(base_url).query:
            html_content = initial_html
        else:
            if page_num > 1 and page_delay_sec > 0:
                time.sleep(page_delay_sec)
            try:
                html_content = fetch_html(page_url)
            except requests.exceptions.HTTPError as http_err:
                code = http_err.response.status_code if http_err.response is not None else "Error"
                if code == 404:
                    stop_reason = f"Stopped at page {page_num} (HTTP 404 Not Found — end of pages)"
                else:
                    stop_reason = f"Stopped at page {page_num} (HTTP {code})"
                break
            except requests.exceptions.RequestException as req_err:
                stop_reason = f"Stopped at page {page_num} (Network error: {req_err})"
                break

        page_rows, container_count = extract_grid_items(
            html=html_content,
            base_url=page_url,
            container_selector=container_selector,
            title_selector=title_selector,
            price_selector=price_selector,
            image_selector=image_selector,
            link_selector=link_selector,
            limit=None,
        )

        # Break condition 1: 0 product containers or 0 items extracted
        if container_count == 0 or not page_rows:
            stop_reason = f"Stopped at page {page_num} (0 product containers found)"
            break

        current_signatures = [
            (r.get("Title", ""), r.get("Link", ""), r.get("Price", "")) for r in page_rows
        ]

        # Break condition 2: Current page items duplicate the previous page or are 100% already seen
        if current_signatures == prev_page_signatures or all(
            sig in seen_signatures for sig in current_signatures
        ):
            stop_reason = (
                f"Stopped at page {page_num} (duplicate items detected — end of catalog)"
            )
            break

        new_items_on_page = 0
        for row, sig in zip(page_rows, current_signatures):
            if sig not in seen_signatures:
                seen_signatures.add(sig)
                all_rows.append(row)
                new_items_on_page += 1

        prev_page_signatures = current_signatures
        pages_scraped += 1

        status_placeholder.info(
            f"✅ **Scraped page {page_num}** (+{new_items_on_page} items) — "
            f"Total extracted: **{len(all_rows)}** items"
        )

    progress_bar.progress(1.0)
    return pd.DataFrame(all_rows), pages_scraped, stop_reason


# ==============================================================================
# OPTIONAL CUSTOM CSS SELECTOR ENGINE (FOR SINGLE PAGE FALLBACK)
# ==============================================================================
def extract_element_value(element: Optional[Tag], extract_type: str, base_url: str) -> str:
    """Extracts text, image `src`, or link `href` from a BeautifulSoup Tag."""
    if element is None:
        return ""
    if extract_type == "Text":
        return element.get_text(separator=" ", strip=True)
    if extract_type == "Image (src)":
        return _extract_image_src(element, base_url)
    if extract_type == "Link (href)":
        return _extract_link_href(element, base_url)

    if element.name == "img" or element.get("src") or element.get("data-src"):
        return _extract_image_src(element, base_url)
    if element.name == "a" and not element.get_text(strip=True) and element.get("href"):
        return _extract_link_href(element, base_url)

    text_val = element.get_text(separator=" ", strip=True)
    if text_val:
        return text_val
    return _extract_link_href(element, base_url)


def build_single_page_dataframe(
    detected_records: List[Dict[str, str]],
    selected_fields: List[str],
    html: str,
    base_url: str,
    custom_selectors: List[Dict[str, str]],
) -> pd.DataFrame:
    """Combines selected auto-detected fields and optional custom CSS selectors."""
    columns_data: Dict[str, List[str]] = {}

    if detected_records and selected_fields:
        for field in selected_fields:
            columns_data[field] = [rec.get(field, "") for rec in detected_records]

    valid_custom = [
        c
        for c in custom_selectors
        if c.get("name", "").strip() and c.get("selector", "").strip()
    ]
    if valid_custom and html:
        soup = BeautifulSoup(html, "html.parser")
        for col in valid_custom:
            col_name = col["name"].strip()
            selector = col["selector"].strip()
            extract_type = col.get("extract_type", "Auto (Smart)")
            elements = soup.select(selector)
            columns_data[col_name] = [
                extract_element_value(el, extract_type, base_url) for el in elements
            ]

    if not columns_data:
        return pd.DataFrame()

    max_len = max((len(v) for v in columns_data.values()), default=0)
    for col_name, values in columns_data.items():
        if len(values) < max_len:
            if len(values) == 1 and max_len > 1:
                columns_data[col_name] = values * max_len
            else:
                columns_data[col_name].extend([""] * (max_len - len(values)))

    return pd.DataFrame(columns_data)


# ==============================================================================
# SESSION STATE INITIALIZATION
# ==============================================================================
defaults = {
    "analyzed": False,
    "analyzed_url": "",
    "analyzed_mode": MODE_SINGLE,
    "raw_html": "",
    "detected_records": [],
    "detected_fields": {},
    "detection_source": "",
    "custom_columns": [],
    "grid_info": {},
    "pages_scraped": 1,
    "final_df": None,
}
for key, default_val in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = default_val


def add_custom_column() -> None:
    st.session_state.custom_columns.append(
        {"name": "", "selector": "", "extract_type": "Auto (Smart)"}
    )


def remove_custom_column(index: int) -> None:
    if 0 <= index < len(st.session_state.custom_columns):
        st.session_state.custom_columns.pop(index)


def shutdown_server() -> None:
    """Terminates the local Python/Streamlit server process cleanly."""
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except Exception:
        pass
    os._exit(0)


# ==============================================================================
# SIDEBAR (SIMPLIFIED FOR NON-TECHNICAL USERS + SHUTDOWN BUTTON)
# ==============================================================================
with st.sidebar:
    st.header("⚙️ Scraper Controls")
    page_delay_sec = st.slider(
        "Polite Delay Between Pages (sec)",
        min_value=0.0,
        max_value=5.0,
        value=0.5,
        step=0.25,
        help="Wait time between fetching consecutive pages so websites don't block your connection.",
    )

    st.divider()
    st.subheader("📖 How to Use")
    st.markdown(
        "1. **Choose Mode**:\n"
        "   - *Single Product Page* for individual item links.\n"
        "   - *Category/Collection Page* for multi-product store grids.\n"
        "2. **Analyze Page**: Paste the website link and click **Analyze Page**.\n"
        "3. **Generate & Download**: Review the detected fields or product preview, "
        "click **Generate Data**, and save your Excel-ready **CSV** file."
    )

    st.divider()
    shutdown_clicked = st.button(
        "🛑 Shutdown Application",
        type="primary",
        use_container_width=True,
        help="Completely stops the background application server so you can exit cleanly.",
    )
    if shutdown_clicked:
        st.warning("👋 Shutting down application... You can safely close this browser tab now.")
        st.toast("Application server stopped.", icon="🛑")
        time.sleep(1.0)
        shutdown_server()


# ==============================================================================
# MAIN APPLICATION UI
# ==============================================================================
st.title("🧠 Smart E-Commerce Web Scraper")
st.markdown(
    "Automatically extract product data from **Single Product Pages** or multi-page "
    "**Category / Collection Grids** with one click."
)

# ------------------------------------------------------------------------------
# STEP 1: SCRAPING MODE & URL ANALYSIS
# ------------------------------------------------------------------------------
st.subheader("Step 1: Choose Scraping Mode & Analyze Page")

scrape_mode = st.radio(
    "Target Page Type:",
    options=[MODE_SINGLE, MODE_GRID],
    horizontal=True,
)

url_col, btn_col = st.columns([4, 1])
with url_col:
    placeholder_url = (
        "https://sourcebeauty.com/products/..."
        if scrape_mode == MODE_SINGLE
        else "https://sourcebeauty.com/collections/..."
    )
    target_url = st.text_input(
        "Target Website URL",
        placeholder=placeholder_url,
        label_visibility="collapsed",
    )

with btn_col:
    analyze_clicked = st.button("🔍 Analyze Page", type="primary", use_container_width=True)

if analyze_clicked:
    clean_url = target_url.strip()
    if not clean_url:
        st.error("Please enter a Target Website URL.")
    elif not clean_url.startswith(("http://", "https://")):
        st.error("URL must start with `http://` or `https://`.")
    else:
        with st.spinner(f"Fetching and analyzing `{clean_url}`..."):
            try:
                html_text = fetch_html(clean_url)

                st.session_state.analyzed = True
                st.session_state.analyzed_url = clean_url
                st.session_state.analyzed_mode = scrape_mode
                st.session_state.raw_html = html_text
                st.session_state.pages_scraped = 1
                st.session_state.final_df = None

                if scrape_mode == MODE_SINGLE:
                    records, source_label = analyze_single_product_page(html_text, clean_url)
                    field_samples: Dict[str, str] = {}
                    for rec in records:
                        for k, v in rec.items():
                            if k not in field_samples and v:
                                field_samples[k] = v

                    st.session_state.detected_records = records
                    st.session_state.detected_fields = field_samples
                    st.session_state.detection_source = source_label
                else:
                    grid_info = auto_detect_product_grid(html_text)
                    st.session_state.grid_info = grid_info
                    st.session_state["grid_container_sel"] = grid_info["container_selector"]
                    st.session_state["grid_title_sel"] = grid_info["title_selector"]
                    st.session_state["grid_price_sel"] = grid_info["price_selector"]
                    st.session_state["grid_image_sel"] = grid_info["image_selector"]
                    st.session_state["grid_link_sel"] = grid_info["link_selector"]

            except requests.exceptions.HTTPError as http_err:
                status_code = (
                    http_err.response.status_code if http_err.response is not None else "Unknown"
                )
                st.error(
                    f"HTTP {status_code} Error: The website blocked or rejected the request ({http_err})."
                )
            except requests.exceptions.RequestException as req_err:
                st.error(f"Network Error while connecting to `{clean_url}`: {req_err}")
            except Exception as exc:
                st.error(f"Unexpected error while analyzing page: {exc}")

# ------------------------------------------------------------------------------
# STEP 2 & 3: DYNAMIC CONFIRMATION & DATA GENERATION
# ------------------------------------------------------------------------------
if st.session_state.analyzed:
    st.divider()

    # ==========================================================================
    # WORKFLOW A: SINGLE PRODUCT PAGE
    # ==========================================================================
    if st.session_state.analyzed_mode == MODE_SINGLE:
        st.subheader("Step 2: Select Fields to Extract")

        detected_fields: Dict[str, str] = st.session_state.detected_fields
        selected_fields: List[str] = []

        if detected_fields:
            st.info(
                f"**Detection Source:** `{st.session_state.detection_source}` — "
                f"Found **{len(detected_fields)}** structured fields across "
                f"**{len(st.session_state.detected_records)}** item(s)."
            )

            grid_cols = st.columns(2)
            for idx, (field_name, sample_val) in enumerate(detected_fields.items()):
                default_checked = field_name in DEFAULT_CHECKED_FIELDS or idx < 5
                preview = sample_val if len(sample_val) <= 75 else f"{sample_val[:72]}..."
                with grid_cols[idx % 2]:
                    is_checked = st.checkbox(
                        f"**{field_name}** — `{preview}`",
                        value=default_checked,
                        key=f"chk_{st.session_state.analyzed_url}_{field_name}",
                    )
                    if is_checked:
                        selected_fields.append(field_name)
        else:
            st.warning(
                "No `application/ld+json` or Open Graph (`og:`) structured data was found on this page. "
                "Use the **Advanced Fallback: Custom CSS Selectors** section below."
            )

        with st.expander(
            "🛠️ Advanced Fallback: Add Custom CSS Selectors (Optional)",
            expanded=not bool(detected_fields),
        ):
            st.caption(
                "Need an extra element that wasn't auto-detected? Add custom CSS selectors below."
            )
            st.button("➕ Add Custom Selector", on_click=add_custom_column)

            extract_options = ["Auto (Smart)", "Text", "Image (src)", "Link (href)"]
            for idx, col_item in enumerate(st.session_state.custom_columns):
                c1, c2, c3, c4 = st.columns([3, 4, 2.5, 0.8])
                with c1:
                    st.session_state.custom_columns[idx]["name"] = st.text_input(
                        f"Custom Column #{idx + 1}",
                        value=col_item["name"],
                        key=f"cust_name_{idx}",
                        placeholder="e.g., Discount Price",
                    )
                with c2:
                    st.session_state.custom_columns[idx]["selector"] = st.text_input(
                        f"CSS Selector #{idx + 1}",
                        value=col_item["selector"],
                        key=f"cust_sel_{idx}",
                        placeholder="e.g., span.sale-price",
                    )
                with c3:
                    curr_type = col_item.get("extract_type", "Auto (Smart)")
                    t_idx = (
                        extract_options.index(curr_type) if curr_type in extract_options else 0
                    )
                    st.session_state.custom_columns[idx]["extract_type"] = st.selectbox(
                        f"Extract Type #{idx + 1}",
                        options=extract_options,
                        index=t_idx,
                        key=f"cust_type_{idx}",
                    )
                with c4:
                    st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
                    st.button(
                        "🗑️",
                        key=f"cust_del_{idx}",
                        help="Remove this custom selector",
                        on_click=remove_custom_column,
                        args=(idx,),
                    )

        st.divider()
        st.subheader("Step 3: Scrape & Export")

        if st.button("⚡ Generate Data", type="primary", use_container_width=True):
            has_custom = any(
                c.get("name", "").strip() and c.get("selector", "").strip()
                for c in st.session_state.custom_columns
            )
            if not selected_fields and not has_custom:
                st.error(
                    "Please select at least one auto-detected field or provide a valid Custom CSS Selector."
                )
            else:
                df_generated = build_single_page_dataframe(
                    detected_records=st.session_state.detected_records,
                    selected_fields=selected_fields,
                    html=st.session_state.raw_html,
                    base_url=st.session_state.analyzed_url,
                    custom_selectors=st.session_state.custom_columns,
                )
                st.session_state.pages_scraped = 1
                st.session_state.final_df = df_generated
                if df_generated.empty:
                    st.warning("No data rows were generated. Check your selected fields or selectors.")
                else:
                    st.success(
                        f"Generated **{len(df_generated)}** row(s) with "
                        f"**{len(df_generated.columns)}** column(s)!"
                    )

    # ==========================================================================
    # WORKFLOW B: CATEGORY / COLLECTION PAGE (PRODUCT GRID + PAGINATION)
    # ==========================================================================
    else:
        st.subheader("Step 2: Confirm Grid Selectors & First Product Preview")
        grid_info: Dict[str, Any] = st.session_state.grid_info

        if grid_info.get("detected"):
            st.success(
                f"Auto-detected **{grid_info['count']}** repeating product containers on Page 1 using "
                f"`{grid_info['container_selector']}`! You can tweak any selector below and "
                "watch the first-product preview update live."
            )
        else:
            st.warning(
                "Could not automatically confirm a repeating product grid. "
                "Please adjust the CSS selectors below to match your page's HTML structure."
            )

        sel_col, preview_col = st.columns([1.25, 1])

        with sel_col:
            st.markdown("#### 🎯 Auto-Detected CSS Selectors (Editable)")
            container_sel = st.text_input(
                "Repeating Product Container Selector",
                key="grid_container_sel",
                help="Wrapper element that repeats for every product card on the page (e.g. li.grid__item, div.product-card).",
            )
            title_sel = st.text_input(
                "Product Title Selector (inside container)",
                key="grid_title_sel",
                help="Relative CSS selector for the product title inside each container.",
            )
            price_sel = st.text_input(
                "Price Selector (inside container)",
                key="grid_price_sel",
                help="Relative CSS selector for the product price inside each container.",
            )
            image_sel = st.text_input(
                "Image Selector (inside container)",
                key="grid_image_sel",
                help="Relative CSS selector for the product image (`img`) inside each container.",
            )
            link_sel = st.text_input(
                "Product Link Selector (inside container)",
                key="grid_link_sel",
                help="Relative CSS selector for the product anchor link (`a`) inside each container.",
            )

        preview_rows, live_container_count = extract_grid_items(
            html=st.session_state.raw_html,
            base_url=st.session_state.analyzed_url,
            container_selector=container_sel,
            title_selector=title_sel,
            price_selector=price_sel,
            image_selector=image_sel,
            link_selector=link_sel,
            limit=1,
        )

        with preview_col:
            st.markdown(
                f"#### 👁️ First Detected Product Preview ({live_container_count} items on Page 1)"
            )
            if preview_rows:
                first_prod = preview_rows[0]
                with st.container(border=True):
                    if first_prod.get("Image"):
                        st.image(first_prod["Image"], width=180)
                    st.markdown(f"**Title:** {first_prod.get('Title') or '*(Not matched)*'}")
                    st.markdown(f"**Price:** `{first_prod.get('Price') or '(Not matched)'}`")
                    st.markdown(f"**Image URL:** `{first_prod.get('Image') or '(Not matched)'}`")
                    if first_prod.get("Link"):
                        st.markdown(
                            f"**Product Link:** [{first_prod['Link']}]({first_prod['Link']})"
                        )
                    else:
                        st.markdown("**Product Link:** `*(Not matched)*`")
            else:
                st.info(
                    "No product matched the current Container Selector. "
                    "Try tweaking the **Repeating Product Container Selector** on the left."
                )

        st.divider()
        st.subheader("Step 3: Scrape All Pages & Export")

        pag_col1, pag_col2 = st.columns([1, 2])
        with pag_col1:
            max_pages = int(
                st.number_input(
                    "Max Pages to Scrape",
                    min_value=1,
                    max_value=500,
                    value=50,
                    step=1,
                    help=(
                        "Safety limit on how many pages (?page=1, ?page=2, ...) to crawl. "
                        "The scraper stops automatically earlier if a page returns 0 products, "
                        "HTTP 404, or duplicate items."
                    ),
                )
            )
        with pag_col2:
            example_page2 = build_paginated_url(st.session_state.analyzed_url, 2)
            st.caption(
                f"**Automatic Pagination Pattern:** `{example_page2}`\n\n"
                "Automatically stops when a page returns **0 containers**, **HTTP 404**, "
                "or **duplicate products**, or when **Max Pages** is reached."
            )

        if st.button("⚡ Generate Data (Paginated)", type="primary", use_container_width=True):
            status_box = st.empty()
            progress_bar = st.progress(0.0)

            df_grid, pages_count, stop_reason = scrape_paginated_grid(
                base_url=st.session_state.analyzed_url,
                initial_html=st.session_state.raw_html,
                container_selector=container_sel,
                title_selector=title_sel,
                price_selector=price_sel,
                image_selector=image_sel,
                link_selector=link_sel,
                max_pages=max_pages,
                page_delay_sec=page_delay_sec,
                status_placeholder=status_box,
                progress_bar=progress_bar,
            )

            st.session_state.pages_scraped = pages_count
            st.session_state.final_df = df_grid

            if df_grid.empty:
                status_box.warning(
                    "No products could be extracted with the current selectors. "
                    "Please check your container and field CSS selectors above."
                )
            else:
                status_box.success(
                    f"🎉 Finished! Extracted **{len(df_grid)}** total unique products across "
                    f"**{pages_count}** page(s). ({stop_reason})"
                )

    # ==========================================================================
    # SHARED DATAFRAME DISPLAY & CSV DOWNLOAD
    # ==========================================================================
    if st.session_state.final_df is not None and not st.session_state.final_df.empty:
        df_out: pd.DataFrame = st.session_state.final_df

        m1, m2, m3 = st.columns(3)
        m1.metric("Total Rows Extracted", len(df_out))
        m2.metric("Pages Scraped", st.session_state.pages_scraped)
        m3.metric("Total Columns", len(df_out.columns))

        st.dataframe(df_out, use_container_width=True, height=450)

        csv_bytes = df_out.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="📥 Download CSV",
            data=csv_bytes,
            file_name="smart_scraped_products.csv",
            mime="text/csv",
            use_container_width=True,
        )
