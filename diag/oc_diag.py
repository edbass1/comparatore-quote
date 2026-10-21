import sys, time, asyncio
from curl_cffi import requests
U = "https://www.oddschecker.com/it/calcio/italia/serie-a"
API = "https://www.oddschecker.com/api/cx/sports-catalogue/v1/grid/3587787560"
print("IP:", requests.get("https://api.ipify.org").text)
for imp in ["chrome", "chrome131", "chrome124", "safari", "safari_ios", "firefox", "edge"]:
    try:
        r = requests.get(U, impersonate=imp, timeout=20, headers={"Accept-Language": "it-IT,it;q=0.9"})
        print(imp, r.status_code, r.headers.get("cf-mitigated"), len(r.text))
    except Exception as e:
        print(imp, "ERR", e)
r = requests.get(API, impersonate="chrome", timeout=20, headers={"accept": "application/json", "content-type": "application/json", "repub": "IT", "country-code": "IT"})
print("API", r.status_code, r.headers.get("cf-mitigated"), r.text[:120])
for u in ["https://www.oddschecker.com/it/", "https://www.oddschecker.com/robots.txt", "https://www.oddschecker.com/football/italy/serie-a"]:
    r = requests.get(u, impersonate="chrome", timeout=20); print(u, r.status_code, r.headers.get("cf-mitigated"))

async def browser(headless):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome", headless=headless, args=["--disable-blink-features=AutomationControlled"])
        ctx = await b.new_context(locale="it-IT", user_agent=None)
        pg = await ctx.new_page()
        await pg.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        resp = await pg.goto(U, wait_until="domcontentloaded", timeout=60000)
        print("browser headless=%s first status" % headless, resp.status if resp else None)
        for i in range(12):
            await pg.wait_for_timeout(2500)
            t = await pg.title()
            if "moment" not in t.lower(): break
        html = await pg.content()
        print("browser headless=%s title=%r len=%d islands=%s" % (headless, await pg.title(), len(html), "cx-island-data" in html))
        await b.close()
for h in (True, False):
    try: asyncio.run(browser(h))
    except Exception as e: print("browser", h, "ERR", e)
