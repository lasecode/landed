import { useMemo, useState } from "react";
import Drawer from "../components/Drawer";
import Icon from "../components/Icon";
import { api, naira, useApi, useSession, useToast, when } from "../lib";

function Assign({ p, onClose, onDone }) {
  const { me } = useSession();
  const toast = useToast();
  const [mode, setMode] = useState("invoice");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(null);
  const [busy, setBusy] = useState(false);
  const { data: invoices } = useApi("/api/invoices?limit=500");
  const { data: customers } = useApi("/api/customers");
  const owner = me.user.role === "owner";

  const options = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (mode === "invoice") {
      return (invoices || []).filter((i) => ["unpaid", "part_paid", "expired"].includes(i.status))
        .filter((i) => !s || i.ref.toLowerCase().includes(s) || (i.customer?.name || "").toLowerCase().includes(s))
        .sort((a, b) => Math.abs(a.balance - p.amount) - Math.abs(b.balance - p.amount)); // closest amount first
    }
    return (customers || []).filter((c) => !s || c.name.toLowerCase().includes(s));
  }, [mode, q, invoices, customers, p.amount]);

  const save = async () => {
    setBusy(true);
    try {
      await api(`/api/payments/${p.id}/assign`, { method: "POST", body: mode === "invoice" ? { invoice_id: sel } : { customer_id: sel } });
      toast("Payment assigned"); onDone();
    } catch (e) { toast(e.message); setBusy(false); }
  };
  const refund = async () => {
    if (!confirm("Only do this after you've sent the money back from your bank app. Continue?")) return;
    setBusy(true);
    try { await api(`/api/payments/${p.id}/refund`, { method: "POST" }); toast("Marked as refunded"); onDone(); }
    catch (e) { toast(e.message); setBusy(false); }
  };

  return (
    <Drawer title={`Assign ${naira(p.amount)}`} sub={`From ${p.payer_name || "unknown sender"} · ${when(p.paid_at)}`} onClose={onClose}
      footer={<>
        {owner && <button className="btn ghost" onClick={refund} disabled={busy} style={{ marginRight: "auto" }}>I sent it back</button>}
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!sel || busy} onClick={save}>Assign</button>
      </>}>
      <div className="callout"><div className="small"><b>Why it's here:</b> {p.unmatched_reason}. The money is confirmed. We just don't know which invoice it's for.</div></div>
      <div className="seg">
        <button className={mode === "invoice" ? "on" : ""} onClick={() => { setMode("invoice"); setSel(null); }}>An invoice</button>
        <button className={mode === "customer" ? "on" : ""} onClick={() => { setMode("customer"); setSel(null); }}>A customer</button>
      </div>
      <label className="search" style={{ width: "100%" }}><Icon name="search" /><input placeholder={mode === "invoice" ? "Search invoices" : "Search customers"} value={q} onChange={(e) => setQ(e.target.value)} /></label>
      <div className="list">
        {options.slice(0, 30).map((o) => (
          <label key={o.id} className="li" style={{ gridTemplateColumns: "20px 1fr auto", cursor: "pointer" }}>
            <input type="radio" name="sel" checked={sel === o.id} onChange={() => setSel(o.id)} style={{ accentColor: "var(--brand)" }} />
            {mode === "invoice" ? (
              <><span><span className="li-name mono">{o.ref}</span> <span className="li-sub">{o.customer?.name}</span>
                <span className="li-sub" style={{ display: "block" }}>{o.description}</span></span>
                <span className="li-amt">{naira(o.balance)}<small>{o.balance === p.amount ? "exact match" : "left to pay"}</small></span></>
            ) : (
              <><span className="li-name">{o.name}</span><span className="li-amt">{o.owed ? <small>owes {naira(o.owed)}</small> : null}</span></>
            )}
          </label>
        ))}
        {options.length === 0 && <p className="muted small">Nothing found.</p>}
      </div>
      {mode === "customer" && <p className="muted small">The money goes to their oldest unpaid invoices first. Anything left becomes credit.</p>}
    </Drawer>
  );
}

export default function Unmatched() {
  const { pulse, refresh } = useSession();
  const { data, reload } = useApi("/api/payments?status=unmatched", [pulse]);
  const [open, setOpen] = useState(null);
  return (
    <>
      <div className="page-head"><div><h1>Unmatched payments</h1>
        <p>Money that landed but couldn't be tied to an invoice, usually a transfer to an old or expired account. Tell us who it's from.</p></div></div>
      <div className="table-card"><div className="table-scroll">
        <table>
          <thead><tr><th>From</th><th className="r">Amount</th><th className="hide-sm">Why</th><th className="hide-sm">When</th><th /></tr></thead>
          <tbody>
            {(data || []).map((p) => (
              <tr key={p.id} className="click" onClick={() => setOpen(p)}>
                <td><b>{p.payer_name || "Unknown sender"}</b><span className="sub mono">to {p.account_number || "—"}</span></td>
                <td className="r num"><b>{naira(p.amount)}</b></td>
                <td className="hide-sm muted">{p.unmatched_reason}</td>
                <td className="hide-sm muted">{when(p.paid_at)}</td>
                <td className="r"><button className="btn sm">Assign</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && data.length === 0 && <div className="empty"><b>All clear</b>Every payment has been matched.</div>}
      </div></div>
      {open && <Assign p={open} onClose={() => setOpen(null)} onDone={() => { setOpen(null); reload(); refresh(); }} />}
    </>
  );
}
