// Toasts, drawer and list refresh for the HTMX dashboard.
(function () {
  let timer;
  function toast(msg) {
    let el = document.getElementById("toast");
    if (!el) { el = document.createElement("div"); el.id = "toast"; el.className = "toast"; el.setAttribute("role", "status"); document.body.appendChild(el); }
    el.textContent = msg; el.hidden = false;
    clearTimeout(timer); timer = setTimeout(() => { el.hidden = true; }, 3400);
  }
  function closeDrawer() { const d = document.getElementById("drawer"); if (d) d.innerHTML = ""; }
  window.landed = { toast, closeDrawer };
  document.addEventListener("toast", (e) => toast(e.detail.value || e.detail));
  document.addEventListener("closeDrawer", closeDrawer);
  document.addEventListener("refresh", () => {
    const page = document.getElementById("page");
    if (page && window.htmx) htmx.ajax("GET", location.href, { target: "#page", select: "#page", swap: "outerHTML" });
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-close]")) closeDrawer();
    const c = e.target.closest("[data-copy]");
    if (c) { navigator.clipboard?.writeText(c.dataset.copy).then(() => toast("Copied"), () => toast("Couldn't copy")); }
    const m = e.target.closest("[data-menu]");
    if (m) document.querySelector(".side")?.classList.toggle("open");
  });
  document.body?.addEventListener?.("htmx:responseError", () => toast("Something went wrong. Please try again."));
  document.addEventListener("htmx:sendError", () => toast("Can't reach the server. Check your connection."));
})();
