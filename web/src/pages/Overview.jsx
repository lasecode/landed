import { useState } from "react";
import { Link } from "react-router-dom";
import Icon from "../components/Icon";
import InvoiceDrawer from "../components/InvoiceDrawer";
import NewInvoice from "../components/NewInvoice";
import { naira, useApi, useSession, when } from "../lib";

function lagosDate(offsetDays = 0) {
  const d = new Date(Date.now() - offsetDays * 864e5);
  return d.toLocaleDateString("en-CA", { timeZone: "Africa/Lagos" });
}

export default function Overview() {
  const { me, pulse, refresh } = useSession();
  const { data: o } = useApi("/api/overview", [pulse]);
  const { data: rep } = useApi(`/api/reports?start=${lagosDate(6)}&end=${lagosDate(0)}`, [pulse]);
  const [creating, setCreating] = useState(false);
  const [openInv, setOpenInv] = useState(null);
  const hour = Number(new Date().toLocaleString("en-GB", { timeZone: "Africa/Lagos", hour: "numeric", hour12: false }));
  const greet = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  const days = rep?.daily || [];
  const max = Math.max(1, ...days.map((d) => d.amount));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{greet}, {me.user.name.split(" ")[0]}</h1>
          <p>Every transfer here was confirmed by the bank, not by a screenshot.</p>
        </div>
        <button className="btn primary" onClick={() => setCreating(true)}><Icon name="plus" />New invoice</button>
      </div>

      {o && o.unmatched > 0 && (
        <div className="notice red">
          <span className="ic"><Icon name="alert" /></span>
          <p><b>{o.unmatched} payment{o.unmatched > 1 ? "s" : ""} couldn't be matched automatically.</b> Tell us who {o.unmatched > 1 ? "they're" : "it's"} from and the invoice updates.</p>
          <Link to="/unmatched" className="btn sm">Assign now</Link>
        </div>
      )}
      {o && o.overpaid > 0 && (
        <div className="notice">
          <span className="ic"><Icon name="alert" /></span>
          <p><b>{o.overpaid} customer{o.overpaid > 1 ? "s" : ""} paid more than the invoice.</b> Choose whether to refund the extra or keep it as credit.</p>
          <Link to="/invoices?status=paid&excess=1" className="btn sm">Review</Link>
        </div>
      )}

      <div className="stats">
        <div className="stat"><small>Received today</small><strong>{o ? naira(o.today) : "…"}</strong>
          <span>{o ? `${o.today_count} payment${o.today_count === 1 ? "" : "s"}` : ""}</span></div>
        <div className="stat"><small>This month</small><strong>{o ? naira(o.month) : "…"}</strong>
          <span>{o ? `${naira(o.week)} this week` : ""}</span></div>
        <div className="stat"><small>Still owed to you</small><strong>{o ? naira(o.outstanding) : "…"}</strong>
          <span>{o ? <Link className="link" to="/invoices?status=unpaid">{o.open_invoices} open invoice{o.open_invoices === 1 ? "" : "s"}</Link> : ""}</span></div>
      </div>

      <div className="home-grid">
        <section className="panel">
          <div className="panel-head"><h2>Last 7 days</h2><Link to="/reports" className="link">Reports</Link></div>
          <div className="chart-total"><strong>{naira(days.reduce((a, d) => a + d.amount, 0))}</strong><span className="muted small">received</span></div>
          <div className="bars" style={{ gridTemplateColumns: `repeat(${days.length || 7},1fr)` }}>
            {days.map((d, i) => (
              <div key={d.date} className={`bar${i === days.length - 1 ? " today" : ""}`} title={naira(d.amount)}>
                <em>{d.amount ? naira(d.amount, { short: true }) : ""}</em>
                <i style={{ height: `${Math.max(3, (d.amount / max) * 100)}%` }} />
                <span>{i === days.length - 1 ? "Today" : new Date(d.date + "T12:00:00").toLocaleDateString("en-GB", { weekday: "short" })}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="panel-head"><h2>Latest payments</h2></div>
          <div className="panel-body">
            {o && o.recent.length === 0 && (
              <div className="empty" style={{ padding: "28px 0" }}><b>No payments yet</b>Create an invoice and payments will appear here the moment they land.</div>
            )}
            <div className="list">
              {o?.recent.map((p) => (
                <button key={p.id} className="li" onClick={() => p.invoice && setOpenInv(p.invoice.id)}>
                  <span className={`li-ic ${p.status === "unmatched" ? "red" : ""}`}><Icon name={p.status === "unmatched" ? "alert" : "arrowIn"} /></span>
                  <span style={{ minWidth: 0 }}>
                    <span className="li-name">{p.customer?.name || p.payer_name || "Unknown sender"}</span>
                    <span className="li-sub" style={{ display: "block" }}>
                      {p.invoice ? p.invoice.ref : p.status === "unmatched" ? "Needs assigning" : p.status === "refunded" ? "Refunded" : "Held as credit"} · {when(p.paid_at)}
                    </span>
                  </span>
                  <span className="li-amt">{naira(p.amount)}</span>
                </button>
              ))}
            </div>
          </div>
        </section>
      </div>

      {creating && <NewInvoice onClose={() => setCreating(false)} onCreated={(inv) => { setCreating(false); refresh(); setOpenInv(inv.id); }} />}
      {openInv && <InvoiceDrawer id={openInv} onClose={() => setOpenInv(null)} onChange={refresh} />}
    </>
  );
}
