#!/usr/bin/env python3
"""Scarica le quote calcio (bookmaker italiani ADM) da Oddschecker Italia e Oddspedia Italia
e scrive JSON statici in <out>/data/<fonte>/.

Nessuna quota viene inventata: se una fonte non risponde viene marcata come "non disponibile"
(o, se presente, si riusano i dati dell'esecuzione precedente segnalandoli come NON aggiornati).
"""
import argparse, html, json, os, re, shutil, subprocess, sys, time, traceback
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from curl_cffi import requests as creq
from classifiche import calcola_rating, estrai_quote_movimento, pubblica_movimenti

NOW = datetime.now(timezone.utc)
GIORNI_AVANTI = 6          # Oddspedia: partite entro i prossimi N giorni
# Oddschecker segue solo i grandi campionati: nello stacco per le nazionali
# il turno successivo cade spesso oltre i 6 giorni, e il sito restava vuoto
# anche se l'API rispondeva. 13 giorni copre il weekend e la settimana di coppe.
OC_GIORNI = 13
OC_MAX = 24                # un turno intero (il tetto globale da 14 tagliava la Serie A)
MAX_PER_LEGA = 14          # limite di sicurezza per lega (Oddspedia)
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

def in_finestra(dt, giorni=None):
    g = GIORNI_AVANTI if giorni is None else giorni
    return NOW - timedelta(minutes=5) < dt < NOW + timedelta(days=g)

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
    # L'id storico 38925767 risponde 404; la pagina è stata rinominata.
    # Se l'HTML è raggiungibile, l'id nuovo si legge dalla pagina (vedi oc_lega).
    ("Conference League", 38925767, "/it/calcio/coppe-e-amichevoli-per-club/uefa-europa-conference-league"),
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

def oc_lega(s, comp, event_id, _path):
    """Usa l'API JSON pubblica che la pagina di lega di Oddschecker Italia chiama nel browser
    (/api/cx/sports-catalogue/v1/subevent-group), a blocchi di tipi di scommessa.
    L'HTML non va richiesto: sui runner è dietro Cloudflare e un 403 può far cadere anche l'API."""
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
            if i == 0: raise RuntimeError(f"HTTP {r.status_code} (eventId {event_id})")
            log(f"[oddschecker] {comp} blocco {i}: HTTP {r.status_code}"); continue
        for se in (r.json() or {}).get("subevents") or []:
            if se.get("type") != "MATCH" or se.get("inRunning"): continue
            if not in_finestra(parse_dt(se["startTime"]), OC_GIORNI): continue
            cur = subs.setdefault(se["id"], dict(se, markets=[]))
            cur["markets"] += se.get("markets") or []
        time.sleep(0.4)
    out = []
    for se in sorted(subs.values(), key=lambda x: x["startTime"])[:OC_MAX]:
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
    leghe_ok = 0
    for comp, ev, path in OC_LEGHE:
        try:
            ps = oc_lega(s, comp, ev, path)
            leghe_ok += 1
            log(f"[oddschecker] {comp}: {len(ps)} partite")
            partite += ps
        except Exception as e:
            errori.append(f"{comp}: {e}"); log(f"[oddschecker] ERRORE {comp}: {e}")
        time.sleep(0.5)
    return partite, errori, {"leghe_ok": leghe_ok, "leghe": len(OC_LEGHE), "finestra_giorni": OC_GIORNI}

# --------------------------------------------------------------------------------------
# ODDSPEDIA ITALIA  (https://oddspedia.com/it/)
# Lista partite e quote: API JSON della pagina (getLeagueInfo, getMatchList, getMatchOdds).
# Sugli IP dei runner GitHub anche l'API è un challenge Cloudflare, e il cookie di
# clearance non vale per curl_cffi: le chiamate passano da fetch() dentro Chrome.
# getMatchOdds usa bookmakerGeoCode=IT => solo bookmaker mostrati agli utenti italiani.
# --------------------------------------------------------------------------------------
OP = "https://oddspedia.com"
OP_LEGHE = [
    # Italia: Serie A, B e terzo livello (gironi), più coppe nazionali
    ("Serie A", "/it/calcio/italia/serie-a"),
    ("Serie B", "/it/calcio/italia/serie-b"),
    ("Coppa Italia", "/it/calcio/italia/coppa-italia"),
    ("Serie C - Girone A", "/it/calcio/italia/lega-pro-group-a"),
    ("Serie C - Girone B", "/it/calcio/italia/lega-pro-group-b"),
    ("Serie C - Girone C", "/it/calcio/italia/lega-pro-group-c"),
    ("Coppa Italia Serie C", "/it/calcio/italia/coppa-italia-lega-pro"),

    # Europa occidentale e britannica: prime tre divisioni quando presenti
    ("Premier League", "/it/calcio/inghilterra/premier-league"),
    ("Championship", "/it/calcio/inghilterra/championship"),
    ("League One", "/it/calcio/inghilterra/league-one"),
    ("League Two", "/it/calcio/inghilterra/league-two"),
    ("National League", "/it/calcio/inghilterra/national-league"),
    ("National League North", "/it/calcio/inghilterra/national-league-north"),
    ("National League South", "/it/calcio/inghilterra/national-league-south"),
    ("LaLiga", "/it/calcio/spagna/primera-division"),
    ("Segunda División", "/it/calcio/spagna/segunda-division"),
    ("Primera Federación", "/it/calcio/spagna/primera-federacion"),
    ("Bundesliga", "/it/calcio/germania/bundesliga"),
    ("2. Bundesliga", "/it/calcio/germania/2-bundesliga"),
    ("3. Liga", "/it/calcio/germania/3-liga"),
    ("Ligue 1", "/it/calcio/francia/ligue-1"),
    ("Ligue 2", "/it/calcio/francia/ligue-2"),
    ("National", "/it/calcio/francia/national"),
    ("Primeira Liga", "/it/calcio/portogallo/primeira-liga"),
    ("Segunda Liga", "/it/calcio/portogallo/segunda-liga"),
    ("Liga 3", "/it/calcio/portogallo/liga-3"),
    ("Eredivisie", "/it/calcio/olanda/eredivisie"),
    ("Eerste Divisie", "/it/calcio/olanda/eerste-divisie"),
    ("Tweede Divisie", "/it/calcio/olanda/tweede-divisie"),
    ("Belgian Pro League", "/it/calcio/belgio/first-division-a"),
    ("Challenger Pro League", "/it/calcio/belgio/first-division-b"),
    ("National Division", "/it/calcio/belgio/first-national-division"),
    ("Scottish Premiership", "/it/calcio/scozia/premiership"),
    ("Scottish Championship", "/it/calcio/scozia/championship"),
    ("Scottish League One", "/it/calcio/scozia/league-one"),
    ("Scottish League Two", "/it/calcio/scozia/league-two"),
    ("Austrian Bundesliga", "/it/calcio/austria/bundesliga"),
    ("2. Liga Austria", "/it/calcio/austria/2-liga"),
    ("Regionalliga Austria", "/it/calcio/austria/regionalliga"),
    ("Swiss Super League", "/it/calcio/svizzera/super-league"),
    ("Challenge League", "/it/calcio/svizzera/challenge-league"),
    ("Promotion League", "/it/calcio/svizzera/promotion-league"),

    # Europa settentrionale
    ("Superliga Denmark", "/it/calcio/danimarca/superliga"),
    ("1st Division Denmark", "/it/calcio/danimarca/1st-division"),
    ("2nd Division Denmark", "/it/calcio/danimarca/2nd-division"),
    ("Eliteserien", "/it/calcio/norvegia/eliteserien"),
    ("OBOS-ligaen", "/it/calcio/norvegia/obos-ligaen"),
    ("2. Division Norway", "/it/calcio/norvegia/2-division"),
    ("Allsvenskan", "/it/calcio/svezia/allsvenskan"),
    ("Superettan", "/it/calcio/svezia/superettan"),
    ("Ettan Sweden", "/it/calcio/svezia/ettan"),
    ("Veikkausliiga", "/it/calcio/finlandia/veikkausliiga"),
    ("Ykkosliiga", "/it/calcio/finlandia/ykkosliiga"),
    ("Kakkonen", "/it/calcio/finlandia/kakkonen"),
    ("Besta-deild", "/it/calcio/islanda/besta-deild"),
    ("1. Deild Iceland", "/it/calcio/islanda/1-deild"),
    ("Premier Division Ireland", "/it/calcio/irlanda/premier-division"),
    ("First Division Ireland", "/it/calcio/irlanda/first-division"),
    ("Northern Ireland Premiership", "/it/calcio/irlanda-del-nord/premiership"),
    ("Northern Ireland Championship", "/it/calcio/irlanda-del-nord/championship"),
    ("Cymru Premier", "/it/calcio/galles/cymru-premier"),
    ("Cymru North", "/it/calcio/galles/cymru-north"),
    ("Cymru South", "/it/calcio/galles/cymru-south"),

    # Europa centrale, orientale e Balcani
    ("Ekstraklasa", "/it/calcio/polonia/ekstraklasa"),
    ("1. Liga Poland", "/it/calcio/polonia/1-liga"),
    ("2. Liga Poland", "/it/calcio/polonia/2-liga"),
    ("Liga I Romania", "/it/calcio/romania/liga-1"),
    ("Liga II Romania", "/it/calcio/romania/liga-2"),
    ("Liga III Romania", "/it/calcio/romania/liga-3"),
    ("Czech First League", "/it/calcio/repubblica-ceca/1-liga"),
    ("Czech National League", "/it/calcio/repubblica-ceca/2-liga"),
    ("Czech Third League", "/it/calcio/repubblica-ceca/3-liga"),
    ("Slovak Super Liga", "/it/calcio/slovacchia/super-liga"),
    ("2. Liga Slovakia", "/it/calcio/slovacchia/2-liga"),
    ("NB I Hungary", "/it/calcio/ungheria/nb-i"),
    ("NB II Hungary", "/it/calcio/ungheria/nb-ii"),
    ("NB III Hungary", "/it/calcio/ungheria/nb-iii"),
    ("Croatian HNL", "/it/calcio/croazia/1-hnl"),
    ("Croatian 2. HNL", "/it/calcio/croazia/2-hnl"),
    ("Croatian 3. HNL", "/it/calcio/croazia/3-hnl"),
    ("Serbian SuperLiga", "/it/calcio/serbia/superliga"),
    ("Prva Liga Serbia", "/it/calcio/serbia/prva-liga"),
    ("Srpska Liga", "/it/calcio/serbia/srpska-liga"),
    ("Slovenian PrvaLiga", "/it/calcio/slovenia/1-snl"),
    ("Slovenian 2. SNL", "/it/calcio/slovenia/2-snl"),
    ("Slovenian 3. SNL", "/it/calcio/slovenia/3-snl"),
    ("Super Lig Turkey", "/it/calcio/turchia/super-lig"),
    ("1. Lig Turkey", "/it/calcio/turchia/1-lig"),
    ("2. Lig Turkey", "/it/calcio/turchia/2-lig"),
    ("3. Lig Turkey", "/it/calcio/turchia/3-lig"),
    ("Super League Greece", "/it/calcio/grecia/super-league"),
    ("Super League 2 Greece", "/it/calcio/grecia/super-league-2"),
    ("Gamma Ethniki", "/it/calcio/grecia/gamma-ethniki"),
    ("First League Bulgaria", "/it/calcio/bulgaria/first-league"),
    ("Second League Bulgaria", "/it/calcio/bulgaria/second-league"),
    ("Third League Bulgaria", "/it/calcio/bulgaria/third-league"),
    ("Premier League Ukraine", "/it/calcio/ucraina/premier-league"),
    ("First League Ukraine", "/it/calcio/ucraina/first-league"),
    ("Second League Ukraine", "/it/calcio/ucraina/second-league"),
    ("Premier League Azerbaijan", "/it/calcio/azerbaigian/premier-league"),
    ("First Division Azerbaijan", "/it/calcio/azerbaigian/first-division"),
    ("A Lyga Lithuania", "/it/calcio/lituania/a-lyga"),
    ("1 Lyga Lithuania", "/it/calcio/lituania/1-lyga"),
    ("Meistriliiga Estonia", "/it/calcio/estonia/meistriliiga"),
    ("Esiliiga Estonia", "/it/calcio/estonia/esiliiga"),
    ("Virsliga Latvia", "/it/calcio/lettonia/virsliga"),
    ("1. Liga Latvia", "/it/calcio/lettonia/1-liga"),
    ("Cyprus First Division", "/it/calcio/cipro/1-division"),
    ("Cyprus Second Division", "/it/calcio/cipro/2-division"),
    ("Albanian Superliga", "/it/calcio/albania/super-league"),
    ("Albanian First Division", "/it/calcio/albania/first-division"),
    ("Bosnia Premier League", "/it/calcio/bosnia/premier-liga"),
    ("First League FBiH", "/it/calcio/bosnia/first-league-fbih"),
    ("First League RS", "/it/calcio/bosnia/first-league-rs"),
    ("Montenegro First League", "/it/calcio/montenegro/first-league"),
    ("Montenegro Second League", "/it/calcio/montenegro/second-league"),
    ("North Macedonia First League", "/it/calcio/macedonia-del-nord/first-league"),
    ("North Macedonia Second League", "/it/calcio/macedonia-del-nord/second-league"),
    ("Georgia Erovnuli Liga", "/it/calcio/georgia/erovnuli-liga"),
    ("Georgia Erovnuli Liga 2", "/it/calcio/georgia/erovnuli-liga-2"),
    ("Armenia Premier League", "/it/calcio/armenia/premier-league"),
    ("Armenia First League", "/it/calcio/armenia/first-league"),
    ("Kosovo Superleague", "/it/calcio/kosovo/superleague"),
    ("Kosovo First League", "/it/calcio/kosovo/first-league"),
    ("Faroe Islands Premier League", "/it/calcio/isole-faroe/premier-league"),
    ("Faroe Islands 1. Deild", "/it/calcio/isole-faroe/1-deild"),

    # Altri paesi richiesti: Sud America, Nord America, Asia e Oceania
    ("Brasile Serie A", "/it/calcio/brasile/serie-a"),
    ("Brasile Serie B", "/it/calcio/brasile/serie-b"),
    ("Brasile Serie C", "/it/calcio/brasile/serie-c"),
    ("Argentina Primera División", "/it/calcio/argentina/primera-division"),
    ("Argentina Primera Nacional", "/it/calcio/argentina/primera-nacional"),
    ("Argentina Primera B", "/it/calcio/argentina/primera-b"),
    ("Argentina Primera C", "/it/calcio/argentina/primera-c"),
    ("Uruguay Primera División", "/it/calcio/uruguay/primera-division"),
    ("Uruguay Segunda División", "/it/calcio/uruguay/segunda-division"),
    ("Colombia Primera A", "/it/calcio/colombia/primera-a"),
    ("Colombia Primera B", "/it/calcio/colombia/primera-b"),
    ("Chile Primera División", "/it/calcio/cile/primera-division"),
    ("Chile Primera B", "/it/calcio/cile/primera-b"),
    ("Chile Segunda División", "/it/calcio/cile/segunda-division"),
    ("MLS", "/it/calcio/usa/mls"),
    ("USL Championship", "/it/calcio/usa/usl-championship"),
    ("USL League One", "/it/calcio/usa/usl-league-one"),
    ("A-League Australia", "/it/calcio/australia/a-league"),
    ("NPL Australia", "/it/calcio/australia/npl"),
    ("J1 League", "/it/calcio/giappone/j1-league"),
    ("J2 League", "/it/calcio/giappone/j2-league"),
    ("J3 League", "/it/calcio/giappone/j3-league"),
    ("Saudi Pro League", "/it/calcio/arabia-saudita/saudi-professional-league"),
    ("Saudi First Division", "/it/calcio/arabia-saudita/first-division"),
    ("Saudi Second Division", "/it/calcio/arabia-saudita/second-division"),
]
# id gruppo mercato Oddspedia, in ordine di importanza
OP_GRUPPI = [1, 7, 4, 11, 8, 9, 5, 6, 3, 10, 12]

_OP_FETCH = """async (url) => {
  const r = await fetch(url, {credentials: 'include', headers: {accept: 'application/json', referer: 'https://oddspedia.com/it/calcio'}});
  return {status: r.status, mitigated: r.headers.get('cf-mitigated'), text: await r.text()};
}"""
_OP_FETCH_MANY = """async (urls) => {
  const out = new Array(urls.length);
  await Promise.all(urls.map(async (url, i) => {
    try {
      const r = await fetch(url, {credentials: 'include', headers: {accept: 'application/json', referer: 'https://oddspedia.com/it/calcio'}});
      out[i] = {status: r.status, mitigated: r.headers.get('cf-mitigated'), text: await r.text()};
    } catch (e) {
      out[i] = {status: 0, mitigated: null, text: String(e)};
    }
  }));
  return out;
}"""

class OPClient:
    """Chrome reale: sugli IP dei runner il challenge Cloudflare blocca anche l'API JSON,
    e il cookie cf_clearance non viene accettato da curl_cffi."""

    def __init__(self):
        self._xvfb = None
        self._pw = None
        self._browser = None
        self._page = None
        try:
            self._ensure_display()
            from playwright.sync_api import sync_playwright
            chrome = shutil.which("google-chrome-stable") or shutil.which("google-chrome")
            if not chrome:
                raise RuntimeError("Google Chrome assente: serve per superare il challenge Cloudflare di Oddspedia")
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(
                executable_path=chrome, headless=False,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                      "--disable-blink-features=AutomationControlled", "--window-size=1400,900"])
            ctx = self._browser.new_context(locale="it-IT", timezone_id="Europe/Rome", viewport={"width": 1400, "height": 900})
            self._page = ctx.new_page()
            self._page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            self._pass_challenge()
        except Exception:
            self.close()
            raise

    def _ensure_display(self):
        if os.environ.get("DISPLAY"):
            return
        xvfb = shutil.which("Xvfb")
        if not xvfb:
            raise RuntimeError("Xvfb assente: serve un display per superare il challenge Cloudflare di Oddspedia")
        n = 99
        while os.path.exists(f"/tmp/.X11-unix/X{n}"):
            n += 1
        self._xvfb = subprocess.Popen([xvfb, f":{n}", "-screen", "0", "1400x900x24"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.environ["DISPLAY"] = f":{n}"
        time.sleep(0.4)

    def _probe(self):
        url = OP + "/api/v1/getLeagueInfo?geoCode=IT&sport=calcio&category=italia&league=serie-a&season=&timezone=Europe%2FRome&language=it"
        try:
            res = self._page.evaluate(_OP_FETCH, url)
        except Exception:
            return False
        return res.get("status") == 200 and (res.get("text") or "").lstrip().startswith("{")

    def _pass_challenge(self):
        page = self._page
        page.goto(OP + "/it/calcio", wait_until="domcontentloaded", timeout=60000)
        for _ in range(20):
            if self._probe():
                log("[oddspedia] sessione browser pronta")
                return
            page.wait_for_timeout(1200)
            for fr in page.frames:
                if "challenges.cloudflare.com" in (fr.url or ""):
                    try:
                        fr.locator("body").click(timeout=1000, force=True)
                    except Exception:
                        pass
        raise RuntimeError("challenge Cloudflare non superato")

    def _as_json(self, res):
        status = res.get("status") or 0
        if status == 200:
            try:
                return json.loads(res.get("text") or "")
            except json.JSONDecodeError:
                return "risposta non JSON"
        extra = " Cloudflare challenge" if res.get("mitigated") == "challenge" else ""
        if not extra and "Just a moment" in (res.get("text") or ""):
            extra = " Cloudflare challenge"
        return f"HTTP {status}{extra}" if status else (res.get("text") or "errore")

    def get_json(self, url):
        last = None
        for i in range(3):
            got = self._as_json(self._page.evaluate(_OP_FETCH, url))
            if not isinstance(got, str):
                return got
            last = got
            if "HTTP 403" in got and i == 0:
                log("[oddspedia] challenge di nuovo, lo riaffronto")
                self._pass_challenge()
                continue
            if got.startswith("HTTP 4"):
                break
            time.sleep(2 + 3 * i)
        raise RuntimeError(last or "errore")

    def get_json_many(self, urls):
        def parse(rows):
            return [self._as_json(r or {}) for r in rows]
        got = parse(self._page.evaluate(_OP_FETCH_MANY, urls))
        if any(isinstance(x, str) and "HTTP 403" in x for x in got):
            log("[oddspedia] challenge di nuovo, lo riaffronto")
            self._pass_challenge()
            got = parse(self._page.evaluate(_OP_FETCH_MANY, urls))
        return got

    def close(self):
        for fn in (lambda: self._browser and self._browser.close(), lambda: self._pw and self._pw.stop()):
            try:
                fn()
            except Exception:
                pass
        self._browser = self._pw = None
        if self._xvfb:
            self._xvfb.terminate()
            self._xvfb = None

def op_qs(params):
    """Stesso serializer della frontend: null diventa stringa vuota, poi URLSearchParams."""
    return urlencode([(k, "" if v is None else v) for k, v in params])

def op_api(client, q):
    endpoint = q.split("?", 1)[0]
    try:
        body = client.get_json(OP + "/api/v1/" + q)
    except RuntimeError as e:
        raise RuntimeError(f"{endpoint}: {e}") from e
    return body.get("data") if isinstance(body, dict) else {}

def op_api_many(client, qs):
    got = client.get_json_many([OP + "/api/v1/" + q for q in qs])
    out = []
    for q, body in zip(qs, got):
        if isinstance(body, str):
            out.append(RuntimeError(f"{q.split('?', 1)[0]}: {body}"))
        else:
            out.append(body.get("data") if isinstance(body, dict) else {})
    return out

def op_slug(path):
    parts = [p for p in path.split("/") if p]
    if len(parts) < 3:
        raise RuntimeError(f"percorso lega inatteso: {path}")
    return parts[-3], parts[-2], parts[-1]

def op_partite_lega(s, nome, path):
    sport, category, league = op_slug(path)
    info = op_api(s, "getLeagueInfo?" + op_qs([
        ("geoCode", "IT"), ("sport", sport), ("category", category), ("league", league),
        ("season", ""), ("timezone", "Europe/Rome"), ("language", "it"),
    ]))
    season = info.get("current_season")
    if not season:
        raise RuntimeError(f"getLeagueInfo: stagione assente per {league}")
    # La pagina di lega manda getMatchList con seasonId e l'intervallo del turno corrente.
    # Qui l'intervallo è la finestra del comparatore (prossimi N giorni): stessi parametri,
    # così non si dipende dall'HTML e non si perde il lunedì fuori dalla settimana in corso.
    start = (NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (NOW + timedelta(days=GIORNI_AVANTI)).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = [
        ("geoCode", "IT"), ("sport", sport), ("category", category), ("league", league),
        ("seasonId", season), ("popularLeaguesOnly", 0), ("excludeSpecialStatus", 0),
        ("status", "all"), ("sortBy", "default"), ("startDate", start), ("endDate", end),
        ("round", ""), ("page", 1), ("perPage", 150), ("perPageDefault", 150), ("language", "it"),
    ]
    ms = []
    for page in range(1, 4):
        q = [(k, page if k == "page" else v) for k, v in base]
        data = op_api(s, "getMatchList?" + op_qs(q))
        batch = data.get("matchList") or []
        ms += batch
        if page >= (data.get("total_pages") or 1) or not batch:
            break
        time.sleep(0.2)
    out = []
    for m in ms:
        dt = parse_dt(m["md"])
        if m.get("inplay") or m.get("matchstatus") not in (1, None) or not in_finestra(dt): continue
        out.append(m)
    out.sort(key=lambda m: m["md"])
    return out[:MAX_PER_LEGA]

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
    payloads = []
    for i in range(0, len(OP_GRUPPI), 4):
        gids = OP_GRUPPI[i:i + 4]
        payloads += list(zip(gids, op_api_many(s, [base + f"&oddGroupId={gid}" for gid in gids])))
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
    for gid, d in payloads:
        if isinstance(d, Exception):
            if gid == 1: raise d
            log(f"[oddspedia] gruppo {gid} {home}-{away}: {d}"); continue
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
    client = OPClient()
    try:
        return _scrape_oddspedia(client)
    finally:
        client.close()

def _scrape_oddspedia(s):
    jobs, errori, leghe_ok = [], [], 0
    blocchi = 0
    for nome, path in OP_LEGHE:
        try:
            ms = op_partite_lega(s, nome, path)
            leghe_ok += 1
            blocchi = 0
            log(f"[oddspedia] {nome}: {len(ms)} partite")
            jobs += [(nome, m) for m in ms]
        except Exception as e:
            errori.append(f"{nome}: {e}"); log(f"[oddspedia] ERRORE {nome}: {e}")
            if "HTTP 403" in str(e):
                blocchi += 1
                # Un challenge sull'API non dipende dalla lega: inutile insistere su tutte.
                if blocchi >= 3 and leghe_ok == 0:
                    errori.append("interrotto: l'API Oddspedia risponde 403")
                    log("[oddspedia] stop: tre 403 consecutivi, l'API è bloccata da questo IP")
                    break
            else:
                blocchi = 0
        time.sleep(0.3)
    # Distribuzione equa: prima il primo incontro di ogni campionato,
    # poi il secondo e così via. In questo modo il limite globale non privilegia
    # soltanto le competizioni elencate per prime.
    per_competizione = {}
    for job in jobs:
        per_competizione.setdefault(job[0], []).append(job)
    jobs_equilibrati = []
    for indice in range(MAX_PER_LEGA):
        for nome, _ in OP_LEGHE:
            elenco = per_competizione.get(nome, [])
            if indice < len(elenco):
                jobs_equilibrati.append(elenco[indice])
    MAX_PARTITE_ODDSPEDIA = 260
    if len(jobs_equilibrati) > MAX_PARTITE_ODDSPEDIA:
        log(f"[oddspedia] Limite globale: {len(jobs_equilibrati)} partite trovate; selezione distribuita su tutti i campionati, massimo {MAX_PARTITE_ODDSPEDIA}")
        jobs_equilibrati = jobs_equilibrati[:MAX_PARTITE_ODDSPEDIA]
    jobs = jobs_equilibrati
    partite = []
    for i, job in enumerate(jobs, 1):
        try:
            r = op_partita(s, *job)
        except Exception as e:
            errori.append(f"{job[1]['ht']}-{job[1]['at']}: {e}"); log(f"[oddspedia] ERRORE {job[1]['ht']}-{job[1]['at']}: {e}")
            r = None
        if r and r["mercati"]: partite.append(r)
        if i % 40 == 0: log(f"[oddspedia] quote {i}/{len(jobs)}")
    return partite, errori, {"leghe_ok": leghe_ok, "leghe": len(OP_LEGHE), "finestra_giorni": GIORNI_AVANTI}

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
    try:
        with open(os.path.join(d, "rating.json"), "w") as f:
            json.dump(calcola_rating(partite), f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        log(f"[{fonte}] classifica rating non scritta: {e}")

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
    quote_nuove = []
    for fonte, (nome, url, fn) in FONTI.items():
        if a.solo and fonte != a.solo: continue
        t0 = time.time()
        stato = {"nome": nome, "url": url, "aggiornato": iso(NOW)}
        info = {}
        try:
            result = fn()
            partite, errori = result[0], result[1]
            info = result[2] if len(result) > 2 else {}
        except Exception as e:
            traceback.print_exc(); partite, errori = [], [str(e)]
        if info.get("finestra_giorni"):
            stato["finestra_giorni"] = info["finestra_giorni"]
        stato.update({"durata_s": round(time.time() - t0), "errori": errori[:20], "n_errori": len(errori), "n_partite": len(partite)})
        fresco = False
        if partite:
            stato.update({"ok": True, "aggiornato_ok": iso(datetime.now(timezone.utc))})
            scrivi(a.out, fonte, partite, stato)
            fresco = True
        elif info.get("leghe_ok", 0) > 0:
            # L'API ha risposto: il calendario è vuoto, la fonte non è guasta.
            stato.update({"ok": True, "vuoto": True, "aggiornato_ok": iso(datetime.now(timezone.utc))})
            scrivi(a.out, fonte, [], stato)
            fresco = True
        elif not fallback(a.out, fonte, stato):
            stato.update({"ok": False})
            scrivi(a.out, fonte, [], stato)
        if fresco and partite:
            try:
                quote_nuove += estrai_quote_movimento(partite, fonte)
            except Exception as e:
                log(f"[{fonte}] quote per lo storico non estratte: {e}")
        riepilogo["fonti"][fonte] = {k: stato.get(k) for k in ("nome", "ok", "vecchi", "vuoto", "n_partite", "n_errori", "aggiornato_ok", "durata_s", "finestra_giorni")}
        log(f"== {fonte}: ok={stato.get('ok')} partite={len(partite)} errori={len(errori)} in {stato['durata_s']}s")
        if fonte == "oddspedia" and not stato.get("ok") and os.environ.get("GITHUB_ACTIONS") == "true":
            primo = errori[0] if errori else ""
            print(f"::warning title=Oddspedia non aggiornata::partite={len(partite)} errori={len(errori)} {primo}", flush=True)
    try:
        mov, st = pubblica_movimenti(a.out, quote_nuove)
        log(f"[storico] snapshot={mov.get('n_snap')} ore={mov.get('ore')} quote={len(st.get('q') or {})}")
    except Exception as e:
        log(f"[storico] non aggiornato: {e}")
        traceback.print_exc()
    os.makedirs(os.path.join(a.out, "data"), exist_ok=True)
    with open(os.path.join(a.out, "data", "stato.json"), "w") as f:
        json.dump(riepilogo, f, ensure_ascii=False, indent=1)
    print(json.dumps(riepilogo, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
