import { useEffect, useRef, useState } from "react";
import Icon from "../components/Icon";
import { api, useSession } from "../lib";

const SAMPLES = ["invoice Tunde 45000 for 3 bags of rice", "today", "unpaid", "help"];

export default function BotTester() {
  const { me, refresh } = useSession();
  const [msgs, setMsgs] = useState([{ dir: "in", text: `Hi ${me.user.name.split(" ")[0]} 👋 This is how the bot works on WhatsApp. Try a command below.` }]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef(null);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [msgs]);

  const send = async (t) => {
    const body = (t ?? text).trim();
    if (!body || busy) return;
    setText(""); setBusy(true);
    setMsgs((m) => [...m, { dir: "out", text: body }]);
    try {
      const r = await api("/api/dev/bot", { method: "POST", body: { text: body } });
      setMsgs((m) => [...m, { dir: "in", text: r.reply }]);
      refresh();
    } catch (e) { setMsgs((m) => [...m, { dir: "in", text: e.message }]); }
    setBusy(false);
  };
  const fmt = (s) => s.split(/(\*[^*]+\*)/g).map((p, i) => (p.startsWith("*") && p.endsWith("*") ? <b key={i}>{p.slice(1, -1)}</b> : p));

  return (
    <>
      <div className="page-head"><div><h1>Try the WhatsApp bot</h1>
        <p>Once your WhatsApp number is connected you'll send these same messages from your phone. Invoices made here are real test invoices.</p></div></div>
      <section className="panel" style={{ maxWidth: 520, padding: 14 }}>
        <div className="chat" aria-live="polite">
          {msgs.map((m, i) => <div key={i} className={`msg ${m.dir}`}><div className="bub">{fmt(m.text)}</div></div>)}
          <div ref={end} />
        </div>
        <div className="chips">{SAMPLES.map((s) => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}</div>
        <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type a message" aria-label="Message" />
          <button className="btn primary" style={{ borderRadius: 99, width: 40, padding: 0 }} disabled={busy} aria-label="Send"><Icon name="send" /></button>
        </form>
      </section>
    </>
  );
}
