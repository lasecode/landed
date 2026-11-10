"""Live end-to-end test against the deployed site.

Creates the demo accounts, runs every payment scenario through the real API, checks the
security rules, then walks the dashboard in a browser and takes screenshots.
Passwords are generated here and saved only encrypted (results/credentials.enc).
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
import traceback

import httpx

BASE = os.environ.get("BASE_URL", "https://landed-zrx5.onrender.com").rstrip("/")
OUT = "results"
os.makedirs(f"{OUT}/shots", exist_ok=True)
results: list[dict] = []
c = httpx.Client(base_url=BASE, timeout=90)


def check(name):
    def deco(fn):
        t0 = time.time()
        try:
            detail = fn()
            results.append({"test": name, "ok": True, "detail": detail or "", "secs": round(time.time() - t0, 1)})
            print("PASS", name)
        except Exception as e:  # noqa: BLE001
            results.append({"test": name, "ok": False, "detail": f"{type(e).__name__}: {e}",
                            "trace": traceback.format_exc()[-1500:]})
            print("FAIL", name, e)
        return fn
    return deco


def pw() -> str:
    return secrets.token_urlsafe(9) + "7a"


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def ok(r, code=200):
    assert r.status_code == code, f"{r.request.method} {r.request.url.path} -> {r.status_code} {r.text[:300]}"
    return r.json() if "json" in r.headers.get("content-type", "") else r


# ---------- warm up (free plan may be asleep) ----------
for i in range(20):
    try:
        if c.get("/healthz").status_code == 200:
            break
    except httpx.HTTPError:
        pass
    time.sleep(10)

creds = {
    "owner": {"business": "Adaeze Foods (Demo)", "name": "Adaeze Okafor", "phone": "07000000001", "password": pw()},
    "staff": {"business": "Adaeze Foods (Demo)", "name": "Bisi (staff)", "phone": "07000000002", "password": pw()},
    "other": {"business": "Scoping Test Co", "name": "Test B", "phone": "07000000003", "password": pw()},
}
S: dict = {}


def signup_or_fail(k):
    u = creds[k]
    r = c.post("/api/auth/signup", json={"business_name": u["business"], "owner_name": u["name"], "phone": u["phone"],
                                         "password": u["password"]})
    if r.status_code == 409:
        raise AssertionError(f"{u['phone']} already registered (test was run before)")
    return ok(r)["token"]


@check("Site is up and serves the dashboard")
def _():
    ok(c.get("/healthz"))
    r = c.get("/")
    assert r.status_code == 200 and '<div id="root">' in r.text
    r = c.get("/invoices")  # SPA route
    assert r.status_code == 200 and '<div id="root">' in r.text


@check("Create demo owner account")
def _():
    S["owner"] = signup_or_fail("owner")
    me = ok(c.get("/api/auth/me", headers=H(S["owner"])))
    assert me["merchant"]["provider"] == "mock"


@check("Create staff login and sign in as staff")
def _():
    u = creds["staff"]
    ok(c.post("/api/settings/staff", headers=H(S["owner"]), json={"name": u["name"], "phone": u["phone"], "password": u["password"]}))
    S["staff"] = ok(c.post("/api/auth/login", json={"phone": u["phone"], "password": u["password"]}))["token"]


@check("Create second business (for data isolation test)")
def _():
    S["other"] = signup_or_fail("other")


@check("Business settings save")
def _():
    m = ok(c.patch("/api/settings/business", headers=H(S["owner"]), json={"reminder_days": "2,5", "invoice_expiry_days": 7}))
    assert m["reminder_days"] == "2,5"


def new_invoice(name, amount, desc, tok=None):
    return ok(c.post("/api/invoices", headers=H(tok or S["owner"]), json={"customer_name": name, "amount_naira": amount, "description": desc}))


def transfer(acct, amount, payer, tok=None):
    r = ok(c.post("/api/dev/simulate-payment", headers=H(tok or S["owner"]), json={"account_number": acct, "amount_naira": amount, "payer_name": payer}))
    assert r["webhook_status"] == 200, r


def inv(iid, tok=None):
    return ok(c.get(f"/api/invoices/{iid}", headers=H(tok or S["owner"])))


def wait_for(fn, cond, secs=20):
    end = time.time() + secs
    while True:
        v = fn()
        if cond(v) or time.time() > end:
            return v
        time.sleep(1)


@check("Exact payment marks invoice paid")
def _():
    i = new_invoice("Tunde Bello", 45000, "3 bags of rice")
    assert i["account"]["account_number"] and i["status"] == "unpaid"
    transfer(i["account"]["account_number"], 45000, "TUNDE BELLO")
    d = wait_for(lambda: inv(i["id"]), lambda d: d["status"] == "paid")
    assert d["status"] == "paid" and d["balance"] == 0, d["status"]
    S["paid_invoice"] = d
    return f"{d['ref']} paid via account {d['account']['account_number']}"


@check("Underpayment → part-paid, then second payment → paid")
def _():
    i = new_invoice("Kemi Adebayo", 30000, "Hair, 2 bundles")
    a = i["account"]["account_number"]
    transfer(a, 20000, "KEMI ADEBAYO")
    d = wait_for(lambda: inv(i["id"]), lambda d: d["status"] == "part_paid")
    assert d["status"] == "part_paid" and d["balance"] == 1_000_000
    transfer(a, 10000, "KEMI ADEBAYO")
    d = wait_for(lambda: inv(i["id"]), lambda d: d["status"] == "paid")
    assert d["status"] == "paid" and len(d["payments"]) == 2


@check("Overpayment flagged, kept as credit, credit used on next invoice")
def _():
    i = new_invoice("Chinedu Eze", 20000, "Phone screen")
    transfer(i["account"]["account_number"], 25000, "CHINEDU EZE")
    d = wait_for(lambda: inv(i["id"]), lambda d: d["excess_status"] == "pending")
    assert d["status"] == "paid" and d["excess_amount"] == 500_000
    ok(c.post(f"/api/invoices/{i['id']}/excess", headers=H(S["owner"]), json={"decision": "credit"}))
    j = new_invoice("Chinedu Eze", 12000, "Screen guard + case")
    assert j["amount_paid"] == 500_000 and j["balance"] == 700_000, j
    S["open_invoice"] = j


@check("Static account: oldest invoice first, remainder held as credit")
def _():
    cu = ok(c.post("/api/customers", headers=H(S["owner"]), json={"name": "Mama Ngozi Stores"}))
    cu = ok(c.post(f"/api/customers/{cu['id']}/static-account", headers=H(S["owner"]), json={}))
    acct = cu["static_account"]["account_number"]
    a = ok(c.post("/api/invoices", headers=H(S["owner"]), json={"customer_id": cu["id"], "amount_naira": 50000, "description": "5 cartons noodles"}))
    b = ok(c.post("/api/invoices", headers=H(S["owner"]), json={"customer_id": cu["id"], "amount_naira": 40000, "description": "2 kegs groundnut oil"}))
    assert a["account"]["account_number"] == acct == b["account"]["account_number"]
    transfer(acct, 70000, "NGOZI OKAFOR")
    wait_for(lambda: inv(b["id"]), lambda d: d["status"] == "part_paid")
    assert inv(a["id"])["status"] == "paid" and inv(b["id"])["balance"] == 2_000_000


@check("Payment to unknown account → unmatched → assigned to invoice")
def _():
    i = new_invoice("Grace Okon", 15000, "Food trays x3")
    transfer("9000000001", 15000, "GRACE OKON")
    um = wait_for(lambda: ok(c.get("/api/payments?status=unmatched", headers=H(S["owner"]))), lambda u: len(u) >= 1)
    assert len(um) == 1 and um[0]["unmatched_reason"] == "Account not recognised"
    ok(c.post(f"/api/payments/{um[0]['id']}/assign", headers=H(S["owner"]), json={"invoice_id": i["id"]}))
    assert inv(i["id"])["status"] == "paid"


@check("Cancelled invoice: later payment goes to unmatched")
def _():
    i = new_invoice("Walk-in customer", 8000, "Drinks")
    ok(c.post(f"/api/invoices/{i['id']}/cancel", headers=H(S["owner"])))
    transfer(i["account"]["account_number"], 8000, "UNKNOWN SENDER")
    um = wait_for(lambda: ok(c.get("/api/payments?status=unmatched", headers=H(S["owner"]))), lambda u: len(u) >= 1)
    assert any("cancelled" in (p["unmatched_reason"] or "") for p in um)


@check("Webhook with bad signature is rejected")
def _():
    st = ok(c.get("/api/settings", headers=H(S["owner"])))
    path = "/" + st["provider"]["webhook_url"].split("/", 3)[3]
    r = c.post(path, content=b'{"event":"payment.received","reference":"FAKE"}', headers={"x-mock-signature": "deadbeef"})
    assert r.status_code == 401, r.status_code
    r = c.post(path, content=b'{"event":"payment.received","reference":"FAKE"}')
    assert r.status_code == 401


@check("Businesses can't see each other's data")
def _():
    pid = S["paid_invoice"]["id"]
    assert c.get(f"/api/invoices/{pid}", headers=H(S["other"])).status_code == 404
    assert ok(c.get("/api/invoices", headers=H(S["other"]))) == []
    assert ok(c.get("/api/customers", headers=H(S["other"]))) == []


@check("Staff can invoice but not open settings or export")
def _():
    ok(c.get("/api/invoices", headers=H(S["staff"])))
    assert c.get("/api/settings", headers=H(S["staff"])).status_code == 403
    assert c.get("/api/reports/export.csv", headers=H(S["staff"])).status_code == 403


@check("Login rejects wrong password; API rejects no token")
def _():
    assert c.post("/api/auth/login", json={"phone": "07000000001", "password": "wrong-password"}).status_code == 401
    assert c.get("/api/invoices").status_code == 401


@check("Reports and CSV export")
def _():
    r = ok(c.get("/api/reports", headers=H(S["owner"])))
    assert r["received"] > 0 and r["invoices"] >= 7
    csv = c.get("/api/reports/export.csv?kind=payments", headers=H(S["owner"]))
    assert csv.status_code == 200 and "TUNDE BELLO" in csv.text
    return f"received ₦{r['received'] // 100:,} across {r['payments']} payments"


@check("WhatsApp bot commands")
def _():
    r = ok(c.post("/api/dev/bot", headers=H(S["owner"]), json={"text": "today"}))["reply"]
    assert "Today so far" in r
    r = ok(c.post("/api/dev/bot", headers=H(S["owner"]), json={"text": "invoice Tunde 18000 for 1 bag of beans"}))["reply"]
    assert "created for Tunde Bello" in r, r
    r = ok(c.post("/api/dev/bot", headers=H(S["owner"]), json={"text": "unpaid"}))["reply"]
    assert "Unpaid invoices" in r


@check("Customer receipt page")
def _():
    url = S["paid_invoice"]["receipt_url"]
    r = c.get("/" + url.split("/", 3)[3])
    assert r.status_code == 200 and "Receipt" in r.text and "Adaeze Foods" in r.text
    assert url.startswith(BASE), f"receipt link points at {url}"


@check("Leave a payment waiting in Unmatched for demos")
def _():
    transfer("9000000777", 12500, "IBRAHIM MUSA")


# ---------- browser walkthrough ----------
@check("Dashboard in a browser (desktop + phone)")
def _():
    from playwright.sync_api import sync_playwright

    errors = []
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": 1360, "height": 860})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(BASE + "/")
        pg.fill("#ph", creds["owner"]["phone"]); pg.fill("#pw", creds["owner"]["password"])
        pg.get_by_role("button", name="Sign in").click()
        pg.get_by_text("Latest payments").wait_for(timeout=30000)
        pg.wait_for_timeout(1500); pg.screenshot(path=f"{OUT}/shots/01-overview.png", full_page=True)
        pg.goto(BASE + "/invoices"); pg.wait_for_timeout(1500); pg.screenshot(path=f"{OUT}/shots/02-invoices.png")
        pg.locator("tbody tr").first.click(); pg.get_by_text("History").wait_for(); pg.wait_for_timeout(600)
        pg.screenshot(path=f"{OUT}/shots/03-invoice-drawer.png"); pg.keyboard.press("Escape")
        # create one invoice through the UI and pay it
        pg.get_by_role("button", name="New invoice").first.click()
        pg.fill("#cust", "Femi Ade"); pg.fill("#amt", "22,500"); pg.fill("#desc", "Wig install")
        pg.get_by_role("button", name="Create invoice").click(); pg.get_by_text("Account for this invoice").wait_for(timeout=20000)
        pg.get_by_role("button", name="Send transfer").click()
        pg.get_by_text("Fully paid").wait_for(timeout=25000)
        pg.screenshot(path=f"{OUT}/shots/04-ui-invoice-paid.png"); pg.keyboard.press("Escape")
        for path, name in [("/customers", "05-customers"), ("/unmatched", "06-unmatched"), ("/reports", "07-reports"),
                           ("/settings", "08-settings")]:
            pg.goto(BASE + path); pg.wait_for_timeout(1500); pg.screenshot(path=f"{OUT}/shots/{name}.png", full_page=True)
        pg.goto(BASE + "/bot"); pg.get_by_role("button", name="today", exact=True).click(); pg.wait_for_timeout(2500)
        pg.screenshot(path=f"{OUT}/shots/09-bot.png")
        tok = pg.evaluate("localStorage.getItem('landed.token')")
        m = br.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        m.goto(BASE + "/"); m.evaluate("t=>localStorage.setItem('landed.token',t)", tok); m.goto(BASE + "/")
        m.get_by_text("Latest payments").wait_for(timeout=30000); m.wait_for_timeout(1200)
        m.screenshot(path=f"{OUT}/shots/10-phone-overview.png", full_page=True)
        sw = m.evaluate("document.documentElement.scrollWidth")
        br.close()
    assert sw <= 392, f"phone layout scrolls sideways ({sw}px)"
    assert not errors, errors
    return "UI invoice created and paid; 10 screenshots"


# ---------- save ----------
with open(f"{OUT}/report.json", "w") as f:
    json.dump({"base": BASE, "passed": sum(r["ok"] for r in results), "total": len(results), "results": results}, f, indent=2)
raw = json.dumps({k: [v["phone"], v["password"]] for k, v in creds.items()}).encode()
subprocess.run(["openssl", "pkeyutl", "-encrypt", "-pubin", "-inkey", "e2e/results-key.pub.pem",
                "-pkeyopt", "rsa_padding_mode:oaep", "-out", f"{OUT}/credentials.enc"], input=raw, check=True)
print(f"\n{sum(r['ok'] for r in results)}/{len(results)} passed")
sys.exit(0)
