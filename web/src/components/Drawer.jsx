import { useEffect } from "react";
import Icon from "./Icon";

export default function Drawer({ title, sub, onClose, children, footer }) {
  useEffect(() => {
    const k = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : undefined}>
        <div className="drawer-head">
          <div>
            <h2>{title}</h2>
            {sub && <div className="muted small" style={{ marginTop: 2 }}>{sub}</div>}
          </div>
          <button className="x" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>
        <div className="drawer-body">{children}</div>
        {footer && <div className="drawer-foot">{footer}</div>}
      </aside>
    </>
  );
}
