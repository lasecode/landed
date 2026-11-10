import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Icon from "../components/Icon";
import InvoiceDrawer from "../components/InvoiceDrawer";
import NewInvoice from "../components/NewInvoice";
import { Badge, day, naira, useApi, useSession } from "../lib";

const TABS = [["", "All"], ["unpaid", "Unpaid"], ["part_paid", "Part-paid"], ["paid", "Paid"], ["expired", "Expired"], ["cancelled", "Cancelled"]];

export default function Invoices() {
  const { pulse, refresh } = useSession();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") || "";
  const excessOnly = params.get("excess") === "1";
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const [openId, setOpenId] = useState(null);
  const { data: all, error } = useApi("/api/invoices?limit=500", [pulse]);

  const counts = useMemo(() => {
    const c = {};
    (all || []).forEach((i) => { c[i.status] = (c[i.status] || 0) + 1; });
    return c;
  }, [all]);
  const rows = useMemo(() => {
    const s = q.trim().toLowerCase();
    return (all || []).filter((i) =>
      (!status || i.status === status) &&
      (!excessOnly || i.excess_status === "pending") &&
      (!s || i.ref.toLowerCase().includes(s) || (i.customer?.name || "").toLowerCase().includes(s) || (i.description || "").toLowerCase().includes(s)));
  }, [all, status, q, excessOnly]);

  return (
    <>
      <div className="page-head">
        <div><h1>Invoices</h1><p>Each one has its own account number, so payments match themselves.</p></div>
        <button className="btn primary" onClick={() => setCreating(true)}><Icon name="plus" />New invoice</button>
      </div>
      <div className="toolbar">
        <div className="tabs" role="tablist">
          {TABS.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={status === k} className={`tab${status === k && !excessOnly ? " on" : ""}`}
              onClick={() => setParams(k ? { status: k } : {})}>
              {label}{k && counts[k] ? <span className="n">{counts[k]}</span> : null}
            </button>
          ))}
          {excessOnly && <button className="tab on">Overpaid</button>}
        </div>
        <label className="search"><Icon name="search" /><input placeholder="Search name, INV-number…" value={q} onChange={(e) => setQ(e.target.value)} /></label>
      </div>
      <div className="table-card">
        <div className="table-scroll">
          <table>
            <thead><tr><th>Invoice</th><th>Customer</th><th className="r">Amount</th><th className="r hide-sm">Paid</th><th>Status</th><th className="hide-sm">Created</th></tr></thead>
            <tbody>
              {rows.map((i) => (
                <tr key={i.id} className="click" onClick={() => setOpenId(i.id)}>
                  <td><b className="mono">{i.ref}</b><span className="sub">{i.description}</span></td>
                  <td>{i.customer?.name || <span className="muted">—</span>}</td>
                  <td className="r num"><b>{naira(i.amount)}</b>{i.excess_status === "pending" && <span className="sub" style={{ color: "var(--amber)" }}>+{naira(i.excess_amount)} extra</span>}</td>
                  <td className="r num hide-sm">{naira(i.amount_paid)}</td>
                  <td><Badge s={i.status} /></td>
                  <td className="hide-sm muted">{day(i.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {all && rows.length === 0 && (
            <div className="empty"><b>{all.length ? "Nothing matches" : "No invoices yet"}</b>
              {all.length ? "Try a different filter or search." : "Create your first invoice. Your customer gets an account number to pay into."}</div>
          )}
          {error && <div className="empty"><b>Couldn't load invoices</b>{error}</div>}
          {!all && !error && <div className="empty">Loading…</div>}
        </div>
      </div>
      {creating && <NewInvoice onClose={() => setCreating(false)} onCreated={(inv) => { setCreating(false); refresh(); setOpenId(inv.id); }} />}
      {openId && <InvoiceDrawer id={openId} onClose={() => setOpenId(null)} onChange={refresh} />}
    </>
  );
}
