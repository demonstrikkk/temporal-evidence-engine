import asyncio
import json
import os
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.async_api import async_playwright

BASE = os.environ.get("DG_BASE_URL", "http://127.0.0.1:5173")
API = os.environ.get("DG_API_URL", "http://127.0.0.1:8000")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "screenshots")
os.makedirs(OUT, exist_ok=True)

with urllib.request.urlopen(f"{API}/api/datasets", timeout=60) as r:
    items = json.loads(r.read().decode("utf-8"))["data"] or []
legal = [d for d in items if "legal corpus" in (d.get("name") or "").lower()]
if not legal:
    raise SystemExit("no legal corpus dataset found")
DS = max(legal, key=lambda d: d.get("record_count") or 0)["id"]
print("dataset:", DS)

OFFLINE_FONTS = re.compile(r"fonts\.(googleapis|gstatic)\.com")


async def main():
    fails = []
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        p = await b.new_page(viewport={"width": 1600, "height": 1000})
        p.on("pageerror", lambda e: (print("PAGEERROR:", e), fails.append(str(e))))
        p.on("console", lambda m: (print("CONSOLE:", m.type, m.text)
                                   if m.type in ("error", "warning") else None))
        p.on("requestfailed", lambda r: print("REQFAILED:", r.url, r.failure))
        p.on("response", lambda r: print("HTTP:", r.status, r.url)
              if "/api/datasets" in r.url else None)
        await p.route(re.compile(r"fonts\.(googleapis|gstatic)\.com"),
                      lambda route: route.abort())

        await p.goto(f"{BASE}/library/{DS}", wait_until="networkidle")
        await p.wait_for_selector("table tbody tr", timeout=45000)
        await p.wait_for_timeout(1200)
        print("dataset page      : OK")

        # The Buckets tab.
        await p.locator('[role=tablist][aria-label="Dataset section"] [role=tab]',
                        has_text=re.compile("^Buckets$")).first.click()
        # Wait for the section to actually render, not for a fixed delay: the
        # first read of a dataset's records crosses a remote pooler, and a
        # timeout-based check reports "the tab is broken" for what is really
        # "the read was still in flight".
        try:
            await p.wait_for_function(
                "() => /Local/.test(document.querySelector('main')?.innerText || '')",
                timeout=30000)
        except Exception:
            pass
        await p.wait_for_timeout(500)
        body = await p.inner_text("main")
        for want in ("Local", "Regional", "National"):
            print(f"bucket {want:<9}:", want in body)
            if want not in body:
                fails.append(f"bucket {want} missing")
        await p.screenshot(path=os.path.join(OUT, "legal-buckets.png"))

        # A record, then its evidence chain.
        await p.locator('[role=tablist][aria-label="Dataset section"] [role=tab]',
                        has_text=re.compile("^Records$")).first.click()
        await p.wait_for_selector("table tbody tr", timeout=30000)
        await p.wait_for_timeout(800)
        await p.click("table tbody tr")
        await p.wait_for_timeout(1200)
        panel = await p.inner_text('aside[aria-label="Inspector"]')
        print("inspector opened  :", bool(panel))
        print("  mentions a page :", "page" in panel.lower())
        print("  mentions status :", "verified" in panel.lower())
        # The quote is the evidence; show it so a human can read it.
        quoted = await p.locator('aside[aria-label="Inspector"] blockquote').count()
        print("  quote blocks    :", quoted)
        if quoted:
            print("  quote text      :", (await p.locator(
                'aside[aria-label="Inspector"] blockquote').first.inner_text())[:120])
        print("--- inspector text ---")
        print(panel[:600])
        print("--- end ---")

        # Expand a field to reveal its quote and the stored page.
        expander = p.locator('aside[aria-label="Inspector"] button[aria-expanded]').first
        await expander.click()
        await p.wait_for_timeout(1200)
        after = await p.inner_text('aside[aria-label="Inspector"]')
        print("  after expanding :", "quote" in after.lower()
              or "“" in after or '"' in after)
        print("  shows a page ref:", "page" in after.lower())
        print("  shows offsets   :", "offset" in after.lower()
              or bool(re.search(r"\d+\s*[-–]\s*\d+", after)))
        await p.screenshot(path=os.path.join(OUT, "legal-record.png"))

        # The tier column, which is the corpus's own derived field.
        await p.keyboard.press("Escape")
        await p.wait_for_timeout(600)
        text = await p.inner_text("main")
        print("tier visible      :", "Tier" in text or "tier" in text)

        await b.close()
    print("FAILS:", fails or "none")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
