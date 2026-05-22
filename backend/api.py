"""
Backend API — streaming search + continuous ML discovery.

Run: uvicorn backend.api:app --reload
"""
import json
import logging
import sqlite3
import threading
import time
import datetime
import sys
from pathlib import Path
from typing import Optional, List, Generator

try:
    from kafka import KafkaProducer
except Exception:
    KafkaProducer = None

_producer = None
try:
    if KafkaProducer is not None:
        _producer = KafkaProducer(
            bootstrap_servers=['localhost:9094'],
            value_serializer=lambda x: json.dumps(x, ensure_ascii=False).encode('utf-8'),
            request_timeout_ms=3000,
            api_version_auto_timeout_ms=3000,
        )
except Exception as e:
    logging.getLogger("api").warning(
        f"Kafka unavailable ({type(e).__name__}); falling back to direct SQLite writes. "
        "Search will still work from cached data and fresh scrape results."
    )
    _producer = None

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.append(str(ROOT))

import clothing_scraper

# Lazy-import ml_scraper so the API starts even if ddgs isn't installed yet
def _try_import_ml():
    try:
        import ml_scraper
        return ml_scraper
    except Exception:
        return None

def _try_import_recommendation_ml():
    try:
        from . import recommendation_ml
        return recommendation_ml
    except Exception as e:
        _api_logger.warning(f"recommendation_ml import failed: {type(e).__name__}: {e}")
        return None

DB_PATH = ROOT / "data" / "products.db"

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
_api_logger = logging.getLogger("api")

app = FastAPI(title="Clothing Aggregator API", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# ── Startup / Shutdown lifecycle ──────────────────────────────────────────────

_continuous_discovery_thread: threading.Thread | None = None


@app.on_event("startup")
def on_startup():
    """Inject missing ML brands and start continuous background discovery."""
    global _continuous_discovery_thread

    ml = _try_import_ml()
    if ml is None:
        _api_logger.warning("ml_scraper not available — skipping ML startup")
        return

    # Retroactively inject any ML-discovered brands missing from the BRANDS list
    try:
        injected = ml.inject_missing_brands()
        if injected:
            _api_logger.info(f"Injected {injected} ML-discovered brands on startup")
    except Exception as exc:
        _api_logger.warning(f"inject_missing_brands failed: {exc}")

    # Start continuous background discovery
    _continuous_discovery_thread = threading.Thread(
        target=ml.continuous_discovery,
        kwargs={
            "cycle_interval_s": 300.0, 
            "country_delay_s": 60.0, 
            "max_new_per_run": 6,
            "on_items_discovered": lambda items: store_products(get_conn(), items)
        },
        daemon=True,
        name="ml-continuous-discovery",
    )
    _continuous_discovery_thread.start()
    _api_logger.info("Continuous ML discovery thread started")


@app.on_event("shutdown")
def on_shutdown():
    """Gracefully stop the continuous discovery loop."""
    ml = _try_import_ml()
    if ml:
        ml.stop_continuous_discovery()
        _api_logger.info("Continuous ML discovery stopped")

# ── DB helpers ────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand TEXT,
            title TEXT,
            price REAL,
            currency TEXT,
            url TEXT UNIQUE,
            image_url TEXT,
            sizes TEXT,
            colors TEXT,
            category TEXT,
            available INTEGER,
            country TEXT,
            gender TEXT,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
            title, brand, colors, category, gender, content='products', content_rowid='id'
        );
        CREATE TRIGGER IF NOT EXISTS products_ai AFTER INSERT ON products BEGIN
            INSERT INTO products_fts(rowid, title, brand, colors, category, gender)
            VALUES (new.id, new.title, new.brand, new.colors, new.category, new.gender);
        END;
        CREATE TRIGGER IF NOT EXISTS products_ad AFTER DELETE ON products BEGIN
            INSERT INTO products_fts(products_fts, rowid, title, brand, colors, category, gender)
            VALUES('delete', old.id, old.title, old.brand, old.colors, old.category, old.gender);
        END;
        CREATE TRIGGER IF NOT EXISTS products_au AFTER UPDATE ON products BEGIN
            INSERT INTO products_fts(products_fts, rowid, title, brand, colors, category, gender)
            VALUES('delete', old.id, old.title, old.brand, old.colors, old.category, old.gender);
            INSERT INTO products_fts(rowid, title, brand, colors, category, gender)
            VALUES (new.id, new.title, new.brand, new.colors, new.category, new.gender);
        END;
        CREATE TABLE IF NOT EXISTS cleaned_listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand TEXT,
            title TEXT,
            price REAL,
            currency TEXT,
            url TEXT UNIQUE,
            image_url TEXT,
            sizes TEXT,
            colors TEXT,
            category TEXT,
            available INTEGER,
            country TEXT,
            gender TEXT,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS cleaned_listings_fts USING fts5(
            title, brand, colors, category, gender, content='cleaned_listings', content_rowid='id'
        );
        CREATE TRIGGER IF NOT EXISTS cleaned_listings_ai AFTER INSERT ON cleaned_listings BEGIN
            INSERT INTO cleaned_listings_fts(rowid, title, brand, colors, category, gender)
            VALUES (new.id, new.title, new.brand, new.colors, new.category, new.gender);
        END;
        CREATE TRIGGER IF NOT EXISTS cleaned_listings_ad AFTER DELETE ON cleaned_listings BEGIN
            INSERT INTO cleaned_listings_fts(cleaned_listings_fts, rowid, title, brand, colors, category, gender)
            VALUES('delete', old.id, old.title, old.brand, old.colors, old.category, old.gender);
        END;
        CREATE TRIGGER IF NOT EXISTS cleaned_listings_au AFTER UPDATE ON cleaned_listings BEGIN
            INSERT INTO cleaned_listings_fts(cleaned_listings_fts, rowid, title, brand, colors, category, gender)
            VALUES('delete', old.id, old.title, old.brand, old.colors, old.category, old.gender);
            INSERT INTO cleaned_listings_fts(rowid, title, brand, colors, category, gender)
            VALUES (new.id, new.title, new.brand, new.colors, new.category, new.gender);
        END;
    """)


def store_products(conn: sqlite3.Connection,
                   listings: list) -> None:
    if _producer:
        for item in listings:
            data = {
                "brand": item.brand,
                "title": item.title,
                "price": item.price,
                "currency": item.currency,
                "url": item.url,
                "image_url": item.image_url,
                "sizes": item.sizes,
                "colors": item.colors,
                "category": item.category,
                "available": item.available,
                "country": item.country,
                "gender": item.gender,
                "description": getattr(item, 'description', 'No description available')
            }
            try:
                _producer.send('raw_listings', value=data)
            except Exception as e:
                _api_logger.error(f"Kafka send error: {e}")
        try:
            _producer.flush(timeout=2)
        except Exception:
            pass
        return

    # Kafka unavailable — write directly to cleaned_listings so /api/stream
    # can surface live scrape results without the Kafka → Spark pipeline.
    # Mirrors the Spark normalizations: drop empty url/price<=0, force gender
    # into {male, female, unisex}.
    if not listings:
        return
    init_db(conn)
    rows = []
    for item in listings:
        if not getattr(item, "url", None) or float(getattr(item, "price", 0) or 0) <= 0:
            continue
        rows.append((
            item.brand, item.title, float(item.price), item.currency,
            item.url, item.image_url or "",
            json.dumps(list(item.sizes or [])),
            json.dumps(list(item.colors or [])),
            item.category or "other",
            1 if item.available else 0,
            item.country, clothing_scraper.normalize_gender(item.gender),
        ))
    if not rows:
        return
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO cleaned_listings "
            "(brand,title,price,currency,url,image_url,sizes,colors,category,available,country,gender) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    except Exception as e:
        _api_logger.error(f"Direct SQLite write error: {e}")


# ── Pydantic models ───────────────────────────────────────────────────────────

class ProductOut(BaseModel):
    id: int
    brand: str
    title: str
    price: float
    currency: str
    url: str
    image_url: str
    sizes: List[str]
    colors: List[str]
    category: str
    available: bool
    country: str
    gender: str


class SearchResponse(BaseModel):
    query: str
    country: str
    total: int
    brands_found: List[str]
    results: List[ProductOut]


def _safe_parse_list(val: str) -> List[str]:
    if not val:
        return []
    try:
        parsed = json.loads(val)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
        return [str(parsed)]
    except json.JSONDecodeError:
        return [x.strip() for x in str(val).split(",") if x.strip()]

def row_to_product(row: sqlite3.Row) -> ProductOut:
    return ProductOut(
        id=row["id"],
        brand=row["brand"],
        title=row["title"],
        price=row["price"],
        currency=row["currency"],
        url=row["url"],
        image_url=row["image_url"] or "",
        sizes=_safe_parse_list(row["sizes"]),
        colors=_safe_parse_list(row["colors"]),
        category=row["category"] or "other",
        available=bool(row["available"]),
        country=row["country"],
        gender=row["gender"] or "unisex",
    )


# ── Background ML discovery ───────────────────────────────────────────────────
# Keeps a set of (country, query) pairs currently being discovered so we don't
# launch duplicate threads.

_ml_running: set[tuple[str, str]] = set()
_ml_lock = threading.Lock()


def _run_ml_background(country: str, query: str) -> None:
    key = (country, query)
    ml = _try_import_ml()
    if ml is None:
        return
    try:
        items = ml.scrape_and_learn(country, query, max_new=8, verbose=False)
        if items:
            store_products(get_conn(), items)
    except Exception:
        pass
    finally:
        with _ml_lock:
            _ml_running.discard(key)


def launch_ml_background(country: str, query: str) -> None:
    """Fire-and-forget ML discovery in a daemon thread."""
    key = (country, query)
    with _ml_lock:
        if key in _ml_running:
            return
        _ml_running.add(key)
    t = threading.Thread(target=_run_ml_background, args=(country, query),
                         daemon=True)
    t.start()


# ── SSE streaming scraper ─────────────────────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _query_cached_products(conn: sqlite3.Connection, country: str, query: str,
                           gender: str = "all", limit: int = 3000) -> list[sqlite3.Row]:
    """Pull matching products from the local DB (fast, no network)."""
    fts_query = " ".join(f'"{tok}"' for tok in query.replace("-", " ").split() if tok)
    if not fts_query:
        return []
        
    if gender != "all":
        fts_query = f"{fts_query} gender:{gender}"
        
    sql = """
        SELECT p.* FROM cleaned_listings_fts f
        JOIN cleaned_listings p ON p.id = f.rowid
        WHERE cleaned_listings_fts MATCH ?
          AND p.country = ? COLLATE NOCASE
        ORDER BY bm25(cleaned_listings_fts), p.price
        LIMIT ?
    """
    try:
        return conn.execute(sql, [fts_query, country, limit]).fetchall()
    except sqlite3.OperationalError:
        return []


def _stream_scrape(country: str, query: str, gender: str = "all") -> Generator[str, None, None]:
    """
    Generator that:
      1. immediately yields any cached results we already have in the DB
         (so the user sees products instantly, even if every live scrape fails),
      2. then runs live scrapes against each brand in parallel under a hard
         global deadline (LIVE_SCRAPE_BUDGET_S). When the deadline elapses,
         we stop waiting on stragglers and emit `done` so the EventSource
         on the frontend can close cleanly. Brands that block / error /
         time out are silently skipped.
    """
    import concurrent.futures

    LIVE_SCRAPE_BUDGET_S = 20.0   # total wall-clock budget for ALL live scrapes
    PER_BRAND_RESULT_TIMEOUT_S = 8.0  # how long to wait on any single future

    targets = clothing_scraper.brands_for_country(country)
    if not targets:
        yield _sse("error", {"message": f"No brands configured for {country}"})
        return

    yield _sse("status", {
        "message": f"Scanning {len(targets)} brands in {country}…",
        "total_brands": len(targets),
    })

    conn = get_conn()
    init_db(conn)
    seen_urls: set[str] = set()
    products_sent = 0

    # ── Step 1: emit cached DB results first ──────────────────────────────────
    cached_rows = _query_cached_products(conn, country, query, gender, limit=3000)
    if cached_rows:
        # Group by brand so the UI sees the same per-brand structure as live results
        from collections import defaultdict
        by_brand: dict[str, list] = defaultdict(list)
        for r in cached_rows:
            by_brand[r["brand"]].append(r)

        for brand, rows in by_brand.items():
            items = [row_to_product(r).model_dump() for r in rows]
            for it in items:
                seen_urls.add(it["url"])
            products_sent += len(items)
            yield _sse("products", {
                "brand": brand,
                "items": items,
                "brands_done": 0,             # live progress hasn't started
                "total_brands": len(targets),
                "cached": True,               # frontend can use this if it wants
            })

    # ── Step 2: live scrape to refresh / find new items ───────────────────────
    # Hard-limited: we will stop waiting after LIVE_SCRAPE_BUDGET_S no matter what.

    def scrape_and_store(entry):
        name, base_url, _, _, currency, platform = entry
        session = clothing_scraper.make_session()
        t0 = time.time()
        try:
            if platform == "shopify":
                try:
                    items = clothing_scraper.scrape_shopify(
                        session, name, base_url, currency, country,
                        max_pages=3, per_page=250)
                except clothing_scraper.ScraperBlocked:
                    items = clothing_scraper.scrape_html(
                        session, name, base_url, currency, country)
            else:
                items = clothing_scraper.scrape_html(
                    session, name, base_url, currency, country)
            return name, items, None, time.time() - t0
        except Exception as e:
            return name, None, str(e), time.time() - t0

    brands_done = 0
    deadline = time.time() + LIVE_SCRAPE_BUDGET_S
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
    try:
        futs = {pool.submit(scrape_and_store, e): e[0] for e in targets}

        while futs:
            remaining = deadline - time.time()
            if remaining <= 0:
                # Out of time. Cancel/abandon remaining work and emit `done`.
                for f in futs:
                    f.cancel()
                yield _sse("brand_done", {
                    "brand": None,
                    "success": False,
                    "skipped_due_to_deadline": len(futs),
                    "brands_done": brands_done,
                    "total_brands": len(targets),
                })
                break

            try:
                done, _ = concurrent.futures.wait(
                    futs.keys(),
                    timeout=min(remaining, 1.0),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
            except Exception:
                break

            for fut in done:
                futs.pop(fut, None)
                try:
                    name, items, err, dt = fut.result(timeout=PER_BRAND_RESULT_TIMEOUT_S)
                except Exception as e:
                    brands_done += 1
                    yield _sse("brand_done", {
                        "brand": "?", "success": False,
                        "error": f"{type(e).__name__}",
                        "brands_done": brands_done, "total_brands": len(targets),
                    })
                    continue

                brands_done += 1

                if err or not items:
                    yield _sse("brand_done", {
                        "brand": name, "success": False,
                        "brands_done": brands_done, "total_brands": len(targets),
                    })
                    continue

                # Store fresh results to DB via Kafka Spark pipeline
                matched = [i for i in items if clothing_scraper.matches_query(i, query)]
                if matched:
                    store_products(conn, matched)

                    # Only emit URLs we haven't already sent in the cached batch
                    new_items = [i for i in matched if i.url not in seen_urls]
                    if new_items:
                        target_urls = {i.url for i in new_items}
                        found_items = []
                        poll_timeout = 10.0 # Wait up to 10s for Spark to process
                        poll_start = time.time()
                        
                        while time.time() - poll_start < poll_timeout:
                            placeholders = ",".join(["?"] * len(target_urls))
                            sql = f"SELECT * FROM cleaned_listings WHERE url IN ({placeholders})"
                            rows = conn.execute(sql, list(target_urls)).fetchall()
                            
                            if len(rows) > 0:
                                found_items = [row_to_product(r) for r in rows]
                                break
                            time.sleep(0.5)

                        for r in found_items:
                            seen_urls.add(r.url)

                        if found_items:
                            products_sent += len(found_items)
                            yield _sse("products", {
                                "brand": name,
                                "items": [i.model_dump() for i in found_items],
                                "brands_done": brands_done,
                                "total_brands": len(targets),
                                "cached": False,
                            })
                            continue

                yield _sse("brand_done", {
                    "brand": name, "success": True, "count": len(items),
                    "brands_done": brands_done, "total_brands": len(targets),
                })
    finally:
        # Don't wait for stragglers — daemon-style shutdown so the request returns.
        pool.shutdown(wait=False, cancel_futures=True)

    conn.close()
    yield _sse("done", {
        "total_products": products_sent,
        "brands_scanned": brands_done,
    })


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"service": "clothing-aggregator", "status": "ok"}


@app.get("/api/countries")
def list_countries():
    """Return all supported countries."""
    return {"countries": clothing_scraper.SUPPORTED_COUNTRIES}


@app.get("/api/brands")
def list_brands(country: Optional[str] = None):
    if country:
        brands = [b[0] for b in clothing_scraper.brands_for_country(country)]
    else:
        brands = [b[0] for b in clothing_scraper.BRANDS]
    return {"brands": sorted(brands)}


@app.get("/api/stream")
def stream_search(
    q: str = Query(..., min_length=1),
    country: str = Query("US"),
    gender: str = Query("all"),
):
    """
    SSE endpoint. Streams product batches as each brand is scraped.
    The frontend consumes this via EventSource.

    Events emitted:
      status   – initial info (total_brands)
      products – batch of matching products from one brand (items: [...])
      brand_done – a brand finished with no matches
      done     – all brands done
      error    – fatal error
    """
    country = country.upper()
    if country not in clothing_scraper.SUPPORTED_COUNTRIES:
        raise HTTPException(status_code=400, detail=f"Country {country} not supported")

    # Launch ML background discovery (non-blocking)
    launch_ml_background(country, q)

    return StreamingResponse(
        _stream_scrape(country, q, gender),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/search", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=1),
    country: str = Query("US"),
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    Classic (non-streaming) search — queries the DB for cached results.
    Used as a fallback when SSE isn't available.
    """
    country = country.upper()
    if country not in clothing_scraper.SUPPORTED_COUNTRIES:
        raise HTTPException(status_code=400, detail=f"Country {country} not supported")

    conn = get_conn()
    init_db(conn)

    fts_query = " ".join(f'"{tok}"' for tok in q.replace("-", " ").split() if tok)
    if not fts_query:
        raise HTTPException(status_code=400, detail="Empty query after sanitization")

    sql = """
        SELECT p.* FROM cleaned_listings_fts f
        JOIN cleaned_listings p ON p.id = f.rowid
        WHERE cleaned_listings_fts MATCH ?
          AND p.country = ? COLLATE NOCASE
          AND p.available = 1
    """
    params: list = [fts_query, country]
    if min_price is not None:
        sql += " AND p.price >= ?"
        params.append(min_price)
    if max_price is not None:
        sql += " AND p.price <= ?"
        params.append(max_price)

    sql += " ORDER BY bm25(cleaned_listings_fts), p.price LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        raise HTTPException(status_code=400, detail=f"Search error: {e}")

    products = [row_to_product(r) for r in rows]
    brands_found = sorted({p.brand for p in products})
    conn.close()

    return SearchResponse(
        query=q, country=country,
        total=len(products), brands_found=brands_found,
        results=products,
    )


@app.get("/api/stats")
def stats():
    conn = get_conn()
    init_db(conn)
    total = conn.execute("SELECT COUNT(*) AS n FROM cleaned_listings").fetchone()["n"]
    available = conn.execute(
        "SELECT COUNT(*) AS n FROM cleaned_listings WHERE available = 1"
    ).fetchone()["n"]
    by_brand = conn.execute(
        "SELECT brand, COUNT(*) AS n FROM cleaned_listings GROUP BY brand ORDER BY n DESC"
    ).fetchall()
    conn.close()
    return {
        "total_products": total,
        "available": available,
        "brands": [{"name": r["brand"], "count": r["n"]} for r in by_brand],
    }

class RecommendationRequest(BaseModel):
    history: List[str]

@app.post("/api/recommendations", response_model=List[ProductOut])
def recommendations(req: RecommendationRequest):
    ml = _try_import_recommendation_ml()
    if ml is None:
        raise HTTPException(status_code=503, detail="ML Recommendation engine not available")
    
    try:
        raw_rows = ml.get_recommendations(req.history)
        products = []
        for r in raw_rows:
            products.append(ProductOut(
                id=r["id"],
                brand=r["brand"],
                title=r["title"],
                price=r["price"],
                currency=r["currency"],
                url=r["url"],
                image_url=r["image_url"] or "",
                sizes=_safe_parse_list(r["sizes"]),
                colors=_safe_parse_list(r["colors"]),
                category=r["category"] or "other",
                available=bool(r["available"]),
                country=r["country"],
                gender=r["gender"] or "unisex",
            ))
        return products
    except Exception as e:
        _api_logger.error(f"Recommendation error: {e}")
        return []


@app.get("/api/ml-status")
def ml_status():
    """Return ML discovery statistics — tracked domains, model data, active discovery."""
    ml = _try_import_ml()
    if ml is None:
        return {"available": False, "message": "ml_scraper not installed"}

    import os
    result = {"available": True}

    db_path = ml.DB_PATH
    if os.path.exists(db_path):
        conn = ml.get_conn()
        total = conn.execute("SELECT COUNT(*) FROM site_tracking").fetchone()[0]
        success = conn.execute(
            "SELECT COUNT(*) FROM site_tracking WHERE success=1"
        ).fetchone()[0]
        blocked = conn.execute(
            "SELECT COUNT(*) FROM site_tracking WHERE success=0"
        ).fetchone()[0]
        searches = conn.execute("SELECT COUNT(*) FROM search_log").fetchone()[0]
        conn.close()
        result["tracking"] = {
            "total_domains": total,
            "successful": success,
            "blocked": blocked,
            "search_runs": searches,
        }
    else:
        result["tracking"] = {"total_domains": 0, "successful": 0, "blocked": 0, "search_runs": 0}

    result["continuous_discovery_active"] = (
        _continuous_discovery_thread is not None
        and _continuous_discovery_thread.is_alive()
    )
    result["total_brands_in_memory"] = len(clothing_scraper.BRANDS)

    return result