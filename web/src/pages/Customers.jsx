import { useMemo, useState } from "react";
import Drawer from "../components/Drawer";
import Icon from "../components/Icon";
import InvoiceDrawer from "../components/InvoiceDrawer";
import NewInvoice from "../components/NewInvoice";
import { api, Badge, copy, day, initials, naira, useApi, useSession, useToast, when } from "../lib";

function AddCustomer({ onClose, onSaved }) {
  const [f, setF] = useState({ name: "", phone: "" });
  const [err, setErr] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    try { onSaved(await api("/api/customers", { method: "POST", body: { name: f.name, phone: f.phone || null } })); }
    catch (e2) { setErr(e2.message); }
  };
  return (
    <Drawer title="Add customer" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" form="addc">Save</button></>}>
      <form id="addc" className="form" onSubmit={submit}>
        <div className="field"><label htmlFor="cn">Name</label><input id="cn" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required autoFocus /></div>
        <div className="field"><label htmlFor="cp">WhatsApp number</label><input id="cp" type="tel" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} placeholder="0803 123 4567" />
          <span className="hint">Invoices, receipts and reminders go here.</span></div>
        {err && <div className="error">{err}</div>}
      </form>
    </Drawer>
  );
}

function CustomerDrawer({ id, onClose, onChange }) {
  const { me, pulse } = useSession();
  const toast = useToast();
  const { data: c, reload } = useApi(`/api/customers/${id}`, [pulse]);
  const [giving, setGiving] = useState(false);
  const [idv, setIdv] = useState({ kind: "bvn", value: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [inv, setInv] = useState(null);
  const [creating, setCreating] = useState(false);
  const isMonnify = me.merchant.provider === "monnify";

  const give = async () => {
    setBusy(true); setErr("");
    try {
      await api(`/api/customers/${id}/static-account`, { method: "POST", body: idv.value ? { [idv.kind]: idv.value } : {} });
      toast("Dedicated account created"); setGiving(false); reload(); onChange();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  if (!c) return <Drawer title="Customer" onClose={onClose}><p className="muted">Loading…</p></Drawer>;
  return (
    <>
      <Drawer title={c.name} sub={c.phone ? `+${c.phone}` : "No WhatsApp number"} onClose={onClose}
        footer={<button className="btn primary" onClick={() => setCreating(true)}><Icon name="plus" />New invoice for {c.name.split(" ")[0]}</button>}>
        <dl className="kv">
          <dt>Owes you</dt><dd className="num" style={{ color: c.owed ? "var(--ink)" : "var(--muted)" }}>{naira(c.owed)}</dd>
          <dt>Credit with you</dt><dd className="num">{naira(c.credit_balance)}</dd>
          <dt>Customer since</dt><dd>{day(c.created_at)}</dd>
        </dl>

        <div>
          <div className="section-title">Dedicated account</div>
          {c.static_account ? (
            <div className="acct-box">
              <Icon name="bank" />
              <div style={{ flex: 1 }}><div className="num">{c.static_account.account_number}</div><div className="muted small">{c.static_account.bank_name}</div></div>
              <button className="btn sm" onClick={() => copy(c.static_account.account_number, toast)}><Icon name="copy" />Copy</button>
            </div>
          ) : giving ? (
            <div className="callout blue">
              <div className="small">Regular customers can have one permanent account number. Every transfer into it is recorded against {c.name.split(" ")[0]} automatically.
                {isMonnify && " Monnify requires the customer's BVN or NIN for this. It's sent to Monnify and not stored by us."}</div>
              {isMonnify && (
                <div style={{ display: "flex", gap: 8 }}>
                  <select className="input" style={{ width: 90 }} value={idv.kind} onChange={(e) => setIdv({ ...idv, kind: e.target.value })}><option value="bvn">BVN</option><option value="nin">NIN</option></select>
                  <input className="input" inputMode="numeric" maxLength={11} placeholder="11 digits" value={idv.value} onChange={(e) => setIdv({ ...idv, value: e.target.value.replace(/\D/g, "") })} />
                </div>
              )}
              {err && <div className="error">{err}</div>}
              <div className="btns"><button className="btn sm primary" disabled={busy} onClick={give}>{busy ? "Creating…" : "Create account"}</button>
                <button className="btn sm" onClick={() => setGiving(false)}>Not now</button></div>
            </div>
          ) : (
            <button className="btn sm" onClick={() => setGiving(true)}><Icon name="bank" />Give {c.name.split(" ")[0]} a permanent account number</button>
          )}
        </div>

        <div>
          <div className="section-title">Invoices</div>
          {c.invoices.length === 0 && <p className="muted small">No invoices yet.</p>}
          <div className="list">
            {c.invoices.map((i) => (
              <button key={i.id} className="li" style={{ gridTemplateColumns: "1fr auto" }} onClick={() => setInv(i.id)}>
                <span><span className="li-name mono">{i.ref}</span> <Badge s={i.status} /><span className="li-sub" style={{ display: "block" }}>{i.description || "—"} · {day(i.created_at)}</span></span>
                <span className="li-amt">{naira(i.amount)}{i.balance > 0 && i.status !== "cancelled" && i.status !== "expired" && <small>{naira(i.balance)} left</small>}</span>
              </button>
            ))}
          </div>
        </div>

        {c.payments.length > 0 && (
          <div>
            <div className="section-title">Payments</div>
            <div className="list">
              {c.payments.map((p) => (
                <div key={p.id} className="li" style={{ gridTemplateColumns: "1fr auto" }}>
                  <span><span className="li-name">{p.invoice?.ref || "Held as credit"}</span><span className="li-sub" style={{ display: "block" }}>{p.payer_name} · {when(p.paid_at)}</span></span>
                  <span className="li-amt">{naira(p.amount)}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </Drawer>
      {inv && <InvoiceDrawer id={inv} onClose={() => setInv(null)} onChange={() => { reload(); onChange(); }} />}
      {creating && <NewInvoice customer={c} onClose={() => setCreating(false)} onCreated={(x) => { setCreating(false); reload(); onChange(); setInv(x.id); }} />}
    </>
  );
}

export default function Customers() {
  const { pulse, refresh } = useSession();
  const { data, reload } = useApi("/api/customers", [pulse]);
  const [q, setQ] = useState("");
  const [adding, setAdding] = useState(false);
  const [openId, setOpenId] = useState(null);
  const rows = useMemo(() => (data || []).filter((c) => c.name.toLowerCase().includes(q.trim().toLowerCase())), [data, q]);
  const totalOwed = (data || []).reduce((a, c) => a + c.owed, 0);

  return (
    <>
      <div className="page-head">
        <div><h1>Customers</h1><p>{data ? `${data.length} customer${data.length === 1 ? "" : "s"} · ${naira(totalOwed)} owed to you in total` : "Who owes you, and who's paid."}</p></div>
        <button className="btn" onClick={() => setAdding(true)}><Icon name="plus" />Add customer</button>
      </div>
      <div className="toolbar"><span /><label className="search"><Icon name="search" /><input placeholder="Search customers" value={q} onChange={(e) => setQ(e.target.value)} /></label></div>
      <div className="table-card"><div className="table-scroll">
        <table>
          <thead><tr><th>Customer</th><th className="r">Owes</th><th className="r hide-sm">Credit</th><th className="hide-sm">Account</th></tr></thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} className="click" onClick={() => setOpenId(c.id)}>
                <td><div style={{ display: "flex", gap: 12, alignItems: "center" }}><span className="avatar" style={{ width: 32, height: 32, fontSize: 12 }}>{initials(c.name)}</span>
                  <div><b>{c.name}</b><span className="sub">{c.phone ? `+${c.phone}` : "No number"}</span></div></div></td>
                <td className="r num"><b style={{ color: c.owed ? "var(--ink)" : "var(--muted)" }}>{naira(c.owed)}</b>{c.open_invoices > 0 && <span className="sub">{c.open_invoices} open</span>}</td>
                <td className="r num hide-sm">{c.credit_balance ? naira(c.credit_balance) : <span className="muted">—</span>}</td>
                <td className="hide-sm">{c.static_account ? <span className="mono">{c.static_account.account_number}</span> : <span className="muted small">Per invoice</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && rows.length === 0 && <div className="empty"><b>{data.length ? "No match" : "No customers yet"}</b>{data.length ? "" : "Customers are added automatically when you invoice them."}</div>}
      </div></div>
      {adding && <AddCustomer onClose={() => setAdding(false)} onSaved={(c) => { setAdding(false); reload(); setOpenId(c.id); }} />}
      {openId && <CustomerDrawer id={openId} onClose={() => setOpenId(null)} onChange={() => { reload(); refresh(); }} />}
    </>
  );
}
