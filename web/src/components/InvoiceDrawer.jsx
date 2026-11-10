import { useState } from "react";
import Drawer from "./Drawer";
import Icon from "./Icon";
import { api, Badge, copy, day, naira, useApi, useSession, useToast, when } from "../lib";

const LEDGER = {
  payment: ["Payment received", ""],
  credit_applied: ["Customer credit used", "blue"],
  overpayment: ["Paid more than the invoice", "amber"],
  refund: ["Extra refunded", "grey"],
  credit: ["Extra kept as credit", "blue"],
  assignment: ["Assigned from unmatched payments", "blue"],
};

export function shareText(merchant, inv) {
  const a = inv.account;
  return `Hello${inv.customer ? " " + inv.customer.name.split(" ")[0] : ""}, here is your invoice from ${merchant.business_name}.\n\n`
    + `${inv.ref}: ${naira(inv.balance)}${inv.description ? " for " + inv.description : ""}\n\n`
    + (a ? `Pay by transfer to:\n${a.account_number}\n${a.bank_name}\n\n` : "")
    + `Your payment is confirmed automatically once it lands. ${inv.receipt_url}`;
}

export default function InvoiceDrawer({ id, onClose, onChange }) {
  const { me, pulse } = useSession();
  const toast = useToast();
  const { data: inv, reload, error } = useApi(`/api/invoices/${id}`, [pulse]);
  const [busy, setBusy] = useState(false);
  const [sim, setSim] = useState("");
  const test = me.merchant.provider === "mock";

  const act = async (fn, msg) => {
    setBusy(true);
    try { await fn(); toast(msg); reload(); onChange?.(); } catch (e) { toast(e.message); }
    setBusy(false);
  };

  if (error) return <Drawer title="Invoice" onClose={onClose}><div className="error">{error}</div></Drawer>;
  if (!inv) return <Drawer title="Invoice" onClose={onClose}><p className="muted">Loading…</p></Drawer>;

  const open = inv.status === "unpaid" || inv.status === "part_paid";
  const pct = Math.min(100, Math.round((inv.amount_paid / inv.amount) * 100));
  const text = shareText(me.merchant, inv);
  const waLink = `https://wa.me/${inv.customer?.phone || ""}?text=${encodeURIComponent(text)}`;

  return (
    <Drawer
      title={<span style={{ display: "flex", gap: 10, alignItems: "center" }}>{inv.ref} <Badge s={inv.status} /></span>}
      sub={`${inv.customer?.name || "No customer"} · created ${day(inv.created_at)}`}
      onClose={onClose}
      footer={<>
        {(inv.status === "unpaid" || inv.status === "expired") && (
          <button className="btn danger" disabled={busy} onClick={() => confirm(`Cancel ${inv.ref}? Its account number will stop being used.`) &&
            act(() => api(`/api/invoices/${id}/cancel`, { method: "POST" }), "Invoice cancelled")}>Cancel invoice</button>
        )}
        <a className="btn" href={inv.receipt_url} target="_blank" rel="noreferrer"><Icon name="out" />{inv.status === "paid" ? "Receipt" : "Customer view"}</a>
        {open && inv.customer?.phone && (
          <button className="btn primary" disabled={busy} onClick={() => act(() => api(`/api/invoices/${id}/resend`, { method: "POST" }), `Sent to ${inv.customer.name} on WhatsApp`)}>
            <Icon name="send" />Resend on WhatsApp</button>
        )}
      </>}
    >
      <div>
        <div className="big-amt">{naira(inv.amount)}</div>
        <div className="muted">{inv.description || "No description"}</div>
        <div className="progress" style={{ marginTop: 14 }}><i style={{ width: `${pct}%` }} /></div>
        <div className="muted small" style={{ marginTop: 6 }}>{naira(inv.amount_paid)} paid · {naira(inv.balance)} left</div>
      </div>

      {inv.excess_status === "pending" && (
        <div className="callout">
          <div><b>{inv.customer?.name || "The customer"} paid {naira(inv.excess_amount)} more than the invoice.</b>
            <div className="small" style={{ marginTop: 2 }}>Refund it from your bank app, or keep it as credit for their next order.</div></div>
          <div className="btns">
            <button className="btn sm" disabled={busy} onClick={() => act(() => api(`/api/invoices/${id}/excess`, { method: "POST", body: { decision: "credit" } }), "Kept as credit")}>Keep as credit</button>
            <button className="btn sm" disabled={busy} onClick={() => act(() => api(`/api/invoices/${id}/excess`, { method: "POST", body: { decision: "refund" } }), "Marked as refunded")}>I've refunded it</button>
          </div>
        </div>
      )}

      {inv.account && (
        <div>
          <div className="section-title">{inv.account.type === "static" ? "Customer's dedicated account" : "Account for this invoice"}</div>
          <div className="acct-box">
            <Icon name="bank" />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="num">{inv.account.account_number}</div>
              <div className="muted small">{inv.account.bank_name}{inv.account.account_name ? ` · ${inv.account.account_name}` : ""}</div>
            </div>
            <button className="btn sm" onClick={() => copy(inv.account.account_number, toast)}><Icon name="copy" />Copy</button>
          </div>
          {inv.account.type === "dynamic" && inv.account.expires_at && open && (
            <div className="muted small" style={{ marginTop: 6 }}>Accepts payments until {when(inv.account.expires_at)}.</div>
          )}
          {open && (
            <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
              <button className="btn sm" onClick={() => copy(text, toast)}><Icon name="copy" />Copy message for customer</button>
              <a className="btn sm" href={waLink} target="_blank" rel="noreferrer"><Icon name="chat" />Share on WhatsApp</a>
            </div>
          )}
        </div>
      )}

      {test && open && inv.account && (
        <div className="callout blue">
          <div><b>Test a payment</b><div className="small">Pretend the customer sent a transfer to this account. Try less or more than the balance too.</div></div>
          <div style={{ display: "flex", gap: 8 }}>
            <div className="money-input" style={{ flex: 1 }}><span>₦</span>
              <input className="input" style={{ paddingLeft: 28 }} inputMode="decimal" placeholder={String(inv.balance / 100)} value={sim}
                onChange={(e) => setSim(e.target.value.replace(/[^\d.]/g, ""))} /></div>
            <button className="btn primary sm" style={{ height: 40 }} disabled={busy} onClick={() => act(() => api("/api/dev/simulate-payment", {
              method: "POST", body: { account_number: inv.account.account_number, amount_naira: sim || inv.balance / 100, payer_name: (inv.customer?.name || "Test payer").toUpperCase() },
            }).then(() => setSim("")), "Transfer sent. Watch it confirm.")}>Send transfer</button>
          </div>
        </div>
      )}

      <div>
        <div className="section-title">History</div>
        <div className="timeline">
          <div className="tl"><span className="dot grey" /><span>Invoice created</span><span className="muted small">{when(inv.created_at)}</span></div>
          {inv.ledger.filter((e) => LEDGER[e.type]).map((e) => (
            <div className="tl" key={e.id}>
              <span className={`dot ${LEDGER[e.type][1]}`} />
              <span>{LEDGER[e.type][0]}{e.type === "payment" && inv.payments.find((p) => p.id === e.payment_id)?.payer_name
                ? <span className="muted"> from {inv.payments.find((p) => p.id === e.payment_id).payer_name}</span> : null}</span>
              <span className="num" style={{ textAlign: "right" }}>{naira(e.amount)}<span className="muted small" style={{ display: "block" }}>{when(e.created_at)}</span></span>
            </div>
          ))}
          {inv.status === "paid" && <div className="tl"><span className="dot" /><b>Fully paid</b><span className="muted small">{when(inv.paid_at)}</span></div>}
          {inv.status === "expired" && <div className="tl"><span className="dot grey" /><span>Account expired before payment</span><span /></div>}
          {inv.status === "cancelled" && <div className="tl"><span className="dot red" /><span>Cancelled</span><span /></div>}
        </div>
      </div>
      {inv.reminders_sent > 0 && <p className="muted small">{inv.reminders_sent} WhatsApp reminder{inv.reminders_sent > 1 ? "s" : ""} sent.</p>}
    </Drawer>
  );
}
