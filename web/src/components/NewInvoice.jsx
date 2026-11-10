import { useMemo, useState } from "react";
import Drawer from "./Drawer";
import { api, naira, useApi } from "../lib";

export default function NewInvoice({ onClose, onCreated, customer: preset }) {
  const { data: customers } = useApi("/api/customers");
  const [q, setQ] = useState(preset?.name || "");
  const [picked, setPicked] = useState(preset || null);
  const [phone, setPhone] = useState("");
  const [amount, setAmount] = useState("");
  const [desc, setDesc] = useState("");
  const [due, setDue] = useState("");
  const [send, setSend] = useState(true);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const matches = useMemo(() => {
    if (!customers || !q.trim() || picked) return [];
    const s = q.trim().toLowerCase();
    return customers.filter((c) => c.name.toLowerCase().includes(s)).slice(0, 5);
  }, [customers, q, picked]);

  const hasPhone = picked ? !!picked.phone : !!phone.trim();
  const amt = Number(String(amount).replace(/,/g, ""));

  const submit = async (e) => {
    e.preventDefault();
    setErr("");
    if (!q.trim()) return setErr("Who is this invoice for?");
    if (!amt || amt < 100) return setErr("Enter an amount of at least ₦100");
    setBusy(true);
    try {
      const body = {
        amount_naira: amt, description: desc, send_whatsapp: send && hasPhone,
        due_date: due ? new Date(due + "T23:59:00+01:00").toISOString() : null,
        ...(picked ? { customer_id: picked.id } : { customer_name: q.trim(), customer_phone: phone.trim() || null }),
      };
      onCreated(await api("/api/invoices", { method: "POST", body }));
    } catch (e2) { setErr(e2.message); setBusy(false); }
  };

  return (
    <Drawer title="New invoice" sub="We'll create a bank account number just for this invoice." onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" form="new-inv" disabled={busy}>{busy ? "Creating…" : "Create invoice"}</button>
      </>}>
      <form id="new-inv" className="form" onSubmit={submit}>
        <div className="field" style={{ position: "relative" }}>
          <label htmlFor="cust">Customer</label>
          <input id="cust" value={q} autoComplete="off" placeholder="Start typing a name"
            onChange={(e) => { setQ(e.target.value); setPicked(null); }} autoFocus={!preset} />
          {matches.length > 0 && (
            <div className="panel" style={{ position: "absolute", top: 68, left: 0, right: 0, zIndex: 2, boxShadow: "var(--shadow-lg)", padding: 4 }}>
              {matches.map((c) => (
                <button type="button" key={c.id} className="li" style={{ padding: "8px 10px", gridTemplateColumns: "1fr auto", borderTop: 0 }}
                  onClick={() => { setPicked(c); setQ(c.name); }}>
                  <span><span className="li-name">{c.name}</span><span className="li-sub" style={{ display: "block" }}>{c.phone || "No number saved"}</span></span>
                  <span className="li-amt">{c.owed ? <small>owes {naira(c.owed)}</small> : null}</span>
                </button>
              ))}
            </div>
          )}
          {picked?.credit_balance > 0 && <span className="hint">{picked.name} has {naira(picked.credit_balance)} credit. It will be used first.</span>}
          {picked?.static_account && <span className="hint">Uses their dedicated account {picked.static_account.account_number}.</span>}
        </div>
        {!picked && (
          <div className="field">
            <label htmlFor="ph">Customer's WhatsApp number <span className="muted">(optional)</span></label>
            <input id="ph" type="tel" inputMode="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="0803 123 4567" />
          </div>
        )}
        <div className="field">
          <label htmlFor="amt">Amount</label>
          <div className="money-input"><span>₦</span>
            <input id="amt" inputMode="decimal" value={amount} placeholder="45,000"
              onChange={(e) => setAmount(e.target.value.replace(/[^\d.,]/g, ""))} /></div>
        </div>
        <div className="field">
          <label htmlFor="desc">What's it for?</label>
          <input id="desc" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="3 bags of rice" maxLength={300} />
        </div>
        <div className="field">
          <label htmlFor="due">Due date <span className="muted">(optional)</span></label>
          <input id="due" type="date" value={due} onChange={(e) => setDue(e.target.value)} />
        </div>
        <label className="check">
          <input type="checkbox" checked={send && hasPhone} disabled={!hasPhone} onChange={(e) => setSend(e.target.checked)} />
          <span>Send it to the customer on WhatsApp{!hasPhone && <span className="muted"> (add their number first)</span>}</span>
        </label>
        {err && <div className="error">{err}</div>}
      </form>
    </Drawer>
  );
}
