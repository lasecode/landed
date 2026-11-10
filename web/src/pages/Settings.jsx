import { useEffect, useState } from "react";
import Icon from "../components/Icon";
import { api, copy, useApi, useSession, useToast } from "../lib";

function Business({ b, onSaved }) {
  const toast = useToast();
  const [f, setF] = useState(b);
  const [err, setErr] = useState("");
  useEffect(() => { setF(b); }, [b]);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const save = async (e) => {
    e.preventDefault(); setErr("");
    try {
      await api("/api/settings/business", { method: "PATCH", body: {
        business_name: f.business_name, alert_phone: f.alert_phone, reminder_days: f.reminder_days,
        invoice_expiry_days: Number(f.invoice_expiry_days) } });
      toast("Saved"); onSaved();
    } catch (e2) { setErr(e2.message); }
  };
  return (
    <section className="panel">
      <div className="panel-head"><h2>Business</h2></div>
      <form className="form panel-body" onSubmit={save}>
        <div className="field"><label htmlFor="bn">Business name</label><input id="bn" value={f.business_name || ""} onChange={set("business_name")} />
          <span className="hint">Customers see this on every invoice and receipt.</span></div>
        <div className="field"><label htmlFor="ap">WhatsApp number for payment alerts</label><input id="ap" type="tel" value={f.alert_phone || ""} onChange={set("alert_phone")} /></div>
        <div className="row2">
          <div className="field"><label htmlFor="rd">Send reminders after (days)</label><input id="rd" value={f.reminder_days || ""} onChange={set("reminder_days")} placeholder="2,5" />
            <span className="hint">Days after the invoice is sent. Leave empty for no reminders.</span></div>
          <div className="field"><label htmlFor="ex">Invoice account stays open for (days)</label><input id="ex" type="number" min={1} max={90} value={f.invoice_expiry_days || 7} onChange={set("invoice_expiry_days")} /></div>
        </div>
        {err && <div className="error">{err}</div>}
        <div><button className="btn primary">Save changes</button></div>
      </form>
    </section>
  );
}

function Provider({ p, onSaved }) {
  const toast = useToast();
  const [f, setF] = useState({ api_key: "", secret_key: "", contract_code: p.contract_code, live: p.live });
  const [editing, setEditing] = useState(p.provider !== "monnify");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async (e) => {
    e.preventDefault(); setErr(""); setBusy(true);
    try { await api("/api/settings/provider", { method: "PUT", body: { provider: "monnify", ...f } }); toast("Monnify connected"); setEditing(false); onSaved(); }
    catch (e2) { setErr(e2.message); }
    setBusy(false);
  };
  const connected = p.provider === "monnify" && p.connected;
  return (
    <section className="panel">
      <div className="panel-head"><h2>Payment provider</h2>{connected && <span className="ok-pill small"><Icon name="check" />Monnify connected{p.live ? "" : " (sandbox)"}</span>}</div>
      <div className="panel-body form">
        <p className="muted small">Money goes straight from Monnify to your bank account. We never hold it. Copy your keys from Monnify dashboard → Settings → API Keys & Webhooks.</p>
        {connected && (
          <div className="field"><label>Webhook URL</label>
            <div className="copy-row"><span style={{ flex: 1 }}>{p.webhook_url}</span><button className="btn sm" onClick={() => copy(p.webhook_url, toast)}><Icon name="copy" /></button></div>
            <span className="hint">Paste this into Monnify as your Transaction Completion webhook URL.</span></div>
        )}
        {editing ? (
          <form className="form" onSubmit={save}>
            <div className="row2">
              <div className="field"><label htmlFor="ak">API key</label><input id="ak" value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} placeholder={p.api_key || "MK_…"} autoComplete="off" /></div>
              <div className="field"><label htmlFor="sk">Secret key</label><input id="sk" type="password" value={f.secret_key} onChange={(e) => setF({ ...f, secret_key: e.target.value })} placeholder={p.secret_key || ""} autoComplete="new-password" /></div>
            </div>
            <div className="field"><label htmlFor="cc">Contract code</label><input id="cc" value={f.contract_code} onChange={(e) => setF({ ...f, contract_code: e.target.value })} /></div>
            <label className="check"><input type="checkbox" checked={f.live} onChange={(e) => setF({ ...f, live: e.target.checked })} /><span>These are <b>live</b> keys (real money). Leave unticked for sandbox keys.</span></label>
            {connected && <p className="muted small">Leave a key empty to keep the one already saved.</p>}
            {err && <div className="error">{err}</div>}
            <div style={{ display: "flex", gap: 8 }}><button className="btn primary" disabled={busy}>{busy ? "Checking keys…" : "Connect Monnify"}</button>
              {connected && <button type="button" className="btn" onClick={() => setEditing(false)}>Cancel</button>}</div>
          </form>
        ) : <div><button className="btn sm" onClick={() => setEditing(true)}>Update keys</button></div>}
        {p.provider === "mock" && <p className="small" style={{ color: "var(--amber)" }}>You're in test mode. Payments are simulated until you connect Monnify.</p>}
      </div>
    </section>
  );
}

function Staff({ staff, onSaved, myId }) {
  const toast = useToast();
  const [f, setF] = useState(null);
  const [err, setErr] = useState("");
  const add = async (e) => {
    e.preventDefault(); setErr("");
    try { await api("/api/settings/staff", { method: "POST", body: f }); toast("Login created"); setF(null); onSaved(); }
    catch (e2) { setErr(e2.message); }
  };
  const remove = async (u) => {
    if (!confirm(`Remove ${u.name}'s login?`)) return;
    try { await api(`/api/settings/staff/${u.id}`, { method: "DELETE" }); onSaved(); } catch (e) { toast(e.message); }
  };
  return (
    <section className="panel">
      <div className="panel-head"><h2>Staff logins</h2>{!f && <button className="btn sm" onClick={() => setF({ name: "", phone: "", password: "", role: "staff" })}><Icon name="plus" />Add</button>}</div>
      <p className="muted small" style={{ padding: "0 22px 8px" }}>Staff can create invoices and see payments. They can't change settings or export reports. Their WhatsApp number can also use the bot.</p>
      {staff.filter((u) => u.active).map((u) => (
        <div className="set-row" key={u.id}>
          <div><b>{u.name}</b><span>+{u.phone} · {u.role === "owner" ? "Owner" : "Staff"}</span></div>
          {u.id !== myId && <button className="btn sm danger" onClick={() => remove(u)}>Remove</button>}
        </div>
      ))}
      {f && (
        <form className="form set-row" style={{ display: "flex", flexDirection: "column", alignItems: "stretch" }} onSubmit={add}>
          <div className="row2">
            <div className="field"><label htmlFor="sn">Name</label><input id="sn" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></div>
            <div className="field"><label htmlFor="sp">Phone</label><input id="sp" type="tel" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} required /></div>
          </div>
          <div className="row2">
            <div className="field"><label htmlFor="spw">Temporary password</label><input id="spw" value={f.password} minLength={8} onChange={(e) => setF({ ...f, password: e.target.value })} required /></div>
            <div className="field"><label htmlFor="sr">Role</label><select id="sr" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}><option value="staff">Staff</option><option value="owner">Owner</option></select></div>
          </div>
          {err && <div className="error">{err}</div>}
          <div style={{ display: "flex", gap: 8 }}><button className="btn primary">Create login</button><button type="button" className="btn" onClick={() => setF(null)}>Cancel</button></div>
        </form>
      )}
    </section>
  );
}

export default function Settings() {
  const { me, reloadMe } = useSession();
  const { data, reload } = useApi("/api/settings");
  const saved = () => { reload(); reloadMe(); };
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <>
      <div className="page-head"><div><h1>Settings</h1></div></div>
      <div className="set-grid">
        <Business b={data.business} onSaved={saved} />
        <Provider p={data.provider} onSaved={saved} />
        <Staff staff={data.staff} onSaved={reload} myId={me.user.id} />
      </div>
    </>
  );
}
