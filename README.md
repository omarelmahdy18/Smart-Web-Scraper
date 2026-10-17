# 🧠 Smart E-Commerce Web Scraper

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-GUI-FF4B4B?logo=streamlit&logoColor=white)
![Asyncio](https://img.shields.io/badge/Asyncio-aiohttp-009688)
![BeautifulSoup4](https://img.shields.io/badge/BeautifulSoup4-HTML%20Parser-4CAF50)
![Pandas](https://img.shields.io/badge/Pandas-Data%20Pipeline-150458?logo=pandas&logoColor=white)

An enterprise-grade **E-Commerce Data Extraction & Catalog Matching Suite** engineered in Python. This project combines an interactive **Streamlit Smart Web Scraper GUI** (capable of zero-configuration `JSON-LD` / `Open Graph` metadata detection, automated product grid discovery, and multi-page pagination) with a high-concurrency **Asynchronous CLI Catalog Matcher** (`asyncio` + `aiohttp`) and standalone **Windows `.exe` packaging** via `PyInstaller`.

---

## ✨ Key Features

### 1. 🔍 Intelligent Auto-Detection (`JSON-LD` & `Open Graph`)
- **Single Product Mode**: Automatically inspects `<script type="application/ld+json">` (`Schema.org/Product`, `ItemList`, `@graph`, and `Offer` schemas) to extract structured attributes (`Name`, `Brand`, `Price`, `Currency`, `Availability`, `SKU`, `Rating`, `Image`, `Description`, and `URL`).
- **Smart Metadata Fallback**: Seamlessly falls back to Open Graph (`og:*`), `product:*` meta tags, Twitter Cards, and semantic HTML tags when `JSON-LD` is absent or incomplete.
- **Interactive Field Selection**: Dynamically renders checkboxes with live sample previews and an optional custom CSS selector fallback.

### 2. 🕸️ Automated Product Grid Discovery & Pagination
- **Category / Collection Grid Detection**: Uses heuristic DOM scoring in `BeautifulSoup` to automatically identify repeating product card containers (`li.grid__item`, `div.product-card`, `article.product_pod`, etc.) and infer child CSS selectors for **Title**, **Price**, **Image**, and **Product Link**.
- **Live First-Product Preview**: Renders the first detected product card (including image thumbnail and extracted fields) in real time as you review or fine-tune CSS selectors.
- **Automatic Multi-Page Pagination**: Crawls paginated category URLs (`?page=1`, `?page=2`, or `&page=N`) with polite rate-limiting delays and smart termination rules (stops automatically on `HTTP 404`, `0` containers, or duplicate catalog pages).

### 3. ⚡ High-Performance Asynchronous Catalog Pipeline (`photo_scrabbing.py`)
- **Concurrent I/O (`asyncio` + `aiohttp`)**: Processes large product catalogs concurrently using bounded `asyncio.Semaphore` workers and polite request delays (`asyncio.sleep`).
- **Strict Attribute Validation**: Verifies brand synonyms (English & Arabic), product types, volume/weight sizes (`ml`, `g`, `oz`), and filters out unrequested bundles/sets.
- **Externalized Configuration & CLI**: Fully driven by `config.json` and `argparse` CLI flags (`--input`, `--output`, `--delay`, `--limit`, `--concurrency`) with `tqdm.asyncio` progress bars and structured `UTF-8` file/console logging.

### 4. 📦 Standalone Desktop Packaging (`PyInstaller`)
- **Distraction-Free Desktop UI**: Custom CSS injection hides Streamlit developer chrome (Deploy button, Hamburger menu, header bar, and footer) and provides a one-click **Shutdown Application** button in the sidebar to cleanly terminate the local server process.
- **One-Click `.EXE` Compilation**: Includes `run_app.py` and `setup.bat` to package the entire Streamlit web application into a standalone Windows executable (`SmartWebScraper.exe`).

---

## 📂 Project Structure

```text
├── app.py                  # Streamlit Smart Web Scraper GUI (Single Page + Grid + Pagination)
├── run_app.py              # PyInstaller wrapper entrypoint for standalone .exe execution
├── photo_scrabbing.py      # High-concurrency async catalog matcher CLI (asyncio + aiohttp)
├── config.json             # Externalized store registry, brand/type synonyms, and paths
├── setup.bat               # Automated PyInstaller build script for Windows (.exe)
├── start_app.bat           # Quick one-click local launcher
├── requirements.txt        # Python package dependencies
├── .gitignore              # Git ignore rules for virtualenvs, builds, logs, and CSVs
└── README.md               # Project documentation
```

---

## 🚀 Getting Started

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Launch the Interactive Streamlit GUI
```bash
python -m streamlit run app.py
```
Or simply double-click **`start_app.bat`** on Windows.

### 3. Run the Asynchronous CLI Catalog Scraper
```bash
# Run with default settings from config.json
python photo_scrabbing.py

# Process entire catalog with 8 concurrent workers and a 1.5s polite delay
python photo_scrabbing.py --limit 0 --concurrency 8 --delay 1.5
```

### 4. Build the Standalone Windows Executable (`.exe`)
Double-click **`setup.bat`** or run:
```powershell
.\setup.bat
```
The compiled executable (`SmartWebScraper.exe`) will be placed inside the `dist/` directory.

---



