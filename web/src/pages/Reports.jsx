import { useState } from "react";
import Icon from "../components/Icon";
import { download, naira, useApi, useSession, useToast } from "../lib";

const iso = (d) => d.toLocaleDateString("en-CA", { timeZone: "Africa/Lagos" });
const today = () => new Date();
const PRESETS = {
  "This month": () => { const t = today(); return [iso(new Date(t.getFullYear(), t.getMonth(), 1)), iso(t)]; },
  "Last month": () => { const t = today(); return [iso(new Date(t.getFullYear(), t.getMonth() - 1, 1)), iso(new Date(t.getFullYear(), t.getMonth(), 0))]; },
  "Last 7 days": () => [iso(new Date(Date.now() - 6 * 864e5)), iso(today())],
  "Last 30 days": () => [iso(new Date(Date.now() - 29 * 864e5)), iso(today())],
};

export default function Reports() {
  const { me, pulse } = useSession();
  const toast = useToast();
  const [[start, end], setRange] = useState(PRESETS["This month"]());
  const { data: r, error } = useApi(`/api/reports?start=${start}&end=${end}`, [pulse]);
  const owner = me.user.role === "owner";
  const max = Math.max(1, ...(r?.daily || []).map((d) => d.amount));
  const exp = (kind) => download(`/api/reports/export.csv?kind=${kind}&start=${start}&end=${end}`, `${kind}-${start}-to-${end}.csv`).catch((e) => toast(e.message));

  return (
    <>
      <div className="page-head">
        <div><h1>Reports</h1><p>Totals for any period, and a CSV your accountant can open in Excel.</p></div>
        {owner && <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn" onClick={() => exp("payments")}><Icon name="download" />Payments CSV</button>
          <button className="btn" onClick={() => exp("invoices")}><Icon name="download" />Invoices CSV</button>
        </div>}
      </div>
      <div className="toolbar">
        <div className="tabs">{Object.keys(PRESETS).map((k) => {
          const [s, e] = PRESETS[k]();
          return <button key={k} className={`tab${s === start && e === end ? " on" : ""}`} onClick={() => setRange([s, e])}>{k}</button>;
        })}</div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input className="input" type="date" value={start} max={end} onChange={(e) => setRange([e.target.value, end])} style={{ width: 150 }} aria-label="From" />
          <span className="muted">to</span>
          <input className="input" type="date" value={end} min={start} onChange={(e) => setRange([start, e.target.value])} style={{ width: 150 }} aria-label="To" />
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="stats">
        <div className="stat"><small>Received</small><strong>{r ? naira(r.received) : "…"}</strong><span>{r ? `${r.payments} payment${r.payments === 1 ? "" : "s"}` : ""}</span></div>
        <div className="stat"><small>Invoiced</small><strong>{r ? naira(r.invoiced) : "…"}</strong><span>{r ? `${r.invoices} invoices · ${r.paid_invoices} fully paid` : ""}</span></div>
        <div className="stat"><small>Still owed from this period</small><strong>{r ? naira(r.outstanding_from_period) : "…"}</strong>
          <span>{r && r.unmatched ? `${r.unmatched} unmatched payment${r.unmatched > 1 ? "s" : ""}` : "All payments matched"}</span></div>
      </div>
      <section className="panel">
        <div className="panel-head"><h2>Received per day</h2></div>
        <div className="bars" style={{ gridTemplateColumns: `repeat(${r?.daily.length || 1},minmax(0,1fr))`, gap: (r?.daily.length || 0) > 20 ? 3 : 10 }}>
          {(r?.daily || []).map((d) => (
            <div key={d.date} className="bar" title={`${d.date}: ${naira(d.amount)}`}>
              {(r.daily.length <= 14) && <em>{d.amount ? naira(d.amount, { short: true }) : ""}</em>}
              <i style={{ height: `${Math.max(2, (d.amount / max) * 100)}%`, background: d.amount ? "var(--brand)" : undefined }} />
              {(r.daily.length <= 14) && <span>{new Date(d.date + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" })}</span>}
            </div>
          ))}
        </div>
      </section>
      {!owner && <p className="muted small">CSV export is available to the business owner.</p>}
    </>
  );
}
