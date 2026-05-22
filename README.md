# FOLIO — Big Data Clothing Aggregator

A real-time clothing search engine that scrapes public product catalogues from
clothing brands across **10 countries**, streams the raw listings through
**Kafka**, cleans them with **Spark Structured Streaming**, and serves them
through a **FastAPI** backend to a **React** frontend with Firebase auth and
TF-IDF recommendations.

This is the term project for a Big Data course. The architecture is intentionally
pipeline-shaped (ingest → stream-process → serve) rather than the simpler
"scrape on demand" version it started as.

---

## Architecture

```
                                  ┌─────────────────────────────────────────────┐
                                  │                                             │
   ┌──────────────┐   produce     │   ┌─────────┐    consume    ┌────────────┐  │   write    ┌───────────────┐
   │  Scrapers    │ ─────────────▶│   │  Kafka  │ ─────────────▶│   Spark    │  │ ─────────▶ │   SQLite      │
   │  (40+ brands)│               │   │ (KRaft) │               │ Structured │  │            │ products.db   │
   │  Shopify +   │               │   │ topic:  │               │ Streaming  │  │            │ + FTS5 index  │
   │  JSON-LD     │               │   │ raw_    │               │  cleaning  │  │            │               │
   │  fallback    │               │   │ listings│               │  + dedup   │  │            └───────┬───────┘
   └──────┬───────┘               │   └─────────┘               └────────────┘  │                    │
          │                       │                                             │                    │
          │  fire-and-forget      └─────────────────────────────────────────────┘                    │
          │  on user search                          docker-compose                                  │ read
          │                                                                                          ▼
   ┌──────▼─────────────┐    SSE stream     ┌─────────────────┐    HTTP / SSE     ┌──────────────────────────┐
   │   ML discovery     │ ────────────────▶ │     FastAPI     │ ─────────────────▶│        React + Vite      │
   │  (RandomForest +   │                   │  /api/stream    │                   │  Firebase Auth + search  │
   │  googlesearch)     │                   │  /api/search    │                   │  + TF-IDF recommendations│
   └────────────────────┘                   │  /api/recommend │                   └──────────────────────────┘
                                            └─────────────────┘
```

### Data flow

1. **User searches** on the React frontend for a query (e.g. `"hoodie"`) and a country.
2. The frontend opens a **Server-Sent Events** connection to `/api/stream`.
3. FastAPI immediately emits any **cached** matches from `cleaned_listings` (instant first paint).
4. In parallel it kicks off **live scrapers** for every brand registered for that country.
5. Each scraped product is shipped to the **Kafka topic `raw_listings`** as JSON.
6. **Spark Structured Streaming** consumes the topic, applies cleaning rules
   (drop nulls, drop dupes, drop `price <= 0`, normalize `gender` to
   `{male, female, unisex}`, drop outliers above `10× per-brand median`),
   and writes the result into `data/products.db → cleaned_listings`.
7. FastAPI polls `cleaned_listings` for the fresh URLs and pushes them to the
   browser over the same SSE connection.
8. A background **ML discovery thread** (`ml_scraper.py`) periodically uses
   Google search + a RandomForest classifier (trained on `data/ml_tracking.db`)
   to find *new* clothing brands the BRANDS list doesn't know about yet.
9. **Recommendations** (`/api/recommendations`) score the user's search history
   against the cleaned catalogue using TF-IDF + cosine similarity.

If Kafka is unavailable, the backend falls back to writing cleaned rows
**directly** to SQLite so the app degrades gracefully on a laptop without Docker.

---

## Tech stack

| Layer            | Technology                                                |
| ---------------- | --------------------------------------------------------- |
| Ingestion        | Python scrapers (`requests`, JSON-LD parser, Shopify API) |
| Message broker   | **Apache Kafka** (KRaft mode, no Zookeeper)               |
| Stream processor | **Apache Spark 3.5.4 Structured Streaming** (PySpark)     |
| Storage          | **SQLite** with **FTS5** full-text-search index           |
| API              | **FastAPI** + Server-Sent Events + Uvicorn                |
| ML               | scikit-learn (RandomForest discovery, TF-IDF recs), pandas |
| Frontend         | **React 19** + **Vite 8** + Firebase Web SDK              |
| Auth / profile   | **Firebase Auth** + **Firestore**                         |
| Orchestration    | **Docker Compose** (Kafka + Spark containers)             |

---

## Prerequisites

| Tool             | Why                                                  | Version          |
| ---------------- | ---------------------------------------------------- | ---------------- |
| **Docker**       | Runs Kafka + Spark containers via `docker-compose`   | 20.10+           |
| **Python**       | FastAPI backend + scrapers + ML                      | 3.10+ (3.12 ok)  |
| **Node.js**      | React frontend (Vite)                                | 18+              |
| **A Firebase project** | Email/password auth + user profile in Firestore | Free tier is fine |

> ℹ️  Scrapers work best from a **residential IP**. Cloudflare blanket-blocks
> datacenter IPs (AWS/GCP/Azure), so if you run the scrapers from a cloud VM
> most brands will be silently skipped.

---

## Setup

### 1. Clone and install Python deps

```bash
git clone <your-repo-url>
cd "bd term project"

python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install fastapi "uvicorn[standard]" pydantic requests kafka-python \
            pandas scikit-learn googlesearch-python ddgs
```

### 2. Install frontend deps

```bash
npm install
```

### 3. Configure Firebase

Copy `.env.example` to `.env` and fill in your Firebase Web SDK config (from
**Firebase console → Project settings → Your apps → SDK setup and configuration**):

```bash
cp .env.example .env
# then edit .env with your real values
```

The `firestore.rules` file in the repo locks `/users/{uid}` to its own owner —
deploy it with `firebase deploy --only firestore:rules` once you have the
Firebase CLI installed.

### 4. Start Kafka + Spark

```bash
docker-compose up -d
```

This brings up two containers:
- `kafka` — Apache Kafka in KRaft mode, listening on `localhost:9094`
- `spark-stream` — runs `spark-stream/stream_job.py`, subscribes to the
  `raw_listings` topic, writes cleaned rows into `./data/products.db`
  (the `data/` folder is bind-mounted into the container)

### 5. Run the app

The easiest way on Windows is:

```bash
start.bat
```

`start.bat` starts Docker, waits for Kafka to be ready, then opens two terminals
for the FastAPI backend and the Vite dev server, and finally opens the app at
`http://localhost:5173`.

If you prefer manual:

```bash
# terminal 1
python -m uvicorn backend.api:app --reload

# terminal 2
npm run dev
```

### 6. (Optional) Bootstrap the database

The very first time, `cleaned_listings` is empty so the UI has nothing to show
until your first live scrape lands. To pre-populate it:

```bash
python bootstrap_scrape.py
# or one country:
python bootstrap_scrape.py --country US
```

This walks every brand × every supported country, ships everything through the
same Kafka → Spark → SQLite pipeline, and exits. It typically loads several
thousand products in a few minutes.

---

## Supported countries

`US, CA, GB, IE, AU, DE, NL, FR, IT, PK` — roughly 12–15 real clothing brands
per country (Shopify-based for the most part, with a JSON-LD HTML fallback for
the rest). The ML discovery loop continuously expands this list.

---

## API surface

| Endpoint                       | Method | What it does                                                                 |
| ------------------------------ | ------ | ---------------------------------------------------------------------------- |
| `GET /api/stream?q=…&country=…&gender=…` | GET | **SSE stream**: emits cached results immediately, then live-scraped results as each brand finishes |
| `GET /api/search?q=…&country=…` | GET   | Classic non-streaming search against `cleaned_listings` (FTS5)               |
| `GET /api/countries`           | GET    | Supported country codes                                                      |
| `GET /api/brands?country=…`    | GET    | Known brands, optionally filtered by country                                 |
| `POST /api/recommendations`    | POST   | Body `{history: ["query1", …]}` → TF-IDF + cosine top matches                |
| `GET /api/stats`               | GET    | Row counts, by-brand breakdown                                               |
| `GET /api/ml-status`           | GET    | ML discovery tracking stats                                                  |

---

## Project layout

```
bd term project/
├── backend/
│   ├── api.py                 # FastAPI app — SSE + REST endpoints
│   └── recommendation_ml.py   # TF-IDF + cosine recommendations
├── spark-stream/
│   ├── Dockerfile             # Spark 3.5.4 + PySpark image
│   ├── requirements.txt
│   └── stream_job.py          # Structured Streaming cleaner
├── src/                       # React + Vite frontend
│   ├── App.jsx
│   ├── main.jsx
│   ├── firebase.js            # reads VITE_FIREBASE_* env vars
│   ├── App.css / index.css
│   └── assets/
├── data/                      # ⚠ gitignored — generated SQLite DBs live here
│   ├── products.db            # main catalogue (FTS5 index)
│   └── ml_tracking.db         # RandomForest training data
├── clothing_scraper.py        # 40+ brand scrapers (Shopify + JSON-LD)
├── ml_scraper.py              # ML-based brand discovery loop
├── bootstrap_scrape.py        # one-shot full-catalogue bootstrap
├── check_ml.py                # CLI helper to inspect ML tracking DB
├── docker-compose.yml         # Kafka + Spark services
├── firebase.json              # points firebase CLI at firestore.rules
├── firestore.rules            # per-user read/write rule on /users/{uid}
├── start.bat                  # one-shot Windows launcher
├── folio.jsx                  # legacy single-file React variant (not wired in)
├── .env.example               # template — copy to .env
└── package.json / vite.config.js
```

---

## Caveats

- **Datacenter IPs get blocked.** Run from a residential connection for a real hit rate.
- **Spark on Windows.** Spark runs inside Docker via `docker-compose.yml`, so you
  don't need a local JVM/Hadoop install. The `./data` folder is bind-mounted, so
  the SQLite DB Spark writes to is the same one FastAPI reads from.
- **First search may feel slow** until `cleaned_listings` is populated — run
  `python bootstrap_scrape.py` once to seed it.
- **Robots.txt.** `/products.json` is a public Shopify endpoint used by Google
  Shopping, but always check each brand's `robots.txt` and respect rate limits.
- **Firebase config in `.env`.** The Firebase Web SDK key is technically public
  (security is enforced by `firestore.rules`), but it's kept in `.env` so the
  public repo isn't hardwired to one specific Firebase project.
