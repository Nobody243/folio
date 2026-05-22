import React, { useState, useEffect, useRef, useMemo } from "react";
import { onAuthStateChanged, signInWithEmailAndPassword, createUserWithEmailAndPassword, signOut, updateProfile } from "firebase/auth";
import { doc, getDoc, setDoc, updateDoc, arrayUnion } from "firebase/firestore";
import { auth, db } from "./firebase";

// ----------- CONSTANTS -----------
const API_BASE = "http://localhost:8000";

// All 10 supported countries — hardcoded so the dropdown always works
// even if the backend is slow to respond.
const ALL_COUNTRIES = ["US", "CA", "GB", "IE", "AU", "DE", "NL", "FR", "IT", "PK"];

const COUNTRY_NAMES = {
  US: "United States", CA: "Canada", GB: "United Kingdom",
  IE: "Ireland", AU: "Australia", DE: "Germany",
  NL: "Netherlands", FR: "France", IT: "Italy", PK: "Pakistan",
};

// ----------- MAIN APP -----------
export default function App() {
  const [country, setCountry] = useState("US");
  const [query, setQuery] = useState("");
  const [products, setProducts] = useState([]);   // flat live list, grows as SSE arrives
  const [searchMeta, setSearchMeta] = useState(null); // { query, country, totalBrands }
  const [progress, setProgress] = useState(null);     // { done, total }
  const [loading, setLoading] = useState(false);
  const [mlRunning, setMlRunning] = useState(false);
  const [activeFilter, setActiveFilter] = useState("all");
  const [genderFilter, setGenderFilter] = useState("all");
  const [priceSort, setPriceSort] = useState("relevance");
  const [currentPage, setCurrentPage] = useState(1);
  const [user, setUser] = useState(null);
  const [loadingAuth, setLoadingAuth] = useState(true);
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [recommendations, setRecommendations] = useState([]);
  const [historyError, setHistoryError] = useState(null);

  // Fetch personalized recommendations for a user based on their Firestore
  // search history. Safe to call repeatedly — it logs every step so failures
  // are visible in the browser console.
  const refreshRecommendations = async (currentUser) => {
    if (!currentUser) {
      setRecommendations([]);
      return;
    }
    let history;
    try {
      const docRef = doc(db, "users", currentUser.uid);
      const docSnap = await getDoc(docRef);
      if (!docSnap.exists()) {
        console.info("[recs] no Firestore doc for user yet — make a search first.");
        setRecommendations([]);
        return;
      }
      history = docSnap.data().searchHistory || [];
      console.info(`[recs] searchHistory length=${history.length}`, history);
    } catch (err) {
      console.error("[recs] Firestore read failed:", err.code, err.message);
      if (err.code === "permission-denied") {
        setHistoryError(
          "Firestore rejected the read (permission-denied). Deploy firestore.rules " +
          "or open Firebase Console → Firestore → Rules and allow authenticated users " +
          "to read /users/{uid}. Until then, recommendations cannot load."
        );
      }
      setRecommendations([]);
      return;
    }
    if (history.length === 0) {
      setRecommendations([]);
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/api/recommendations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ history }),
      });
      if (!res.ok) {
        console.error(`[recs] /api/recommendations returned ${res.status}`, await res.text().catch(() => ""));
        setRecommendations([]);
        return;
      }
      const data = await res.json();
      const list = Array.isArray(data) ? data : [];
      console.info(`[recs] received ${list.length} recommendations`);
      setRecommendations(list);
    } catch (err) {
      console.error("[recs] fetch failed:", err);
      setRecommendations([]);
    }
  };

  // Listen to Auth State
  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, async (currentUser) => {
      setUser(currentUser);
      try {
        await refreshRecommendations(currentUser);
      } catch (err) {
        console.error("Auth state error:", err);
      } finally {
        setLoadingAuth(false);
      }
    });
    return unsubscribe;
  }, []);

  // Reset page when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [activeFilter, genderFilter, priceSort]);

  // Scroll to top when page changes
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }, [currentPage]);
  const esRef = useRef(null);  // EventSource ref so we can close it

  // On mount, try to get countries from backend (with fallback)
  useEffect(() => {
    // Countries are hardcoded above — no fetch needed for the dropdown.
    // We still ping the backend to warm it up.
    fetch(`${API_BASE}/api/countries`).catch(() => { });
  }, []);

  // Close any existing EventSource when component unmounts
  useEffect(() => () => esRef.current?.close(), []);

  const executeSearch = (searchQuery, searchCountry, searchGender) => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }

    setLoading(true);
    setMlRunning(true);
    setProducts([]);
    setActiveFilter("all");
    setCurrentPage(1);
    setProgress(null);
    setSearchMeta({ query: searchQuery, country: searchCountry });

    const params = new URLSearchParams({ q: searchQuery, country: searchCountry, gender: searchGender });
    const url = `${API_BASE}/api/stream?${params}`;
    const es = new EventSource(url);
    esRef.current = es;

    es.addEventListener("status", (ev) => {
      const d = JSON.parse(ev.data);
      setProgress({ done: 0, total: d.total_brands });
    });

    es.addEventListener("products", (ev) => {
      const d = JSON.parse(ev.data);
      setProducts((prev) => {
        const existingUrls = new Set(prev.map((p) => p.url));
        const newItems = (d.items || []).filter((p) => !existingUrls.has(p.url));
        return [...prev, ...newItems];
      });
      setProgress({ done: d.brands_done, total: d.total_brands });
    });

    es.addEventListener("brand_done", (ev) => {
      const d = JSON.parse(ev.data);
      setProgress({ done: d.brands_done, total: d.total_brands });
    });

    es.addEventListener("done", (ev) => {
      setLoading(false);
      setMlRunning(false);
      es.close();
      esRef.current = null;
    });

    es.addEventListener("error", () => {
      setLoading(false);
      setMlRunning(false);
      es.close();
      esRef.current = null;
    });
  };

  // Cancel an in-progress scan. Keeps whatever products have already arrived
  // so the user isn't punished for stopping early.
  const stopSearch = () => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    setLoading(false);
    setMlRunning(false);
  };

  const handleSearch = async (e) => {
    if (e) e.preventDefault();
    if (!query.trim()) return;

    if (user) {
      try {
        const userRef = doc(db, "users", user.uid);
        const snap = await getDoc(userRef);
        if (!snap.exists()) {
          await setDoc(userRef, { searchHistory: [query] });
        } else {
          await updateDoc(userRef, { searchHistory: arrayUnion(query) });
        }
        console.info(`[history] saved "${query}" to users/${user.uid}`);
        setHistoryError(null);
        // Refresh recommendations in the background so the homepage is fresh
        // next time the user lands on it. Don't await — search shouldn't block.
        refreshRecommendations(user);
      } catch(err) {
        console.error("History save error:", err.code, err.message, err);
        if (err.code === "permission-denied") {
          setHistoryError(
            "Firestore rejected the write (permission-denied). Deploy firestore.rules " +
            "or open Firebase Console → Firestore → Rules and allow authenticated users " +
            "to write /users/{uid}. Until then, search history won't persist."
          );
        } else {
          setHistoryError(`Couldn't save search history: ${err.code || err.message}`);
        }
      }
    }

    setGenderFilter("all");
    executeSearch(query, country, "all");
  };

  const filtered = useMemo(() => {
    let arr = products;
    if (activeFilter !== "all") arr = arr.filter((p) => p.brand === activeFilter);
    if (genderFilter !== "all") {
      const gf = genderFilter.toLowerCase();
      arr = arr.filter((p) => {
        const pg = (p.gender || "unisex").toLowerCase();
        return pg === gf;
      });
    }
    if (priceSort === "low") arr = [...arr].sort((a, b) => a.price - b.price);
    if (priceSort === "high") arr = [...arr].sort((a, b) => b.price - a.price);
    return arr;
  }, [products, activeFilter, genderFilter, priceSort]);

  const ITEMS_PER_PAGE = 48;
  const totalPages = Math.ceil(filtered.length / ITEMS_PER_PAGE) || 1;
  const paginated = useMemo(() => {
    const start = (currentPage - 1) * ITEMS_PER_PAGE;
    return filtered.slice(start, start + ITEMS_PER_PAGE);
  }, [filtered, currentPage]);

  const brandsFound = useMemo(() => {
    const set = new Set(products.map((p) => p.brand));
    return [...set].sort();
  }, [products]);

  if (loadingAuth) {
    return (
      <div className="app" style={{display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh'}}>
        <div className="scanning-placeholder">
          <span className="spin-lg" />
          <p>Loading session…</p>
        </div>
        <GlobalStyles />
      </div>
    );
  }

  if (!user) {
    return (
      <div className="app">
        <AuthModal onClose={() => {}} forceOpen={true} />
        <GlobalStyles />
      </div>
    );
  }

  return (
    <div className="app">
      <Header user={user} onAuthClick={() => setShowAuthModal(true)} onLogout={() => signOut(auth)} />
      <Hero
        country={country}
        countries={ALL_COUNTRIES}
        setCountry={setCountry}
        query={query}
        setQuery={setQuery}
        onSearch={handleSearch}
        onStop={stopSearch}
        loading={loading}
        progress={progress}
      />
      {historyError && (
        <div className="history-error-banner">
          <strong>Heads up:</strong> {historyError}
        </div>
      )}
      {mlRunning && products.length > 0 && (
        <MlBanner />
      )}
      {searchMeta && (
        <ResultsSection
          products={products}
          filtered={filtered}
          paginated={paginated}
          currentPage={currentPage}
          setCurrentPage={setCurrentPage}
          totalPages={totalPages}
          brandsFound={brandsFound}
          activeFilter={activeFilter}
          setActiveFilter={setActiveFilter}
          genderFilter={genderFilter}
          setGenderFilter={setGenderFilter}
          priceSort={priceSort}
          setPriceSort={setPriceSort}
          query={searchMeta.query}
          country={searchMeta.country}
          loading={loading}
          progress={progress}
        />
      )}
      {!searchMeta && recommendations.length > 0 && <RecommendationSection recommendations={recommendations} onSearch={setQuery} user={user} />}
      {!searchMeta && recommendations.length === 0 && <FeaturedSection setQuery={setQuery} user={user} />}
      <Footer />
      {showAuthModal && <AuthModal onClose={() => setShowAuthModal(false)} />}
      <GlobalStyles />
    </div>
  );
}

// ----------- COMPONENTS -----------

function Header({ user, onAuthClick, onLogout }) {
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
          {user ? (
            <UserAvatar user={user} onLogout={onLogout} />
          ) : (
            <button className="text-btn nav-label" style={{textDecoration: 'none', margin:0, padding:0, width: 'auto'}} onClick={onAuthClick}>sign in</button>
          )}
        </nav>
      </div>
    </header>
  );
}

function UserAvatar({ user, onLogout }) {
  const name = user.displayName || user.email.split('@')[0];
  const initial = name.charAt(0).toUpperCase();

  return (
    <div className="user-profile">
      <div className="avatar">{initial}</div>
      <span className="username">{name}</span>
      <button className="text-btn" style={{marginLeft: '16px', marginTop: 0, width: 'auto', textDecoration: 'none'}} onClick={onLogout}>logout</button>
    </div>
  );
}

function Hero({ country, countries, setCountry, query, setQuery, onSearch, onStop, loading, progress }) {
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

          <div className="search-actions">
            <button className="search-btn" type="submit" disabled={loading || !query.trim()}>
              {loading ? (
                <span className="btn-loading">
                  <span className="spin" />
                  <span>
                    {progress
                      ? `${progress.done} / ${progress.total} brands…`
                      : "connecting…"}
                  </span>
                </span>
              ) : (
                <>
                  <span>find</span>
                  <span className="arrow">→</span>
                </>
              )}
            </button>
            {loading && (
              <button
                type="button"
                className="stop-btn"
                onClick={onStop}
                aria-label="Stop scanning"
              >
                <span>stop</span>
                <span className="arrow">×</span>
              </button>
            )}
          </div>

          {loading && progress && (
            <div className="progress-bar-wrap">
              <div
                className="progress-bar"
                style={{ width: `${Math.round((progress.done / progress.total) * 100)}%` }}
              />
            </div>
          )}
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

function MlBanner() {
  return (
    <div className="ml-banner">
      <span className="ml-dot" />
      <span>Discovering new brands in the background — library is growing…</span>
    </div>
  );
}

function ResultsSection({
  products, filtered, paginated,
  currentPage, setCurrentPage, totalPages,
  brandsFound, activeFilter, setActiveFilter,
  genderFilter, setGenderFilter,
  priceSort, setPriceSort,
  query, country, loading, progress,
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
            {products.length} listing{products.length !== 1 ? "s" : ""}
            {" across "}
            {brandsFound.length} brand{brandsFound.length !== 1 ? "s" : ""}
            {loading && progress && (
              <span className="scanning-badge">
                &nbsp;· scanning {progress.done}/{progress.total} brands
              </span>
            )}
          </p>
        </div>

        <div className="sort-controls">
          <span className="field-label">gender</span>
          <select
            className="gender-select sort-btn"
            value={genderFilter}
            onChange={(e) => setGenderFilter(e.target.value)}
          >
            <option value="all">all</option>
            <option value="female">female</option>
            <option value="male">male</option>
            <option value="unisex">unisex</option>
          </select>
          <span className="field-label" style={{ marginLeft: "12px" }}>sort</span>
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

      {brandsFound.length > 0 && (
        <div className="brand-filters">
          <button
            className={`brand-pill ${activeFilter === "all" ? "active" : ""}`}
            onClick={() => setActiveFilter("all")}
          >
            all brands
            <span className="pill-count">{products.length}</span>
          </button>
          {brandsFound.map((b) => {
            const count = products.filter((p) => p.brand === b).length;
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

      {filtered.length === 0 && !loading ? (
        <EmptyState />
      ) : filtered.length === 0 && loading ? (
        <div className="scanning-placeholder">
          <span className="spin-lg" />
          <p>Scanning brands — first results will appear shortly…</p>
        </div>
      ) : (
        <>
          <div className="grid">
            {paginated.map((p, i) => {
              const absoluteIndex = (currentPage - 1) * 48 + i;
              return <ProductCard key={p.url || i} product={p} index={absoluteIndex} />
            })}
          </div>
          {totalPages > 1 && (
            <div className="pagination">
              <button
                className="sort-btn"
                disabled={currentPage === 1}
                onClick={() => setCurrentPage(p => p - 1)}
              >
                ← prev
              </button>
              <span className="page-info">
                page {currentPage} of {totalPages}
              </span>
              <button
                className="sort-btn"
                disabled={currentPage === totalPages}
                onClick={() => setCurrentPage(p => p + 1)}
              >
                next →
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function ProductCard({ product, index }) {
  return (
    <article className="card" style={{ animationDelay: `${(index % 12) * 40}ms` }}>
      <div className="card-image">
        <span className="card-num">{String(index + 1).padStart(3, "0")}</span>
        {product.image_url ? (
          <img src={product.image_url} alt={product.title} loading="lazy" />
        ) : (
          <div className="img-placeholder">
            <span>{product.brand.charAt(0)}</span>
          </div>
        )}
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

function AuthModal({ onClose, forceOpen }) {
  const [mode, setMode] = useState("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      if (mode === "login") {
        await signInWithEmailAndPassword(auth, email, password);
      } else {
        const res = await createUserWithEmailAndPassword(auth, email, password);
        await updateProfile(res.user, { displayName: username });
        window.location.reload();
        return;
      }
      if (!forceOpen) onClose();
    } catch (err) {
      setError(err.message);
    }
    setLoading(false);
  };

  if (forceOpen) {
    return (
      <div className="auth-split-screen">
        <div className="auth-image-pane">
          <div className="auth-brand">
            <span className="logo-num" style={{fontFamily: "'JetBrains Mono', monospace", fontSize: '14px', color: 'rgba(244,237,224,0.6)'}}>№ 001</span>
            <h1 className="logo-name" style={{fontSize: '64px', fontWeight: 800, letterSpacing: '-0.04em', lineHeight: 1}}>FOLIO<span className="logo-dot" style={{color: 'var(--accent)'}}>.</span></h1>
            <p className="logo-sub" style={{fontStyle: 'italic', color: 'rgba(244,237,224,0.8)', fontSize: '18px', marginTop: '12px'}}>/ atlas of garments</p>
          </div>
        </div>
        <div className="auth-form-pane">
          <div className="auth-form-container">
            <h3 className="modal-title">{mode === "login" ? "Welcome Back" : "Join the Atlas"}</h3>
            <p className="modal-sub">
              {mode === "login" 
                ? "Sign in to access your curated recommendations." 
                : "Create an account to save your searches and get personalized curation."}
            </p>
            
            {error && <div className="modal-error">{error}</div>}
            
            <form onSubmit={handleSubmit} className="modal-form">
              {mode === "signup" && (
                <div className="search-field">
                  <label className="field-label">username</label>
                  <input type="text" className="search-input" value={username} onChange={e => setUsername(e.target.value)} required />
                </div>
              )}
              <div className="search-field" style={{marginTop: mode === "signup" ? '20px' : '0'}}>
                <label className="field-label">email</label>
                <input type="email" className="search-input" value={email} onChange={e => setEmail(e.target.value)} required />
              </div>
              <div className="search-field" style={{marginTop: '20px'}}>
                <label className="field-label">password</label>
                <input type="password" className="search-input" value={password} onChange={e => setPassword(e.target.value)} required />
              </div>
              <button className="search-btn" style={{marginTop: '40px', width: '100%', justifyContent: 'center'}} type="submit" disabled={loading}>
                {loading ? <span className="spin" /> : (mode === "login" ? "Enter" : "Create Account")}
              </button>
            </form>
            
            <div className="modal-footer">
              <button className="text-btn" onClick={() => setMode(mode === "login" ? "signup" : "login")}>
                {mode === "login" ? "Need an account? Sign up" : "Already have an account? Sign in"}
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="auth-modal" onClick={e => e.stopPropagation()}>
        <button className="close-btn" onClick={onClose}>✕</button>
        <h3 className="modal-title">{mode === "login" ? "Sign In" : "Create Account"}</h3>
        <p className="modal-sub">To save your searches and get personalized curation.</p>
        
        {error && <div className="modal-error">{error}</div>}
        
        <form onSubmit={handleSubmit} className="modal-form">
          {mode === "signup" && (
            <div className="search-field">
              <label className="field-label">username</label>
              <input type="text" className="search-input" style={{color: 'var(--ink)', borderColor: 'rgba(26,20,16,0.2)'}} value={username} onChange={e => setUsername(e.target.value)} required />
            </div>
          )}
          <div className="search-field" style={{marginTop: mode === "signup" ? '20px' : '0'}}>
            <label className="field-label">email</label>
            <input type="email" className="search-input" style={{color: 'var(--ink)', borderColor: 'rgba(26,20,16,0.2)'}} value={email} onChange={e => setEmail(e.target.value)} required />
          </div>
          <div className="search-field" style={{marginTop: '20px'}}>
            <label className="field-label">password</label>
            <input type="password" className="search-input" style={{color: 'var(--ink)', borderColor: 'rgba(26,20,16,0.2)'}} value={password} onChange={e => setPassword(e.target.value)} required />
          </div>
          <button className="search-btn" style={{marginTop: '30px', width: '100%', justifyContent: 'center'}} type="submit" disabled={loading}>
            {loading ? "..." : (mode === "login" ? "Enter" : "Register")}
          </button>
        </form>
        
        <div className="modal-footer">
          <button className="text-btn" onClick={() => setMode(mode === "login" ? "signup" : "login")}>
            {mode === "login" ? "Need an account? Sign up" : "Already have an account? Sign in"}
          </button>
        </div>
      </div>
    </div>
  );
}

function RecommendationSection({ recommendations, onSearch, user }) {
  if (!recommendations || recommendations.length === 0) return null;
  const name = user?.displayName || user?.email?.split('@')[0] || "there";
  return (
    <section className="featured results">
      <p className="eyebrow centered">for you</p>
      <h3 className="feat-title">Welcome back, <em>{name}</em></h3>
      <div className="grid">
        {recommendations.slice(0, 12).map((p, i) => (
          <ProductCard key={p.url || i} product={p} index={i} />
        ))}
      </div>
    </section>
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

function FeaturedSection({ setQuery, user }) {
  const name = user?.displayName || user?.email?.split('@')[0] || "there";
  const suggestions = [
    "black t-shirt", "linen shirt", "denim jacket",
    "wide leg jeans", "wool sweater", "cotton hoodie",
  ];
  return (
    <section className="featured">
      <p className="eyebrow centered">start exploring</p>
      <h3 className="feat-title">Welcome, <em>{name}</em></h3>
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

// COUNTRY_NAMES is declared at the top of the file (used in COUNTRY_NAMES map)


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
        font-size: 14px;
        text-transform: uppercase;
        letter-spacing: 0.2em;
        color: var(--accent);
        margin-bottom: 16px;
      }
      .eyebrow.centered { text-align: center; }
      em { font-style: italic; font-feature-settings: "ss01"; color: var(--accent); }
      .field-label {
        font-family: 'JetBrains Mono', monospace;
        font-size: 14px;
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
        font-size: 14px; color: var(--ink-soft);
      }
      .logo-name {
        font-size: 42px; font-weight: 800;
        letter-spacing: -0.04em; line-height: 1;
      }
      .logo-dot { color: var(--accent); }
      .logo-sub {
        font-style: italic; color: var(--ink-soft); font-size: 16px;
      }
      .nav { display: flex; gap: 12px; align-items: baseline; }
      .nav-label {
        font-family: 'JetBrains Mono', monospace;
        font-size: 14px; text-transform: uppercase;
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
        font-size: clamp(56px, 8vw, 104px);
        font-weight: 400;
        line-height: 0.92;
        letter-spacing: -0.03em;
        margin-bottom: 32px;
      }
      .lede {
        font-size: 22px;
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
        font-size: 28px;
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
        font-size: 16px;
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

      .search-actions {
        display: flex;
        gap: 12px;
        align-items: stretch;
      }
      .search-actions .search-btn { flex: 1; }
      .stop-btn {
        background: transparent;
        color: var(--paper);
        border: 2px solid rgba(244, 237, 224, 0.4);
        padding: 16px 22px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 16px;
        text-transform: uppercase;
        letter-spacing: 0.2em;
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 10px;
        transition: background 150ms, color 150ms, border-color 150ms, transform 150ms;
      }
      .stop-btn:hover {
        background: var(--paper);
        color: var(--ink);
        border-color: var(--paper);
        transform: translate(2px, -2px);
      }
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

      /* PROGRESS BAR */
      .progress-bar-wrap {
        height: 3px;
        background: rgba(244,237,224,0.15);
        border-radius: 2px;
        overflow: hidden;
      }
      .progress-bar {
        height: 100%;
        background: var(--accent);
        border-radius: 2px;
        transition: width 400ms ease;
      }

      /* HISTORY ERROR BANNER */
      .history-error-banner {
        max-width: 1400px;
        margin: 16px auto 0;
        padding: 14px 20px;
        background: var(--accent);
        color: var(--paper);
        font-family: 'JetBrains Mono', monospace;
        font-size: 13px;
        line-height: 1.5;
        letter-spacing: 0.04em;
        border-radius: 2px;
      }
      .history-error-banner strong {
        text-transform: uppercase;
        letter-spacing: 0.15em;
        margin-right: 8px;
      }

      /* ML BANNER */
      .ml-banner {
        max-width: 1400px;
        margin: 0 auto;
        padding: 10px 40px;
        display: flex;
        align-items: center;
        gap: 10px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 13px;
        text-transform: uppercase;
        letter-spacing: 0.15em;
        color: var(--ink-soft);
        border-bottom: 1px dashed rgba(26,20,16,0.2);
      }
      .ml-dot {
        width: 8px; height: 8px;
        border-radius: 50%;
        background: var(--success);
        animation: pulse 1.5s ease-in-out infinite;
        flex-shrink: 0;
      }
      @keyframes pulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.4; transform: scale(0.7); }
      }

      /* SCANNING PLACEHOLDER */
      .scanning-placeholder {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 24px;
        padding: 100px 20px;
        color: var(--ink-soft);
        font-family: 'JetBrains Mono', monospace;
        font-size: 16px;
        text-transform: uppercase;
        letter-spacing: 0.15em;
      }
      .spin-lg {
        width: 40px; height: 40px;
        border: 3px solid rgba(26,20,16,0.15);
        border-top-color: var(--accent);
        border-radius: 50%;
        animation: spin 1s linear infinite;
      }
      .scanning-badge {
        color: var(--accent);
      }

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
        font-size: 16px;
        text-transform: uppercase;
        letter-spacing: 0.2em;
      }
      @keyframes ticker {
        from { transform: translateX(0); }
        to { transform: translateX(-33.33%); }
      }

      /* MODAL */
      .auth-split-screen {
        display: flex; width: 100vw; height: 100vh;
        background: var(--paper);
      }
      .auth-image-pane {
        flex: 1;
        background-color: var(--ink);
        background-image: url('https://images.unsplash.com/photo-1441984904996-e0b6ba687e04?q=80&w=2000&auto=format&fit=crop');
        background-size: cover;
        background-position: center;
        position: relative;
        display: flex; flex-direction: column; justify-content: flex-end; padding: 60px;
        color: var(--paper);
      }
      .auth-image-pane::after {
        content: ''; position: absolute; inset: 0;
        background: linear-gradient(to top, rgba(26,20,16,0.8), rgba(26,20,16,0.2));
      }
      .auth-brand { position: relative; z-index: 2; }

      .auth-form-pane {
        flex: 1;
        display: flex; align-items: center; justify-content: center;
        background: var(--paper);
        padding: 40px;
      }
      .auth-form-container {
        width: 100%; max-width: 440px;
      }
      .auth-form-pane .search-input {
        color: var(--ink);
        border-color: rgba(26,20,16,0.2);
      }
      .auth-form-pane .search-input:focus { border-color: var(--accent); }

      .modal-overlay {
        position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
        background: rgba(26,20,16,0.6);
        backdrop-filter: blur(4px);
        display: flex; align-items: center; justify-content: center;
        z-index: 100;
      }
      .auth-modal {
        background: var(--paper);
        padding: 40px;
        border-radius: 2px;
        box-shadow: 12px 12px 0 var(--accent-soft);
        width: 100%; max-width: 440px;
        position: relative;
      }
      .close-btn {
        position: absolute; top: 20px; right: 20px;
        background: none; border: none; font-size: 20px; cursor: pointer; color: var(--ink-soft);
      }
      .modal-title { font-size: 32px; font-weight: 400; margin-bottom: 8px; }
      .modal-sub { color: var(--ink-soft); margin-bottom: 24px; font-style: italic; }
      .modal-error { color: var(--paper); background: var(--accent); padding: 12px; font-family: 'JetBrains Mono', monospace; font-size: 12px; margin-bottom: 24px; border-radius: 2px; }
      .text-btn { background: none; border: none; font-family: 'JetBrains Mono', monospace; font-size: 12px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--ink-soft); cursor: pointer; text-decoration: underline; margin-top: 24px; width: 100%; text-align: center; }

      /* HEADER AVATAR */
      .user-profile {
        display: flex; align-items: center; gap: 12px;
      }
      .avatar {
        width: 32px; height: 32px; border-radius: 50%;
        background: var(--accent); color: var(--paper);
        display: flex; align-items: center; justify-content: center;
        font-family: 'JetBrains Mono', monospace; font-size: 14px; font-weight: 500;
      }
      .username {
        font-family: 'JetBrains Mono', monospace; font-size: 14px;
        color: var(--ink); text-transform: lowercase; letter-spacing: 0.1em;
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
        font-size: clamp(40px, 5vw, 64px);
        font-weight: 400;
        letter-spacing: -0.02em;
        line-height: 1;
        margin-bottom: 12px;
      }
      .results-meta {
        font-family: 'JetBrains Mono', monospace;
        font-size: 16px;
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
        font-size: 14px;
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
        font-size: 14px;
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
        font-size: 12px;
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
      .img-placeholder {
        width: 100%; height: 100%;
        display: flex; align-items: center; justify-content: center;
        background: var(--paper-2);
        font-family: 'Fraunces', serif;
        font-size: 80px;
        font-weight: 800;
        color: rgba(26,20,16,0.08);
        letter-spacing: -0.04em;
        user-select: none;
      }
      .card-num {
        position: absolute;
        top: 12px; left: 12px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 14px;
        background: var(--paper);
        padding: 4px 8px;
        letter-spacing: 0.15em;
        z-index: 2;
      }
      .card-brand {
        font-family: 'JetBrains Mono', monospace;
        font-size: 14px;
        text-transform: uppercase;
        letter-spacing: 0.2em;
        color: var(--accent);
        margin-bottom: 6px;
      }
      .card-title {
        font-size: 22px;
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
      .meta-row { display: flex; justify-content: space-between; gap: 12px; font-size: 15px; }
      .meta-label {
        font-family: 'JetBrains Mono', monospace;
        font-size: 14px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--ink-soft);
      }
      .meta-val {
        font-size: 15px;
        text-align: right;
        color: var(--ink);
      }
      .card-foot {
        display: flex; justify-content: space-between; align-items: end;
      }
      .price {
        font-size: 28px;
        font-weight: 500;
        letter-spacing: -0.02em;
      }
      .currency { font-size: 16px; color: var(--ink-soft); margin-right: 2px; }
      .visit-btn {
        font-family: 'JetBrains Mono', monospace;
        font-size: 14px;
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

      /* PAGINATION */
      .pagination {
        display: flex;
        justify-content: center;
        align-items: center;
        gap: 20px;
        margin-top: 60px;
      }
      .page-info {
        font-family: 'JetBrains Mono', monospace;
        font-size: 14px;
        text-transform: uppercase;
        letter-spacing: 0.12em;
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
