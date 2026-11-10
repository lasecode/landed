import { useState } from "react";
import Icon from "../components/Icon";
import { api, useSession } from "../lib";

export default function Auth() {
  const { signIn } = useSession();
  const [mode, setMode] = useState("login");
  const [f, setF] = useState({ business_name: "", owner_name: "", phone: "", password: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    setErr(""); setBusy(true);
    try {
      const body = mode === "login" ? { phone: f.phone, password: f.password } : f;
      signIn(await api(`/api/auth/${mode}`, { method: "POST", body }));
    } catch (e2) { setErr(e2.message); }
    setBusy(false);
  };

  return (
    <div className="auth">
      <div className="auth-side">
        <div className="logo"><span className="logo-mark"><Icon name="check" /></span><b>Landed</b></div>
        <div>
          <h2>Stop checking your bank app.</h2>
          <p>Every invoice gets its own account number. When the transfer lands, you and your customer both get a WhatsApp confirmation. Fake receipts do nothing.</p>
        </div>
        <p className="quote">Confirmation comes from the bank, not from a screenshot.</p>
      </div>
      <div className="auth-main">
        <form className="auth-card form" onSubmit={submit}>
          <div>
            <h1>{mode === "login" ? "Welcome back" : "Create your account"}</h1>
            <p className="muted" style={{ marginTop: 4 }}>
              {mode === "login" ? "Sign in with your phone number." : "Free while you try it. No card needed."}
            </p>
          </div>
          {mode === "signup" && (
            <>
              <div className="field"><label htmlFor="bn">Business name</label>
                <input id="bn" value={f.business_name} onChange={set("business_name")} placeholder="Adaeze Foods" required /></div>
              <div className="field"><label htmlFor="on">Your name</label>
                <input id="on" value={f.owner_name} onChange={set("owner_name")} placeholder="Adaeze Okafor" required /></div>
            </>
          )}
          <div className="field"><label htmlFor="ph">Phone number</label>
            <input id="ph" type="tel" inputMode="tel" value={f.phone} onChange={set("phone")} placeholder="0803 123 4567" required autoComplete="tel" />
            {mode === "signup" && <span className="hint">Use your WhatsApp number. That's where payment alerts go.</span>}
          </div>
          <div className="field"><label htmlFor="pw">Password</label>
            <input id="pw" type="password" value={f.password} onChange={set("password")} minLength={mode === "signup" ? 8 : 1} required
              autoComplete={mode === "login" ? "current-password" : "new-password"} /></div>
          {err && <div className="error">{err}</div>}
          <button className="btn primary block" disabled={busy}>{busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}</button>
          <p className="muted small" style={{ textAlign: "center" }}>
            {mode === "login" ? "New here? " : "Already have an account? "}
            <button type="button" className="link" onClick={() => { setMode(mode === "login" ? "signup" : "login"); setErr(""); }}>
              {mode === "login" ? "Create an account" : "Sign in"}
            </button>
          </p>
        </form>
      </div>
    </div>
  );
}
