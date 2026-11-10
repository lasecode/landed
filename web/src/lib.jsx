import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

export const API_URL = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");
const TOKEN_KEY = "landed.token";

export const store = {
  get() { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } },
  set(t) { try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ } },
};

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

export async function api(path, { method = "GET", body, raw } = {}) {
  const headers = { "Content-Type": "application/json" };
  const t = store.get();
  if (t) headers.Authorization = `Bearer ${t}`;
  let res;
  try {
    res = await fetch(API_URL + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch {
    throw new ApiError("Can't reach the server. Check your connection.", 0);
  }
  if (res.status === 401 && path !== "/api/auth/login") {
    store.set(null);
    window.dispatchEvent(new Event("landed:logout"));
  }
  if (!res.ok) {
    let msg = `Something went wrong (${res.status})`;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") msg = j.detail;
      else if (Array.isArray(j.detail)) msg = j.detail.map((d) => d.msg.replace(/^Value error, /, "")).join(". ");
    } catch { /* not json */ }
    throw new ApiError(msg, res.status);
  }
  return raw ? res : res.json();
}

export async function download(path, filename) {
  const res = await api(path, { raw: true });
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------- formatting (money arrives as kobo) ----------
export const naira = (kobo, { short = false } = {}) => {
  const n = (kobo || 0) / 100;
  if (short && n >= 1_000_000) return `₦${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}m`;
  if (short && n >= 10_000) return `₦${Math.round(n / 1000)}k`;
  return "₦" + n.toLocaleString("en-NG", { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 });
};
const tz = { timeZone: "Africa/Lagos" };
export const when = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const sameDay = d.toLocaleDateString("en-GB", tz) === now.toLocaleDateString("en-GB", tz);
  const time = d.toLocaleTimeString("en-GB", { ...tz, hour: "2-digit", minute: "2-digit" });
  if (sameDay) return `Today, ${time}`;
  const y = new Date(now.getTime() - 864e5);
  if (d.toLocaleDateString("en-GB", tz) === y.toLocaleDateString("en-GB", tz)) return `Yesterday, ${time}`;
  return d.toLocaleDateString("en-GB", { ...tz, day: "numeric", month: "short" }) + `, ${time}`;
};
export const day = (iso) => (iso ? new Date(iso).toLocaleDateString("en-GB", { ...tz, day: "numeric", month: "short", year: "numeric" }) : "");
export const initials = (s = "") => s.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();

export const STATUS_LABEL = {
  paid: "Paid", part_paid: "Part-paid", unpaid: "Unpaid", expired: "Expired", cancelled: "Cancelled",
  matched: "Matched", unmatched: "Needs assigning", refunded: "Refunded",
};
export const Badge = ({ s }) => <span className={`badge b-${s}`}>{STATUS_LABEL[s] || s}</span>;

// ---------- session ----------
export const Session = createContext(null);
export const useSession = () => useContext(Session);

// ---------- toast ----------
const ToastCtx = createContext(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }) {
  const [msg, setMsg] = useState(null);
  const timer = useRef();
  const show = useCallback((m) => {
    setMsg(m);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setMsg(null), 3200);
  }, []);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      {msg && <div className="toast" role="status">{msg}</div>}
    </ToastCtx.Provider>
  );
}

// ---------- data hook ----------
export function useApi(path, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    if (!path) return;
    api(path).then((d) => live && (setData(d), setError(null))).catch((e) => live && setError(e.message));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tick, ...deps]);
  return { data, error, reload: () => setTick((t) => t + 1), setData };
}

export async function copy(text, toast) {
  try { await navigator.clipboard.writeText(text); toast?.("Copied"); } catch { toast?.("Couldn't copy, select and copy it manually"); }
}
