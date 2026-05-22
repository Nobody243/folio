import React, { useState, useEffect, useMemo } from "react";

// ----------- API LAYER -----------
// In dev, point this to the FastAPI server. Demo build uses an inline mock that
// mirrors the API contract so the UI is fully testable without a server.
const API_BASE = ""; // e.g. "http://localhost:8000"

// Inline demo dataset mirroring the backend's response shape
const DEMO_DATA = generateDemoData();

const api = {
  async countries() {
    if (!API_BASE) return { countries: ["US", "CA", "GB", "DE", "KR"] };
    return (await fetch(`${API_BASE}/api/countries`)).json();
  },
  async search({ q, country, minPrice, maxPrice }) {
    if (!API_BASE) return mockSearch({ q, country, minPrice, maxPrice });
    const params = new URLSearchParams({ q, country });
    if (minPrice) params.set("min_price", minPrice);
    if (maxPrice) params.set("max_price", maxPrice);
    return (await fetch(`${API_BASE}/api/search?${params}`)).json();
  },
};

// ----------- MAIN APP -----------
export default function App() {
  const [country, setCountry] = useState("US");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [countries, setCountries] = useState(["US"]);
  const [activeFilter, setActiveFilter] = useState("all");
  const [priceSort, setPriceSort] = useState("relevance");

  useEffect(() => {
    api.countries().then((d) => setCountries(d.countries || ["US"]));
  }, []);

  const handleSearch = async (e) => {
    if (e) e.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    try {
      const data = await api.search({ q: query, country });
      setResults(data);
      setActiveFilter("all");
    } catch (err) {
      console.error(err);
      setResults({ total: 0, results: [], brands_found: [], error: String(err) });
    } finally {
      setLoading(false);
    }
  };

  const filtered = useMemo(() => {
    if (!results?.results) return [];
    let arr = activeFilter === "all"
      ? results.results
      : results.results.filter((p) => p.brand === activeFilter);
    if (priceSort === "low") arr = [...arr].sort((a, b) => a.price - b.price);
    if (priceSort === "high") arr = [...arr].sort((a, b) => b.price - a.price);
    return arr;
  }, [results, activeFilter, priceSort]);

  return (
    <div className="app">
      <Header />
      <Hero
        country={country}
        countries={countries}
        setCountry={setCountry}
        query={query}
        setQuery={setQuery}
        onSearch={handleSearch}
        loading={loading}
      />
      {results && (
        <ResultsSection
          results={results}
          filtered={filtered}
          activeFilter={activeFilter}
          setActiveFilter={setActiveFilter}
          priceSort={priceSort}
          setPriceSort={setPriceSort}
          query={query}
          country={country}
        />
      )}
      {!results && <FeaturedSection setQuery={setQuery} />}
      <Footer />
      <GlobalStyles />
    </div>
  );
}

// ----------- COMPONENTS -----------

function Header() {
  return (
    <header className="header">
      <div className="header-inner">
        <div className="logo-mark">
          <span className="logo-num">№ 001</span>
          <h1 className="logo-name">FOLIO<span className="logo-dot">.</span></h1>
          <span className="logo-sub">/ atlas of garments</span>
        </div>
        <nav className="nav">
          <span className="nav-label">est. mmxxv</span>
          <span className="nav-divider">—</span>
          <span className="nav-label">an aggregator</span>
        </nav>
      </div>
    </header>
  );
}

function Hero({ country, countries, setCountry, query, setQuery, onSearch, loading }) {
  return (
    <section className="hero">
      <div className="hero-grid">
        <div className="hero-text">
          <p className="eyebrow">issue / 001 — search</p>
          <h2 className="display">
            Every <em>thread</em><br />
            from every <em>house</em>,<br />
            in one <em>folio</em>.
          </h2>
          <p className="lede">
            Type what you're after. Pick a country. We comb the catalogues
            of brands worldwide and lay the listings on a single page —
            with sizes, colourways, prices, and a direct line to each store.
          </p>
        </div>

        <form className="search-card" onSubmit={onSearch}>
          <div className="search-field">
            <label className="field-label">looking for</label>
            <input
              className="search-input"
              type="text"
              placeholder="black t-shirt, linen trousers, denim jacket…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoFocus
            />
          </div>

          <div className="search-field">
            <label className="field-label">shipping to</label>
            <select
              className="country-select"
              value={country}
              onChange={(e) => setCountry(e.target.value)}
            >
              {countries.map((c) => (
                <option key={c} value={c}>{COUNTRY_NAMES[c] || c}</option>
              ))}
            </select>
          </div>

          <button className="search-btn" type="submit" disabled={loading || !query.trim()}>
            {loading ? (
              <span className="btn-loading">
                <span className="spin" />
                <span>combing the racks</span>
              </span>
            ) : (
              <>
                <span>find</span>
                <span className="arrow">→</span>
              </>
            )}
          </button>
        </form>
      </div>

      <div className="ticker">
        <div className="ticker-track">
          {Array(3).fill(0).map((_, i) => (
            <span key={i} className="ticker-content">
              <span>○ NORTHWIND APPAREL</span>
              <span>○ ATLAS &amp; OAK</span>
              <span>○ STUDIO MAREN</span>
              <span>○ COASTLINE CO</span>
              <span>○ FOLK &amp; FIELD</span>
              <span>○ MERIDIAN GOODS</span>
              <span>○ HALCYON WEAR</span>
              <span>○ RIDGE SUPPLY</span>
              <span>○ + 200 MORE</span>
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}

function ResultsSection({
  results, filtered, activeFilter, setActiveFilter,
  priceSort, setPriceSort, query, country,
}) {
  return (
    <section className="results">
      <div className="results-head">
        <div>
          <p className="eyebrow">your search</p>
          <h3 className="results-title">
            <em>"{query}"</em> in {COUNTRY_NAMES[country] || country}
          </h3>
          <p className="results-meta">
            {results.total} listing{results.total !== 1 ? "s" : ""} across {results.brands_found?.length || 0} brand{results.brands_found?.length !== 1 ? "s" : ""}
          </p>
        </div>

        <div className="sort-controls">
          <span className="field-label">sort</span>
          <button
            className={`sort-btn ${priceSort === "relevance" ? "active" : ""}`}
            onClick={() => setPriceSort("relevance")}
          >best match</button>
          <button
            className={`sort-btn ${priceSort === "low" ? "active" : ""}`}
            onClick={() => setPriceSort("low")}
          >price ↑</button>
          <button
            className={`sort-btn ${priceSort === "high" ? "active" : ""}`}
            onClick={() => setPriceSort("high")}
          >price ↓</button>
        </div>
      </div>

      {results.brands_found?.length > 0 && (
        <div className="brand-filters">
          <button
            className={`brand-pill ${activeFilter === "all" ? "active" : ""}`}
            onClick={() => setActiveFilter("all")}
          >
            all brands
            <span className="pill-count">{results.total}</span>
          </button>
          {results.brands_found.map((b) => {
            const count = results.results.filter((p) => p.brand === b).length;
            return (
              <button
                key={b}
                className={`brand-pill ${activeFilter === b ? "active" : ""}`}
                onClick={() => setActiveFilter(b)}
              >
                {b}
                <span className="pill-count">{count}</span>
              </button>
            );
          })}
        </div>
      )}

      {filtered.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="grid">
          {filtered.map((p, i) => (
            <ProductCard key={p.id || i} product={p} index={i} />
          ))}
        </div>
      )}
    </section>
  );
}

function ProductCard({ product, index }) {
  return (
    <article className="card" style={{ animationDelay: `${(index % 12) * 40}ms` }}>
      <div className="card-image">
        <span className="card-num">{String(index + 1).padStart(3, "0")}</span>
        <img src={product.image_url} alt={product.title} loading="lazy" />
      </div>
      <div className="card-body">
        <p className="card-brand">{product.brand}</p>
        <h4 className="card-title">{product.title}</h4>
        <div className="card-meta">
          <div className="meta-row">
            <span className="meta-label">sizes</span>
            <span className="meta-val">{product.sizes.slice(0, 6).join(" · ")}</span>
          </div>
          <div className="meta-row">
            <span className="meta-label">colours</span>
            <span className="meta-val">{product.colors.join(" · ")}</span>
          </div>
        </div>
        <div className="card-foot">
          <span className="price">
            <span className="currency">{product.currency === "USD" ? "$" : product.currency}</span>
            {product.price.toFixed(2)}
          </span>
          <a className="visit-btn" href={product.url} target="_blank" rel="noreferrer">
            visit <span>→</span>
          </a>
        </div>
      </div>
    </article>
  );
}

function EmptyState() {
  return (
    <div className="empty">
      <p className="empty-mark">∅</p>
      <p className="empty-title">No listings found.</p>
      <p className="empty-sub">
        Try a broader query, or change the country. The brands we couldn't reach
        on this run aren't shown.
      </p>
    </div>
  );
}

function FeaturedSection({ setQuery }) {
  const suggestions = [
    "black t-shirt", "linen shirt", "denim jacket",
    "wide leg jeans", "wool sweater", "cotton hoodie",
  ];
  return (
    <section className="featured">
      <p className="eyebrow centered">try a search</p>
      <h3 className="feat-title">A few <em>starting points</em></h3>
      <div className="suggestions">
        {suggestions.map((s) => (
          <button
            key={s}
            className="suggestion"
            onClick={() => setQuery(s)}
          >
            <span className="suggestion-arrow">→</span>
            {s}
          </button>
        ))}
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="footer">
      <div className="footer-inner">
        <p className="footer-name">FOLIO<span className="logo-dot">.</span></p>
        <p className="footer-meta">
          a clothing aggregator · fed by public catalogue endpoints · no personal data collected
        </p>
        <p className="footer-credit">© mmxxv · all listings link to their respective merchants</p>
      </div>
    </footer>
  );
}

// ----------- HELPERS -----------
const COUNTRY_NAMES = {
  US: "United States", CA: "Canada", GB: "United Kingdom",
  DE: "Germany", FR: "France", NL: "Netherlands", BE: "Belgium",
  IT: "Italy", ES: "Spain", AU: "Australia", JP: "Japan",
  KR: "South Korea", SG: "Singapore", IE: "Ireland", MX: "Mexico",
  BR: "Brazil", IN: "India", PK: "Pakistan", AE: "UAE",
};

function generateDemoData() {
  const brands = [
    { name: "Northwind Apparel", countries: ["US", "CA", "GB"], priceMin: 25, priceMax: 180 },
    { name: "Atlas & Oak", countries: ["US", "GB", "DE"], priceMin: 40, priceMax: 250 },
    { name: "Studio Maren", countries: ["DE", "FR"], priceMin: 30, priceMax: 200 },
    { name: "Coastline Co", countries: ["US"], priceMin: 20, priceMax: 150 },
    { name: "Folk & Field", countries: ["GB", "DE"], priceMin: 35, priceMax: 220 },
    { name: "Meridian Goods", countries: ["US", "CA"], priceMin: 15, priceMax: 120 },
    { name: "Halcyon Wear", countries: ["KR"], priceMin: 45, priceMax: 280 },
    { name: "Ridge Supply", countries: ["US"], priceMin: 20, priceMax: 140 },
  ];

  const items = {
    "t-shirt": ["Classic Crew Tee", "Oversized Boxy Tee", "Premium Cotton Tee", "Logo Print Tee", "Vintage Pocket Tee"],
    hoodie: ["Heavyweight Hoodie", "Pullover Hoodie", "Zip-Up Hoodie", "Cropped Hoodie"],
    jeans: ["Straight Leg Jeans", "Slim Fit Jeans", "Wide Leg Jeans", "High-Rise Mom Jeans"],
    jacket: ["Bomber Jacket", "Denim Jacket", "Field Jacket", "Trucker Jacket"],
    shorts: ["Cargo Shorts", "Athletic Shorts", "Linen Shorts"],
    sweater: ["Cable Knit Sweater", "Crewneck Sweater", "Mock Neck Sweater"],
    shirt: ["Oxford Button-Down", "Linen Camp Shirt", "Flannel Shirt"],
    dress: ["Midi Wrap Dress", "Slip Dress", "A-Line Dress"],
  };

  const colors = ["Black", "White", "Navy", "Charcoal", "Olive", "Burgundy", "Cream", "Heather Grey", "Forest Green", "Sand", "Rust"];
  const colorHex = {
    Black: "1a1a1a", White: "f5f5f5", Navy: "1e3a5f", Charcoal: "36454f",
    Olive: "808000", Burgundy: "800020", Cream: "f5f0e1", "Heather Grey": "9e9e9e",
    "Forest Green": "228b22", Sand: "c2b280", Rust: "b7410e",
  };
  const sizes = ["XS", "S", "M", "L", "XL", "XXL"];

  const products = [];
  let id = 1;
  brands.forEach((b) => {
    Object.entries(items).forEach(([cat, titles]) => {
      titles.forEach((t) => {
        const n = 1 + Math.floor(seededRand(b.name + t) * 3);
        const cs = [...colors].sort(() => seededRand(b.name + t + "c") - 0.5).slice(0, n);
        cs.forEach((color) => {
          const r = seededRand(b.name + t + color);
          const price = b.priceMin + r * (b.priceMax - b.priceMin);
          products.push({
            id: id++,
            brand: b.name,
            title: `${color} ${t}`,
            price: Math.round(price * 100) / 100,
            currency: "USD",
            url: `https://example-${b.name.toLowerCase().replace(/[^a-z]+/g, "-")}.com/products/${(color + "-" + t).toLowerCase().replace(/\s+/g, "-")}`,
            image_url: `https://placehold.co/600x800/${colorHex[color] || "888"}/fff?text=${encodeURIComponent(color + " " + cat)}`,
            sizes: sizes,
            colors: [color],
            category: cat,
            available: r > 0.1,
            country: b.countries[Math.floor(seededRand(b.name) * b.countries.length)],
          });
        });
      });
    });
  });
  return products;
}

function seededRand(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h * 16777619) >>> 0;
  }
  return (h % 10000) / 10000;
}

function mockSearch({ q, country, minPrice, maxPrice }) {
  const tokens = q.toLowerCase().replace(/-/g, " ").split(/\s+/).filter(Boolean);
  let results = DEMO_DATA.filter((p) => {
    if (p.country !== country) return false;
    if (!p.available) return false;
    if (minPrice && p.price < minPrice) return false;
    if (maxPrice && p.price > maxPrice) return false;
    const hay = (p.title + " " + p.brand + " " + p.colors.join(" ") + " " + p.category).toLowerCase();
    return tokens.every((t) => hay.includes(t));
  });
  return Promise.resolve({
    query: q,
    country,
    total: results.length,
    brands_found: [...new Set(results.map((r) => r.brand))].sort(),
    results: results.slice(0, 60),
  });
}

// ----------- STYLES -----------
function GlobalStyles() {
  return (
    <style>{`
      @import url('https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300;0,9..144,400;0,9..144,500;0,9..144,600;0,9..144,800;1,9..144,300;1,9..144,500;1,9..144,800&family=JetBrains+Mono:wght@400;500&display=swap');

      :root {
        --paper: #f4ede0;
        --paper-2: #ebe2d2;
        --ink: #1a1410;
        --ink-soft: #4a3f35;
        --rule: #1a1410;
        --accent: #c8462e;
        --accent-soft: #e8a89a;
        --success: #4a6a4f;
      }

      * { box-sizing: border-box; margin: 0; padding: 0; }

      body, .app {
        background: var(--paper);
        color: var(--ink);
        font-family: 'Fraunces', Georgia, serif;
        font-feature-settings: "ss01", "ss02";
        min-height: 100vh;
        background-image:
          radial-gradient(circle at 20% 0%, rgba(200, 70, 46, 0.04) 0%, transparent 40%),
          radial-gradient(circle at 80% 100%, rgba(74, 106, 79, 0.05) 0%, transparent 40%);
      }

      .app::before {
        content: '';
        position: fixed; inset: 0;
        background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Cfilter id='n'%3E%3CfeTurbulence baseFrequency='0.9' /%3E%3CfeColorMatrix values='0 0 0 0 0.1, 0 0 0 0 0.08, 0 0 0 0 0.06, 0 0 0 0.4 0' /%3E%3C/filter%3E%3Crect width='200' height='200' filter='url(%23n)' /%3E%3C/svg%3E");
        opacity: 0.18;
        pointer-events: none;
        z-index: 1;
        mix-blend-mode: multiply;
      }

      .app > * { position: relative; z-index: 2; }

      .eyebrow {
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.2em;
        color: var(--accent);
        margin-bottom: 16px;
      }
      .eyebrow.centered { text-align: center; }
      em { font-style: italic; font-feature-settings: "ss01"; color: var(--accent); }
      .field-label {
        font-family: 'JetBrains Mono', monospace;
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.18em;
        color: var(--ink-soft);
      }

      /* HEADER */
      .header {
        border-bottom: 1px solid var(--rule);
        padding: 20px 40px;
      }
      .header-inner {
        display: flex; justify-content: space-between; align-items: baseline;
        max-width: 1400px; margin: 0 auto;
      }
      .logo-mark { display: flex; align-items: baseline; gap: 16px; }
      .logo-num {
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px; color: var(--ink-soft);
      }
      .logo-name {
        font-size: 32px; font-weight: 800;
        letter-spacing: -0.04em; line-height: 1;
      }
      .logo-dot { color: var(--accent); }
      .logo-sub {
        font-style: italic; color: var(--ink-soft); font-size: 14px;
      }
      .nav { display: flex; gap: 12px; align-items: baseline; }
      .nav-label {
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px; text-transform: uppercase;
        letter-spacing: 0.15em; color: var(--ink-soft);
      }

      /* HERO */
      .hero {
        max-width: 1400px;
        margin: 0 auto;
        padding: 60px 40px 40px;
      }
      .hero-grid {
        display: grid;
        grid-template-columns: 1.1fr 0.9fr;
        gap: 60px;
        align-items: start;
      }
      .display {
        font-size: clamp(48px, 7vw, 92px);
        font-weight: 400;
        line-height: 0.92;
        letter-spacing: -0.03em;
        margin-bottom: 32px;
      }
      .lede {
        font-size: 18px;
        line-height: 1.55;
        max-width: 460px;
        color: var(--ink-soft);
      }

      /* SEARCH CARD */
      .search-card {
        background: var(--ink);
        color: var(--paper);
        padding: 36px;
        border-radius: 2px;
        box-shadow: 12px 12px 0 var(--accent-soft);
        display: flex; flex-direction: column; gap: 24px;
      }
      .search-card .field-label { color: rgba(244, 237, 224, 0.6); }
      .search-field { display: flex; flex-direction: column; gap: 10px; }
      .search-input, .country-select {
        background: transparent;
        border: none;
        border-bottom: 2px solid rgba(244, 237, 224, 0.3);
        color: var(--paper);
        font-family: 'Fraunces', serif;
        font-size: 24px;
        padding: 8px 0;
        outline: none;
        transition: border-color 200ms;
      }
      .search-input:focus, .country-select:focus { border-bottom-color: var(--accent); }
      .search-input::placeholder { color: rgba(244, 237, 224, 0.35); font-style: italic; }
      .country-select { cursor: pointer; }
      .country-select option { background: var(--ink); color: var(--paper); }

      .search-btn {
        background: var(--accent);
        color: var(--paper);
        border: none;
        padding: 18px 24px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 13px;
        text-transform: uppercase;
        letter-spacing: 0.2em;
        cursor: pointer;
        display: flex; align-items: center; justify-content: space-between;
        transition: transform 150ms, background 150ms;
      }
      .search-btn:hover:not(:disabled) {
        background: var(--paper);
        color: var(--ink);
        transform: translate(2px, -2px);
      }
      .search-btn:disabled { opacity: 0.5; cursor: not-allowed; }
      .arrow { font-size: 18px; }
      .btn-loading { display: flex; align-items: center; gap: 12px; width: 100%; }
      .spin {
        width: 12px; height: 12px;
        border: 2px solid var(--paper);
        border-top-color: transparent;
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
      }
      @keyframes spin { to { transform: rotate(360deg); } }

      /* TICKER */
      .ticker {
        margin-top: 60px;
        border-top: 1px solid var(--rule);
        border-bottom: 1px solid var(--rule);
        overflow: hidden;
        padding: 14px 0;
      }
      .ticker-track {
        display: flex; gap: 80px;
        white-space: nowrap;
        animation: ticker 50s linear infinite;
      }
      .ticker-content {
        display: flex; gap: 80px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.2em;
      }
      @keyframes ticker {
        from { transform: translateX(0); }
        to { transform: translateX(-33.33%); }
      }

      /* RESULTS */
      .results {
        max-width: 1400px;
        margin: 0 auto;
        padding: 80px 40px 60px;
      }
      .results-head {
        display: flex; justify-content: space-between; align-items: end;
        margin-bottom: 40px;
        gap: 40px; flex-wrap: wrap;
        border-bottom: 1px solid var(--rule);
        padding-bottom: 24px;
      }
      .results-title {
        font-size: clamp(32px, 4vw, 52px);
        font-weight: 400;
        letter-spacing: -0.02em;
        line-height: 1;
        margin-bottom: 12px;
      }
      .results-meta {
        font-family: 'JetBrains Mono', monospace;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.15em;
        color: var(--ink-soft);
      }
      .sort-controls { display: flex; gap: 8px; align-items: center; }
      .sort-btn {
        background: transparent;
        border: 1px solid var(--rule);
        padding: 8px 14px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        cursor: pointer;
        color: var(--ink);
        transition: all 150ms;
      }
      .sort-btn:hover { background: var(--ink); color: var(--paper); }
      .sort-btn.active { background: var(--accent); color: var(--paper); border-color: var(--accent); }

      .brand-filters {
        display: flex; gap: 10px; flex-wrap: wrap;
        margin-bottom: 40px;
      }
      .brand-pill {
        background: transparent;
        border: 1px solid var(--rule);
        padding: 10px 16px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        cursor: pointer;
        color: var(--ink);
        display: flex; align-items: center; gap: 10px;
        transition: all 150ms;
      }
      .brand-pill:hover { background: var(--paper-2); }
      .brand-pill.active { background: var(--ink); color: var(--paper); border-color: var(--ink); }
      .pill-count {
        background: var(--accent);
        color: var(--paper);
        padding: 2px 6px;
        font-size: 10px;
        border-radius: 1px;
      }

      /* GRID */
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
        gap: 32px;
      }

      .card {
        animation: card-in 600ms cubic-bezier(0.19, 1, 0.22, 1) backwards;
      }
      @keyframes card-in {
        from { opacity: 0; transform: translateY(20px); }
        to { opacity: 1; transform: translateY(0); }
      }
      .card-image {
        position: relative;
        aspect-ratio: 3/4;
        background: var(--paper-2);
        overflow: hidden;
        margin-bottom: 16px;
      }
      .card-image img {
        width: 100%; height: 100%;
        object-fit: cover;
        transition: transform 600ms cubic-bezier(0.19, 1, 0.22, 1);
      }
      .card:hover .card-image img { transform: scale(1.05); }
      .card-num {
        position: absolute;
        top: 12px; left: 12px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 10px;
        background: var(--paper);
        padding: 4px 8px;
        letter-spacing: 0.15em;
        z-index: 2;
      }
      .card-brand {
        font-family: 'JetBrains Mono', monospace;
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.2em;
        color: var(--accent);
        margin-bottom: 6px;
      }
      .card-title {
        font-size: 18px;
        font-weight: 500;
        line-height: 1.2;
        margin-bottom: 16px;
        letter-spacing: -0.01em;
      }
      .card-meta {
        display: flex; flex-direction: column; gap: 6px;
        margin-bottom: 16px;
        padding-bottom: 16px;
        border-bottom: 1px solid rgba(26, 20, 16, 0.15);
      }
      .meta-row { display: flex; justify-content: space-between; gap: 12px; font-size: 12px; }
      .meta-label {
        font-family: 'JetBrains Mono', monospace;
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--ink-soft);
      }
      .meta-val {
        font-size: 12px;
        text-align: right;
        color: var(--ink);
      }
      .card-foot {
        display: flex; justify-content: space-between; align-items: end;
      }
      .price {
        font-size: 24px;
        font-weight: 500;
        letter-spacing: -0.02em;
      }
      .currency { font-size: 14px; color: var(--ink-soft); margin-right: 2px; }
      .visit-btn {
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.15em;
        color: var(--ink);
        text-decoration: none;
        border-bottom: 1px solid var(--ink);
        padding-bottom: 2px;
        display: flex; gap: 6px; align-items: center;
        transition: color 150ms;
      }
      .visit-btn:hover { color: var(--accent); border-bottom-color: var(--accent); }

      /* EMPTY */
      .empty {
        text-align: center; padding: 80px 20px;
        border: 1px dashed var(--rule);
      }
      .empty-mark { font-size: 80px; color: var(--ink-soft); margin-bottom: 20px; }
      .empty-title { font-size: 28px; margin-bottom: 12px; }
      .empty-sub { color: var(--ink-soft); max-width: 400px; margin: 0 auto; }

      /* FEATURED */
      .featured {
        max-width: 1400px;
        margin: 0 auto;
        padding: 60px 40px 100px;
        text-align: center;
      }
      .feat-title {
        font-size: clamp(36px, 5vw, 64px);
        font-weight: 400;
        letter-spacing: -0.02em;
        margin-bottom: 40px;
      }
      .suggestions {
        display: flex; flex-wrap: wrap; justify-content: center; gap: 12px;
        max-width: 700px; margin: 0 auto;
      }
      .suggestion {
        background: transparent;
        border: 1px solid var(--rule);
        padding: 14px 22px;
        font-family: 'Fraunces', serif;
        font-size: 18px;
        font-style: italic;
        cursor: pointer;
        color: var(--ink);
        display: flex; align-items: center; gap: 10px;
        transition: all 200ms;
      }
      .suggestion:hover {
        background: var(--ink);
        color: var(--paper);
        transform: translate(2px, -2px);
        box-shadow: -2px 2px 0 var(--accent);
      }
      .suggestion-arrow {
        font-style: normal;
        color: var(--accent);
        transition: transform 200ms;
      }
      .suggestion:hover .suggestion-arrow {
        color: var(--paper);
        transform: translateX(4px);
      }

      /* FOOTER */
      .footer {
        border-top: 1px solid var(--rule);
        padding: 40px;
      }
      .footer-inner {
        max-width: 1400px;
        margin: 0 auto;
        display: flex; justify-content: space-between; align-items: baseline;
        flex-wrap: wrap; gap: 20px;
      }
      .footer-name { font-size: 24px; font-weight: 800; letter-spacing: -0.04em; }
      .footer-meta, .footer-credit {
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.15em;
        color: var(--ink-soft);
      }

      /* RESPONSIVE */
      @media (max-width: 900px) {
        .header { padding: 16px 20px; }
        .nav { display: none; }
        .hero { padding: 40px 20px; }
        .hero-grid { grid-template-columns: 1fr; gap: 40px; }
        .results, .featured, .footer { padding-left: 20px; padding-right: 20px; }
        .results-head { flex-direction: column; align-items: start; }
        .grid { grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 20px; }
      }
    `}</style>
  );
}
