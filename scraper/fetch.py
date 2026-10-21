#!/usr/bin/env python3
"""Scarica le quote calcio (bookmaker italiani ADM) da Oddschecker Italia e Oddspedia Italia
e scrive JSON statici in <out>/data/<fonte>/.

Nessuna quota viene inventata: se una fonte non risponde viene marcata come "non disponibile"
(o, se presente, si riusano i dati dell'esecuzione precedente segnalandoli come NON aggiornati).
"""
import argparse, html, json, os, re, subprocess, sys, tempfile, time, traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from curl_cffi import requests as creq

NOW = datetime.now(timezone.utc)
GIORNI_AVANTI = 6          # partite entro i prossimi N giorni
MAX_PER_LEGA = 14          # limite di sicurezza per lega
PAGES_URL = os.environ.get("PAGES_URL", "https://edbass1.github.io/comparatore-quote")
STALE_MAX_ORE = 6          # oltre questo i dati vecchi non si mostrano più

def log(*a):
    print(*a, file=sys.stderr, flush=True)

def iso(dt): return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def parse_dt(s):
    s = s.strip().replace(" ", "T")
    if s.endswith("+00"): s += ":00"
    if s.endswith("Z"): s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)

def in_finestra(dt):
    return NOW - timedelta(minutes=5) < dt < NOW + timedelta(days=GIORNI_AVANTI)

def session():
    s = creq.Session(impersonate="chrome")
    s.headers.update({"Accept-Language": "it-IT,it;q=0.9"})
    return s

def get(s, url, tries=3, **kw):
    last = None
    for i in range(tries):
        try:
            r = s.get(url, timeout=40, **kw)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
            if r.status_code in (403, 404, 400):
                break
        except Exception as e:
            last = str(e)
        time.sleep(2 + 3 * i)
    raise RuntimeError(f"{url}: {last}")

# --------------------------------------------------------------------------------------
# Traduzioni
# --------------------------------------------------------------------------------------
MERCATI_EN_IT = {
    "Asian Total": "Totale Gol Asiatico",
    "Late Goal": "Gol nel finale (dopo il 75')",
    "Total Away Shots": "Tiri totali squadra ospite",
    "Total Home Shots": "Tiri totali squadra casa",
    "Player Passes": "Passaggi giocatore",
    "Double Chance And Total Goals": "Doppia chance e Totale gol",
}
def tr_mercato(n):
    n = (n or "").strip()
    if n in MERCATI_EN_IT: return MERCATI_EN_IT[n]
    m = re.match(r"Both Teams To Score And Over/Under ([\d.]+)", n)
    if m: return f"Gol/No Gol e Under/Over {m.group(1)}"
    return n

def tr_sel(n, home="", away=""):
    t = n.strip()
    rep = [(r"\bScore Draw\b", "Pareggio con gol"), (r"\bNo Card\b", "Nessun cartellino"), (r"\bDraw\b", "X"), (r"^Yes$", "Sì"), (r"^No$", "No"), (r"\bOdd BK\b", "Dispari"), (r"^Odd$", "Dispari"),
           (r"\bEven\b", "Pari"), (r"\bNo Goals?\b", "Nessun gol"), (r"\bNo Goalscorer\b", "Nessun marcatore"),
           (r"\bExactly\b", "Esattamente"), (r" and Yes\b", " e Gol"),
           (r" and No\b", " e No Gol"), (r"\bto win by (\d+) Goals?\b", r"vince con \1 gol di scarto"),
           (r"\bto win by (\d+)\+ Goals\b", r"vince con \1+ gol di scarto"),
           (r"\bTotal Goals (\d+) Or More\b", r"\1 o più gol"), (r"\bGoal after 74:59\b", "Gol dopo il 75'"),
           (r"\bNo Goal after 74:59\b", "Nessun gol dopo il 75'"), (r"\bNo Goal\b", "Nessun gol"),
           (r"\bOver\b", "Over"), (r"\bUnder\b", "Under")]
    for a, b in rep:
        t = re.sub(a, b, t)
    return t

# --------------------------------------------------------------------------------------
# ODDSCHECKER ITALIA  (https://www.oddschecker.com/it/)
# I dati sono dentro la pagina (Astro "islands"): coupon di lega + griglie quote per ogni mercato.
# --------------------------------------------------------------------------------------
OC = "https://www.oddschecker.com"
# eventId Oddschecker delle competizioni (dalle pagine /it/calcio/...; stabili tra le stagioni)
OC_LEGHE = [
    ("Serie A", 9950, "/it/calcio/italia/serie-a"),
    ("Serie B", 9952, "/it/calcio/italia/serie-b"),
    ("Champions League", 218309, "/it/calcio/coppe-e-amichevoli-per-club/champions-league"),
    ("Europa League", 10545, "/it/calcio/coppe-e-amichevoli-per-club/europa-league"),
    ("Conference League", 38925767, "/it/calcio/coppe-e-amichevoli-per-club/conference-league"),
    ("Premier League", 2457, "/it/calcio/inghilterra/premier-league"),
    ("LaLiga", 9945, "/it/calcio/spagna/liga"),
    ("Bundesliga", 9935, "/it/calcio/germania/bundesliga"),
    ("Ligue 1", 9941, "/it/calcio/francia/ligue-1"),
    ("Eredivisie", 9943, "/it/calcio/olanda/eredivisie"),
]
OC_API = OC + "/api/cx/sports-catalogue"
OC_HDR = {"accept": "application/json", "content-type": "application/json", "repub": "IT", "country-code": "IT",
          "referer": OC + "/it/calcio"}
# nomi italiani dei mercati e bookmaker italiani (estratti dalle pagine di Oddschecker Italia)
_META = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "oddschecker_meta.json")))
OC_MERCATI = {int(k): v for k, v in _META["mercati"].items() if v.get("c") != "PLAYER_PROP"}
OC_BK = _META["bookmaker"]
OC_CHUNK = 12

def astro_de(v):
    if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int):
        t, x = v
        if t == 0: return {k: astro_de(y) for k, y in x.items()} if isinstance(x, dict) else x
        if t == 1: return [astro_de(i) for i in x]
        return x
    if isinstance(v, dict): return {k: astro_de(y) for k, y in v.items()}
    return v

def astro_props(page, export):
    m = re.search(r'component-export="%s"[^>]*?props="([^"]*)"' % re.escape(export), page)
    if not m: return None
    return astro_de(json.loads(html.unescape(m.group(1))))

def script_json(page, sid):
    m = re.search(r'<script id="%s" type="application/json">(.*?)</script>' % sid, page, re.S)
    return json.loads(m.group(1)) if m else None

def oc_norm_sels(name, sels, hn, an):
    """Etichette coerenti (1/X/2, 1X/12/X2, risultati esatti casa-ospite) e ordine logico."""
    nl = name.lower()
    T = {hn: "1", an: "2", "X": "X"}
    labs = [x["n"] for x in sels]
    if "handicap" in nl and any(x.get("l") for x in sels):
        # linea dal punto di vista della squadra di casa, così 1 / X / 2 della stessa linea stanno insieme
        for x in sels:
            orig = x.get("l")
            if not orig: continue
            try: v = float(orig.replace("+", ""))
            except ValueError: continue
            hv = v if x["n"] == hn else -v
            x["n"] = {hn: "1 · " + hn, an: "2 · " + an}.get(x["n"], x["n"]) + f" ({orig})"
            x["l"] = f"Casa {hv:+g}".replace("+0", "0") if hv != 0 else "Casa 0"
        sels.sort(key=lambda x: (x.get("l") or "", "1X2".find(x["n"][0]) if x["n"][0] in "1X2" else 9))
        return
    def score_key(lab):
        m = re.match(r"(\d+)-(\d+)", lab)
        return (int(m.group(1)) + int(m.group(2)), int(m.group(1)), int(m.group(2))) if m else (99, 0, 0)
    if "doppia chance" in nl and all("-" in l for l in labs):
        mp = {}
        for x in sels:
            a, _, b = x["n"].partition("-")
            k = "".join(sorted([T.get(a.strip(), "?"), T.get(b.strip(), "?")], key=lambda c: "1X2".find(c)))
            mp[x["n"]] = k
        if all("?" not in v for v in mp.values()):
            for x in sels: x["n"] = mp[x["n"]]
            sels.sort(key=lambda x: ["1X", "12", "X2"].index(x["n"]) if x["n"] in ("1X", "12", "X2") else 9)
        return
    if "parziale" in nl and all("/" in l for l in labs):
        ok = True
        for x in sels:
            a, _, b = x["n"].partition("/")
            if a.strip() in T and b.strip() in T: x["n"] = f"{T[a.strip()]}/{T[b.strip()]}"
            else: ok = False
        if ok:
            order = ["1/1", "1/X", "1/2", "X/1", "X/X", "X/2", "2/1", "2/X", "2/2"]
            sels.sort(key=lambda x: order.index(x["n"]) if x["n"] in order else 99)
        return
    if "risultato esatto" in nl:
        for x in sels:
            who, sc = (x["n"], x.pop("l")) if x.get("l") else (x["n"].rsplit(" ", 1) + [""])[:2]
            m = re.match(r"(\d+)-(\d+)$", (sc or "").strip())
            if not m: continue
            a, b = int(m.group(1)), int(m.group(2))
            if who.strip() == an: a, b = min(a, b), max(a, b)
            elif who.strip() == hn: a, b = max(a, b), min(a, b)
            x["n"] = f"{a}-{b}"
        sels.sort(key=lambda x: score_key(x["n"]))
        return
    if len(sels) == 3 and set(labs) == {hn, "X", an}:
        for x in sels: x["n"] = {hn: f"1 · {hn}", "X": "X", an: f"2 · {an}"}[x["n"]]
        sels.sort(key=lambda x: "1X2".find(x["n"][0]))
    elif len(sels) == 2 and set(labs) == {hn, an}:
        sels.sort(key=lambda x: 0 if x["n"] == hn else 1)
    elif set(labs) <= {"Sì", "No"}:
        sels.sort(key=lambda x: 0 if x["n"] == "Sì" else 1)

def oc_lega(s, comp, event_id, path):
    """Usa l'API JSON pubblica che la pagina di lega di Oddschecker Italia chiama nel browser
    (/api/cx/sports-catalogue/v1/subevent-group), a blocchi di tipi di scommessa."""
    bts = sorted(OC_MERCATI, key=lambda b: OC_MERCATI[b].get("p", 9999))
    subs = {}
    for i in range(0, len(bts), OC_CHUNK):
        q = f"eventId={event_id}&betLimit=200&subeventLimit=60&" + "&".join(f"bettypeIds={b}" for b in bts[i:i + OC_CHUNK])
        try:
            r = s.get(f"{OC_API}/v1/subevent-group?{q}", headers=OC_HDR, timeout=60)
        except Exception as e:
            if i == 0: raise
            log(f"[oddschecker] {comp} blocco {i}: {e}"); continue
        if r.status_code == 404 and i > 0: continue
        if r.status_code != 200:
            if i == 0: raise RuntimeError(f"HTTP {r.status_code}")
            log(f"[oddschecker] {comp} blocco {i}: HTTP {r.status_code}"); continue
        for se in (r.json() or {}).get("subevents") or []:
            if se.get("type") != "MATCH" or se.get("inRunning"): continue
            if not in_finestra(parse_dt(se["startTime"])): continue
            cur = subs.setdefault(se["id"], dict(se, markets=[]))
            cur["markets"] += se.get("markets") or []
        time.sleep(0.4)
    out = []
    for se in sorted(subs.values(), key=lambda x: x["startTime"])[:MAX_PER_LEGA]:
        p = oc_build(comp, se)
        if p["mercati"]: out.append(p)
    return out

def oc_build(comp, se):
    hn, an = (se["name"].split(" v ") + [""])[:2]
    hn, an = hn.strip(), an.strip()
    markets, seen = [], set()
    for mk in se["markets"]:
        if mk["id"] in seen: continue
        seen.add(mk["id"])
        meta = OC_MERCATI.get(mk.get("betTypeId"), {})
        name = meta.get("n") or mk.get("name") or "Mercato"
        sels = []
        for b in mk.get("bets") or []:
            o = {}
            for x in b.get("odds") or []:
                if x.get("status") not in (None, "ACTIVE"): continue
                dec = x.get("decimal")
                if not dec or dec <= 1: continue
                o[x["bookmakerCode"]] = round(float(dec), 2)
            if not o: continue
            sel = {"n": tr_sel(b["name"]), "o": o}
            line = (b.get("line") or {}).get("name")
            if line: sel["l"] = line
            sels.append(sel)
        if not sels: continue
        oc_norm_sels(name, sels, hn, an)
        markets.append({"n": tr_mercato(name), "g": meta.get("g", "Altri"), "p": meta.get("p", 9999), "s": sels})
    markets.sort(key=lambda m: (m["p"], m["n"]))
    for m in markets: m.pop("p", None)
    used = {c for m in markets for s_ in m["s"] for c in s_["o"]}
    return {
        "id": f"oc-{se['id']}", "fonte": "oddschecker", "comp": comp, "casa": hn, "ospite": an,
        "inizio": iso(parse_dt(se["startTime"])), "url": OC + (se.get("urlPath") or "/it/calcio"),
        "bk": {c: {"n": OC_BK.get(c, {}).get("n", c), "c": OC_BK.get(c, {}).get("c")} for c in sorted(used)},
        "mercati": markets,
    }

def scrape_oddschecker():
    s = session()
    partite, errori = [], []
    for comp, ev, path in OC_LEGHE:
        try:
            ps = oc_lega(s, comp, ev, path)
            log(f"[oddschecker] {comp}: {len(ps)} partite")
            partite += ps
        except Exception as e:
            errori.append(f"{comp}: {e}"); log(f"[oddschecker] ERRORE {comp}: {e}")
        time.sleep(0.5)
    return partite, errori

# --------------------------------------------------------------------------------------
# ODDSPEDIA ITALIA  (https://oddspedia.com/it/)
# Lista partite: stato Nuxt della pagina di lega.  Quote: API JSON pubblica usata dal sito
# (getMatchOdds) con bookmakerGeoCode=IT => solo bookmaker mostrati agli utenti italiani.
# --------------------------------------------------------------------------------------
OP = "https://oddspedia.com"
OP_LEGHE = [
    ("Serie A", "/it/calcio/italia/serie-a"),
    ("Serie B", "/it/calcio/italia/serie-b"),
    ("Coppa Italia", "/it/calcio/italia/coppa-italia"),
    ("Champions League", "/it/calcio/europa/champions-league"),
    ("Europa League", "/it/calcio/europa/europa-league"),
    ("Conference League", "/it/calcio/europa/uefa-conference-league"),
    ("Premier League", "/it/calcio/inghilterra/premier-league"),
    ("LaLiga", "/it/calcio/spagna/primera-division"),
    ("Bundesliga", "/it/calcio/germania/bundesliga"),
    ("Ligue 1", "/it/calcio/francia/ligue-1"),
]
# id gruppo mercato Oddspedia, in ordine di importanza
OP_GRUPPI = [1, 7, 4, 11, 8, 9, 5, 6, 3, 10, 12]

def nuxt_state(page):
    i = page.find("window.__NUXT__=")
    if i < 0: return None
    j = page.find("</script>", i)
    fd, p = tempfile.mkstemp(suffix=".js"); out = p + ".json"
    with os.fdopen(fd, "w") as f:
        f.write("var window={};" + page[i:j] + ";require('fs').writeFileSync(%s,JSON.stringify(window.__NUXT__));" % json.dumps(out))
    try:
        subprocess.run(["node", p], check=True, timeout=60, capture_output=True)
        return json.load(open(out))
    finally:
        for x in (p, out):
            try: os.remove(x)
            except OSError: pass

def op_partite_lega(s, nome, path):
    st = nuxt_state(get(s, OP + path).text) or {}
    ms = []
    for d in st.get("data") or []:
        if isinstance(d, dict): ms += d.get("matches") or []
    out = []
    for m in ms:
        dt = parse_dt(m["md"])
        if m.get("inplay") or m.get("matchstatus") not in (1, None) or not in_finestra(dt): continue
        out.append(m)
    out.sort(key=lambda m: m["md"])
    return out[:MAX_PER_LEGA]

def op_api(s, q):
    r = get(s, OP + "/api/v1/" + q, headers={"accept": "application/json", "referer": OP + "/it/calcio"})
    return r.json().get("data") or {}

def op_dec(v):
    try:
        f = float(v)
        return round(f, 2) if f > 1 else None
    except (TypeError, ValueError):
        return None

def op_partita(s, comp, m):
    home, away = m["ht"], m["at"]
    base = f"getMatchOdds?matchId={m['id']}&geoCode=IT&geoState=&bookmakerGeoCode=IT&bookmakerGeoState=&wettsteuer=0&inplay=0&language=it"
    bk, markets = {}, []
    def nomi_sel(names):
        r = []
        for i, n in enumerate(names):
            n = (n or "").strip()
            if n == "Casa": n = f"1 · {home}" if len(names) == 3 else home
            elif n == "Ospite": n = f"2 · {away}" if len(names) == 3 else away
            elif n == "Si": n = "Sì"
            elif n == "Più": n = "Over"
            elif n == "Meno": n = "Under"
            r.append(n)
        return r
    for gid in OP_GRUPPI:
        try:
            d = op_api(s, base + f"&oddGroupId={gid}")
        except Exception as e:
            if gid == 1: raise
            log(f"[oddspedia] gruppo {gid} {home}-{away}: {e}"); continue
        time.sleep(0.25)
        for mk in d.get("prematch", []):
            if mk.get("id") != gid or not mk.get("periods"): continue
            for per in mk["periods"]:
                nome = mk["name"] if per.get("name") in (None, "", "Finale") else f"{mk['name']} – {per['name']}"
                if nome.startswith("Draw No Bet"): nome = nome.replace("Draw No Bet", "Rimborso in caso di pareggio (DNB)")
                odds = per.get("odds")
                sels = []
                def add_row(row, names, line=None, label_override=None):
                    bid = str(row["bid"]); bk[bid] = {"n": row.get("bookie_name"), "c": row.get("bookie_primary_color")}
                    for i, nm in enumerate(names):
                        v = op_dec(row.get(f"o{i+1}"))
                        if v is None: continue
                        lab = label_override or nm
                        key = (lab, line)
                        sel = next((x for x in sels if (x["n"], x.get("l")) == key), None)
                        if not sel:
                            sel = {"n": lab, "o": {}}
                            if line: sel["l"] = line
                            sels.append(sel)
                        sel["o"][bid] = v
                if isinstance(odds, list):
                    names = nomi_sel(mk.get("oddsnames") or [])
                    for row in odds:
                        if row.get("status") not in (None, 3, 1): pass
                        add_row(row, names)
                elif isinstance(odds, dict):
                    names = nomi_sel(mk.get("oddsnames") or [])
                    for kind in ("main", "alternative"):
                        for alt in odds.get(kind) or []:
                            ln = (alt.get("name") or "").strip()
                            for row in (alt.get("odds") or {}).values():
                                if mk.get("waytype") == 1:   # es. risultato esatto, parziale/finale
                                    lab = ln.replace(" : ", "-")
                                    if "/" in lab:
                                        lab = "/".join({"Casa": "1", "Pareggio": "X", "Ospite": "2"}.get(t.strip(), t.strip()) for t in lab.split("/"))
                                    add_row(row, [ln], label_override=lab)
                                else:
                                    add_row(row, names, line=ln)
                if sels:
                    markets.append({"n": nome, "g": "Oddspedia", "s": sels})
    return {
        "id": f"op-{m['id']}", "fonte": "oddspedia", "comp": comp, "casa": home, "ospite": away,
        "inizio": iso(parse_dt(m["md"])), "url": f"{OP}/it/calcio/{m['ht_slug']}-{m['at_slug']}-{m['match_key']}",
        "bk": bk, "mercati": markets,
    }

def scrape_oddspedia():
    s = session()
    get(s, OP + "/it/calcio")
    jobs, errori = [], []
    for nome, path in OP_LEGHE:
        try:
            ms = op_partite_lega(s, nome, path)
            log(f"[oddspedia] {nome}: {len(ms)} partite")
            jobs += [(nome, m) for m in ms]
        except Exception as e:
            errori.append(f"{nome}: {e}"); log(f"[oddspedia] ERRORE {nome}: {e}")
        time.sleep(1)
    partite = []
    def run(job):
        try:
            return op_partita(s, *job)
        except Exception as e:
            errori.append(f"{job[1]['ht']}-{job[1]['at']}: {e}"); log(f"[oddspedia] ERRORE {job[1]['ht']}-{job[1]['at']}: {e}")
    with ThreadPoolExecutor(2) as ex:
        for r in ex.map(run, jobs):
            if r and r["mercati"]: partite.append(r)
    return partite, errori

# --------------------------------------------------------------------------------------
FONTI = {
    "oddschecker": ("Oddschecker Italia", "https://www.oddschecker.com/it/", scrape_oddschecker),
    "oddspedia": ("Oddspedia Italia", "https://oddspedia.com/it/", scrape_oddspedia),
}

def scrivi(out, fonte, partite, stato):
    d = os.path.join(out, "data", fonte)
    os.makedirs(os.path.join(d, "p"), exist_ok=True)
    idx = []
    for p in partite:
        with open(os.path.join(d, "p", p["id"] + ".json"), "w") as f:
            json.dump(p, f, ensure_ascii=False, separators=(",", ":"))
        b1x2 = None
        for m in p["mercati"]:
            if m["n"] in ("Vincente", "Esito Finale") and len(m["s"]) == 3:
                b1x2 = [max(s_["o"].values()) for s_ in m["s"]]; break
        idx.append({"id": p["id"], "comp": p["comp"], "casa": p["casa"], "ospite": p["ospite"], "inizio": p["inizio"],
                    "nm": len(p["mercati"]), "nb": len(p["bk"]), "best": b1x2})
    idx.sort(key=lambda x: (x["inizio"], x["comp"]))
    with open(os.path.join(d, "index.json"), "w") as f:
        json.dump({"stato": stato, "partite": idx}, f, ensure_ascii=False, separators=(",", ":"))

def fallback(out, fonte, stato):
    """Riusa i dati pubblicati dall'esecuzione precedente (se recenti), segnalandoli come non aggiornati."""
    try:
        s = session()
        prev = s.get(f"{PAGES_URL}/data/{fonte}/index.json", timeout=30).json()
        pst = prev.get("stato", {})
        agg = pst.get("aggiornato_ok") or pst.get("aggiornato")
        if not agg or NOW - parse_dt(agg) > timedelta(hours=STALE_MAX_ORE): return False
        partite = []
        for it in prev.get("partite", []):
            if parse_dt(it["inizio"]) < NOW: continue
            r = s.get(f"{PAGES_URL}/data/{fonte}/p/{it['id']}.json", timeout=30)
            if r.status_code == 200: partite.append(r.json())
        if not partite: return False
        stato.update({"ok": False, "vecchi": True, "aggiornato_ok": agg})
        scrivi(out, fonte, partite, stato)
        return True
    except Exception as e:
        log(f"[{fonte}] fallback non riuscito: {e}")
        return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--solo", choices=list(FONTI))
    a = ap.parse_args()
    riepilogo = {"generato": iso(NOW), "fonti": {}}
    for fonte, (nome, url, fn) in FONTI.items():
        if a.solo and fonte != a.solo: continue
        t0 = time.time()
        stato = {"nome": nome, "url": url, "aggiornato": iso(NOW)}
        try:
            partite, errori = fn()
        except Exception as e:
            traceback.print_exc(); partite, errori = [], [str(e)]
        stato.update({"durata_s": round(time.time() - t0), "errori": errori[:20], "n_errori": len(errori), "n_partite": len(partite)})
        if partite:
            stato.update({"ok": True, "aggiornato_ok": iso(datetime.now(timezone.utc))})
            scrivi(a.out, fonte, partite, stato)
        elif not fallback(a.out, fonte, stato):
            stato.update({"ok": False})
            scrivi(a.out, fonte, [], stato)
        riepilogo["fonti"][fonte] = {k: stato.get(k) for k in ("nome", "ok", "vecchi", "n_partite", "n_errori", "aggiornato_ok", "durata_s")}
        log(f"== {fonte}: ok={stato.get('ok')} partite={len(partite)} errori={len(errori)} in {stato['durata_s']}s")
    os.makedirs(os.path.join(a.out, "data"), exist_ok=True)
    with open(os.path.join(a.out, "data", "stato.json"), "w") as f:
        json.dump(riepilogo, f, ensure_ascii=False, indent=1)
    print(json.dumps(riepilogo, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
