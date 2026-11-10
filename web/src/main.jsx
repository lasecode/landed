import React, { useCallback, useEffect, useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import "./styles.css";
import Icon from "./components/Icon";
import { api, initials, Session, store, ToastProvider, useSession } from "./lib";
import Auth from "./pages/Auth";
import Overview from "./pages/Overview";
import Invoices from "./pages/Invoices";
import Customers from "./pages/Customers";
import Unmatched from "./pages/Unmatched";
import Reports from "./pages/Reports";
import Settings from "./pages/Settings";
import BotTester from "./pages/BotTester";

function Shell({ children }) {
  const { me, counts, logout } = useSession();
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => { setOpen(false); }, [loc.pathname]);
  const owner = me.user.role === "owner";
  const test = me.merchant.provider === "mock";
  const items = [
    ["/", "home", "Overview"],
    ["/invoices", "invoice", "Invoices"],
    ["/customers", "users", "Customers"],
    ["/unmatched", "alert", "Unmatched", counts.unmatched],
    ["/reports", "chart", "Reports"],
    ...(owner ? [["/settings", "settings", "Settings"]] : []),
  ];
  return (
    <div className="shell">
      <aside className={`side${open ? " open" : ""}`}>
        <div className="logo"><span className="logo-mark"><Icon name="check" /></span><b>Landed</b></div>
        <div className="biz">
          <span className="avatar">{initials(me.merchant.business_name)}</span>
          <div style={{ minWidth: 0 }}><b>{me.merchant.business_name}</b><span>{me.user.name} · {owner ? "Owner" : "Staff"}</span></div>
        </div>
        <nav className="nav">
          {items.map(([to, ic, label, n]) => (
            <NavLink key={to} to={to} end={to === "/"}>
              <Icon name={ic} />{label}{n ? <span className="count">{n}</span> : null}
            </NavLink>
          ))}
        </nav>
        <div className="side-foot">
          {test && <NavLink to="/bot"><Icon name="chat" />Try the WhatsApp bot</NavLink>}
          <button onClick={logout}><Icon name="logout" />Sign out</button>
        </div>
      </aside>
      {open && <div className="scrim" style={{ zIndex: 19 }} onClick={() => setOpen(false)} />}
      <div className="main">
        <header className="topbar">
          <button className="menu-btn" onClick={() => setOpen(true)} aria-label="Open menu"><Icon name="menu" /></button>
          <div className="top-right">
            {test && <span className="test-tag" title="Payments are simulated until you connect Monnify in Settings">Test mode</span>}
          </div>
        </header>
        <main className="content"><Boundary key={loc.pathname}>{children}</Boundary></main>
      </div>
    </div>
  );
}

class Boundary extends React.Component {
  constructor(p) { super(p); this.state = { err: null }; }
  static getDerivedStateFromError(err) { return { err }; }
  render() {
    if (!this.state.err) return this.props.children;
    return (
      <div className="empty" style={{ paddingTop: 80 }}>
        <b>Something went wrong on this page.</b>
        <p style={{ margin: "8px 0 16px" }}>Your data is safe. Reload to try again.</p>
        <button className="btn" onClick={() => location.reload()}>Reload</button>
      </div>
    );
  }
}

function App() {
  const [me, setMe] = useState(null);
  const [ready, setReady] = useState(false);
  const [counts, setCounts] = useState({ unmatched: 0 });
  const [pulse, setPulse] = useState(0);

  const loadMe = useCallback(async () => {
    if (!store.get()) { setMe(null); setReady(true); return; }
    try { setMe(await api("/api/auth/me")); } catch { setMe(null); }
    setReady(true);
  }, []);
  useEffect(() => { loadMe(); }, [loadMe]);
  useEffect(() => {
    const out = () => setMe(null);
    window.addEventListener("landed:logout", out);
    return () => window.removeEventListener("landed:logout", out);
  }, []);

  // light polling so new payments show up without refreshing
  useEffect(() => {
    if (!me) return;
    let last = null;
    const tick = async () => {
      try {
        const o = await api("/api/overview");
        setCounts({ unmatched: o.unmatched });
        const sig = `${o.today_count}:${o.unmatched}:${o.recent[0]?.id ?? ""}`;
        if (last !== null && sig !== last) setPulse((p) => p + 1);
        last = sig;
      } catch { /* offline */ }
    };
    tick();
    const t = setInterval(tick, 12000);
    return () => clearInterval(t);
  }, [me]);

  const session = useMemo(() => ({
    me, counts, pulse,
    refresh: () => setPulse((p) => p + 1),
    signIn: (s) => { store.set(s.token); setMe({ user: s.user, merchant: s.merchant }); },
    reloadMe: loadMe,
    logout: () => { store.set(null); setMe(null); },
  }), [me, counts, pulse, loadMe]);

  if (!ready) return null;
  return (
    <Session.Provider value={session}>
      {!me ? <Auth /> : (
        <Shell>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/invoices" element={<Invoices />} />
            <Route path="/customers" element={<Customers />} />
            <Route path="/unmatched" element={<Unmatched />} />
            <Route path="/reports" element={<Reports />} />
            {me.user.role === "owner" && <Route path="/settings" element={<Settings />} />}
            <Route path="/bot" element={<BotTester />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Shell>
      )}
    </Session.Provider>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <ToastProvider><App /></ToastProvider>
    </BrowserRouter>
  </React.StrictMode>
);
