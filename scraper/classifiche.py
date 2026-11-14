"""Classifica rating e movimenti di quota.

Il rating di un mercato, per un solo bookmaker, è il payout:
100 / somma(1/quota di ogni esito). Si usano solo insiemi di esiti completi
e mutualmente esclusivi (niente doppia chance, niente risultati esatti aperti).

I movimenti confrontano 1X2 e Over/Under 2.5 con uno storico compatto di 26 ore.
"""
import json, os, re
from datetime import datetime, timedelta, timezone

ORE_STORICO = 26
ORE_MOVIMENTO = 24
TOP_N = 30

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORICO_FILE = os.path.join(ROOT, "stato", "storico-quote.json")


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(s):
    s = str(s).strip().replace(" ", "T")
    if s.endswith("+00"):
        s += ":00"
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _low(s):
    return (s or "").strip().lower().replace("º", "°")


def periodo(nome):
    n = _low(nome)
    if any(x in n for x in ("2° tempo", "secondo tempo", "2nd half", "2 tempo")):
        return " – 2º Tempo"
    if any(x in n for x in ("1° tempo", "primo tempo", "1st half", "1 tempo")):
        return " – 1º Tempo"
    return ""


def lato_1x2(n):
    t = (n or "").strip()
    if t == "X" or t.startswith("X ") or t.startswith("X·") or t.startswith("X ·"):
        return "X"
    if t.startswith("1 ·") or t.startswith("1·") or re.match(r"^1\b", t):
        return "1"
    if t.startswith("2 ·") or t.startswith("2·") or re.match(r"^2\b", t):
        return "2"
    return None


def norm_line(v):
    if v is None:
        return ""
    s = str(v).strip().replace(",", ".")
    m = re.fullmatch(r"[+\-]?\d+(?:\.\d+)?", s)
    if not m:
        m2 = re.search(r"(\d+(?:\.\d+)?)\s*$", s)
        if not m2:
            return ""
        s = m2.group(1)
    try:
        f = float(s)
    except ValueError:
        return ""
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    txt = f"{f:.2f}".rstrip("0").rstrip(".")
    return txt


def ou_name_line(sel):
    n = (sel.get("n") or "").strip()
    line = sel.get("l")
    m = re.match(r"(?i)^(over|under|più|piu|meno)\s+([0-9]+(?:[.,][0-9]+)?)$", n)
    if m and not line:
        return m.group(1), m.group(2)
    return n, line


def _payout(quote):
    if len(quote) < 2:
        return None
    tot = 0.0
    for q in quote:
        v = q["q"]
        if v is None or v <= 1.01:
            return None
        tot += 1.0 / v
    if tot <= 0:
        return None
    rating = 100.0 / tot
    # Fuori da questa fascia l'insieme è quasi certamente incompleto
    # (mancano esiti) oppure le quote non sono utilizzabili.
    if rating < 40 or rating > 130:
        return None
    return round(rating, 1)


def rating_di(sels, bid):
    quote = []
    for s in sels:
        raw = (s.get("o") or {}).get(bid)
        if raw is None:
            return None
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return None
        if v <= 1.01:
            return None
        quote.append({"n": s["n"], "q": round(v, 2)})
    rating = _payout(quote)
    if rating is None:
        return None
    return rating, quote


_CHIUDE = re.compile(r"(altro|any other|other score|o più|or more|\+|più di)", re.I)


def _gruppi_ou(nome, sels, p):
    buckets = {}
    for s in sels:
        name, line = ou_name_line(s)
        side = _low(name)
        if side.startswith("over") or side in ("più", "piu"):
            key = "Over"
        elif side.startswith("under") or side in ("meno",):
            key = "Under"
        else:
            continue
        line_s = norm_line(line) or norm_line(nome)
        if not line_s:
            continue
        buckets.setdefault(line_s, {})[key] = s
    out = []
    for line, g in buckets.items():
        if "Over" in g and "Under" in g:
            out.append({
                "mercato": f"Over/Under {line}{p}",
                "sels": [
                    {"n": "Over", "o": g["Over"].get("o") or {}},
                    {"n": "Under", "o": g["Under"].get("o") or {}},
                ],
            })
    return out


def _gruppi_handicap(nome, sels, p):
    buckets = {}
    for s in sels:
        line = (s.get("l") or "").strip() or "—"
        buckets.setdefault(line, []).append(s)
    asiatico = "asiat" in _low(nome) or "asian" in _low(nome)
    out = []
    for line, group in buckets.items():
        if len(group) not in (2, 3):
            continue
        lati = [lato_1x2(s["n"]) for s in group]
        if len(group) == 3 and None not in lati and len(set(lati)) == 3:
            ordered = [next(s for s in group if lato_1x2(s["n"]) == w) for w in ("1", "X", "2")]
            lab = f"Handicap Asiatico {line}{p}" if asiatico else f"Handicap {line}{p}"
            out.append({"mercato": lab, "sels": [{"n": s["n"], "o": s.get("o") or {}} for s in ordered]})
        elif len(group) == 2:
            lab = f"Handicap Asiatico {line}{p}" if asiatico else f"Handicap {line}{p}"
            out.append({"mercato": lab, "sels": [{"n": s["n"], "o": s.get("o") or {}} for s in group]})
    return out


def gruppi_mercato(m):
    """Insiemi completi di esiti esclusivi dentro un mercato già normalizzato."""
    nome = m.get("n") or ""
    nl = _low(nome)
    sels = m.get("s") or []
    if len(sels) < 2:
        return []
    if "doppia chance" in nl or "double chance" in nl:
        return []
    if " e " in nl or " and " in nl:
        return []
    if any(x in nl for x in ("marcatore", "giocatore", "player", "cartellin", "angolo", "corner", "rigore", "assist", "tiri", "falli")):
        return []
    p = periodo(nome)
    if "pari" in nl and "dispari" in nl and len(sels) == 2:
        return [{"mercato": "Pari/Dispari" + p, "sels": sels}]
    if any(x in nl for x in ("gol/no gol", "entrambe", "both teams", "goal/nogoal", "gg/ng")) and len(sels) == 2:
        return [{"mercato": "Goal/NoGoal" + p, "sels": sels}]
    if any(x in nl for x in ("rimborso", "draw no bet", "dnb")) and len(sels) == 2:
        return [{"mercato": "DNB" + p, "sels": sels}]
    if "parziale" in nl or "half time/full time" in nl or "ht/ft" in nl:
        return [{"mercato": "Parziale/Finale", "sels": sels}] if len(sels) == 9 else []
    if any(x in nl for x in ("risultato esatto", "correct score")):
        names = [s.get("n") or "" for s in sels]
        if any(_CHIUDE.search(n) for n in names) and len(sels) <= 40:
            return [{"mercato": "Risultato esatto" + p, "sels": sels}]
        return []
    if any(x in nl for x in ("primo gol", "segna per prima", "prima squadra", "first team to score", "first goal")) and len(sels) == 3:
        return [{"mercato": "Primo gol" + p, "sels": sels}]
    if "handicap" in nl or nl.startswith("spread"):
        return _gruppi_handicap(nome, sels, p)
    if any(x in nl for x in ("under/over", "over/under", "goal totali", "totale gol", "gol totali", "asian total", "totale goal")) and "esatt" not in nl and "squadra" not in nl:
        return _gruppi_ou(nome, sels, p)
    if len(sels) == 3 and len({lato_1x2(s.get("n")) for s in sels}) == 3 and all(lato_1x2(s.get("n")) for s in sels):
        if any(x in nl for x in ("non subendo", "to nil", "rimonta", "entrambi i tempi", "both halves")):
            return []
        return [{"mercato": "1X2" + p, "sels": sels}]
    names = [s.get("n") or "" for s in sels]
    if 2 <= len(sels) <= 12 and any(_CHIUDE.search(n) for n in names):
        return [{"mercato": nome.strip(), "sels": sels}]
    if len(sels) == 2:
        labs = {_low(s.get("n")) for s in sels}
        if labs <= {"sì", "si", "no", "yes"}:
            lab = "Goal/NoGoal" + p if ("gol" in nl or "segn" in nl) else nome.strip()
            return [{"mercato": lab, "sels": sels}]
        if labs == {"pari", "dispari"}:
            return [{"mercato": "Pari/Dispari" + p, "sels": sels}]
        if labs == {"over", "under"}:
            line = norm_line(sels[0].get("l")) or norm_line(nome)
            lab = f"Over/Under {line}{p}" if line else "Over/Under" + p
            return [{"mercato": lab, "sels": [{"n": "Over" if _low(s["n"]).startswith("over") else "Under", "o": s.get("o") or {}} for s in sorted(sels, key=lambda x: 0 if _low(x["n"]).startswith("over") else 1)]}]
    return []


def _nome_book(p, bid):
    info = (p.get("bk") or {}).get(bid) or (p.get("bk") or {}).get(str(bid)) or {}
    return info.get("n") or str(bid)


def calcola_rating(partite):
    """Top 30 partite distinte per bookmaker, con gli altri mercati nel dettaglio."""
    books = {}
    for p in partite or []:
        gruppi = []
        for m in p.get("mercati") or []:
            gruppi.extend(gruppi_mercato(m))
        if not gruppi:
            continue
        bids = set()
        for g in gruppi:
            for s in g["sels"]:
                bids.update((s.get("o") or {}).keys())
        for bid in bids:
            per_label = {}
            for g in gruppi:
                val = rating_di(g["sels"], bid)
                if not val:
                    continue
                rating, quote = val
                prev = per_label.get(g["mercato"])
                if prev is None or rating > prev["rating"]:
                    per_label[g["mercato"]] = {"mercato": g["mercato"], "rating": rating, "quote": quote}
            if not per_label:
                continue
            righe_m = sorted(per_label.values(), key=lambda x: -x["rating"])
            best, altri = righe_m[0], righe_m[1:]
            b = books.setdefault(str(bid), {"id": str(bid), "n": _nome_book(p, bid), "righe": [], "_visti": set()})
            if b["n"] == str(bid):
                b["n"] = _nome_book(p, bid)
            if p["id"] in b["_visti"]:
                continue
            b["_visti"].add(p["id"])
            b["righe"].append({
                "id": p["id"], "casa": p.get("casa") or "", "ospite": p.get("ospite") or "",
                "comp": p.get("comp") or "", "inizio": p.get("inizio") or "",
                "mercato": best["mercato"], "rating": best["rating"], "quote": best["quote"],
                "altri": [{"mercato": a["mercato"], "rating": a["rating"], "quote": a["quote"]} for a in altri[:80]],
            })
    out = []
    for b in books.values():
        b.pop("_visti", None)
        b["righe"].sort(key=lambda r: (-r["rating"], r.get("inizio") or "", r.get("casa") or ""))
        b["righe"] = b["righe"][:TOP_N]
        out.append(b)
    out.sort(key=lambda b: (b.get("n") or "").lower())
    return {"generato": iso(datetime.now(timezone.utc)), "bookmaker": out}


def _is_1x2_ft(nome):
    n = _low(nome)
    if any(x in n for x in ("tempo", "1°", "2°", "half", " e ", " and ", "doppia", "handicap", "esatto")):
        return False
    return n in ("vincente", "esito finale", "win market") or n.startswith("esito finale") or n.startswith("win market")


def _is_ou_ft(nome):
    n = _low(nome)
    if any(x in n for x in ("tempo", "1°", "2°", "half", "squadra", "casa", "ospite", "home", "away", "corner", "angolo", "cartell", "tiri", "esatt", "pari")):
        return False
    return any(x in n for x in ("under/over", "over/under", "goal totali", "totale gol", "gol totali", "asian total", "totale goal"))


def estrai_quote_movimento(partite, fonte):
    """Quote 1X2 e Over/Under 2.5, un record per bookmaker che ha l'insieme completo."""
    rows = []
    for p in partite or []:
        base = {
            "fonte": fonte, "id": p.get("id"), "casa": p.get("casa") or "", "ospite": p.get("ospite") or "",
            "comp": p.get("comp") or "", "inizio": p.get("inizio") or "",
        }
        for m in p.get("mercati") or []:
            if _is_1x2_ft(m.get("n")):
                sels = m.get("s") or []
                if len(sels) != 3:
                    continue
                order = []
                ok = True
                for want in ("1", "X", "2"):
                    sel = next((s for s in sels if lato_1x2(s.get("n")) == want), None)
                    if not sel:
                        ok = False
                        break
                    order.append(sel)
                if not ok:
                    continue
                bids = set(order[0].get("o") or {})
                for s in order[1:]:
                    bids &= set(s.get("o") or {})
                for bid in bids:
                    odds = []
                    good = True
                    for s in order:
                        try:
                            v = float(s["o"][bid])
                        except (KeyError, TypeError, ValueError):
                            good = False
                            break
                        if v <= 1:
                            good = False
                            break
                        odds.append(round(v, 2))
                    if good:
                        rows.append({**base, "mk": "1", "bid": str(bid), "bkn": _nome_book(p, bid), "o": odds})
            elif _is_ou_ft(m.get("n")):
                pair = {}
                for s in m.get("s") or []:
                    name, line = ou_name_line(s)
                    ln, mn = norm_line(line), norm_line(m.get("n"))
                    if not (ln == "2.5" or (ln == "" and mn == "2.5")):
                        continue
                    side = _low(name)
                    if side.startswith("over"):
                        pair["O"] = s
                    elif side.startswith("under"):
                        pair["U"] = s
                if "O" not in pair or "U" not in pair:
                    continue
                bids = set(pair["O"].get("o") or {}) & set(pair["U"].get("o") or {})
                for bid in bids:
                    try:
                        odds = [round(float(pair["O"]["o"][bid]), 2), round(float(pair["U"]["o"][bid]), 2)]
                    except (KeyError, TypeError, ValueError):
                        continue
                    if any(v <= 1 for v in odds):
                        continue
                    rows.append({**base, "mk": "ou", "bid": str(bid), "bkn": _nome_book(p, bid), "o": odds})
    return rows


def carica_storico():
    try:
        with open(STORICO_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("snap"), list) and isinstance(d.get("q"), dict):
            d.setdefault("ev", {})
            d.setdefault("bk", {})
            return d
    except (OSError, json.JSONDecodeError):
        pass
    return {"snap": [], "ev": {}, "bk": {}, "q": {}}


def _salva_storico(st):
    os.makedirs(os.path.dirname(STORICO_FILE), exist_ok=True)
    tmp = STORICO_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, STORICO_FILE)


def _rebase_series(series, cut):
    p = series.get("p", -1)
    if p < cut:
        return None
    carry = None
    kept = []
    bordo = False
    for idx, odds in series.get("h") or []:
        if idx < cut:
            carry = odds
        elif idx == cut:
            bordo = True
            kept.append([0, odds])
        else:
            kept.append([idx - cut, odds])
    if not bordo and carry is not None:
        kept.insert(0, [0, carry])
    if not kept:
        return None
    return {"p": p - cut, "h": kept}


def _potatura(st, ora):
    snaps = st.get("snap") or []
    if not snaps:
        return
    cut = None
    for i, t in enumerate(snaps):
        try:
            if ora - parse_dt(t) <= timedelta(hours=ORE_STORICO):
                cut = i
                break
        except Exception:
            continue
    if cut is None:
        st["snap"], st["ev"], st["bk"], st["q"] = [], {}, {}, {}
        return
    if cut == 0:
        return
    st["snap"] = snaps[cut:]
    nq = {}
    vivi = set()
    for k, series in (st.get("q") or {}).items():
        nb = _rebase_series(series, cut)
        if not nb:
            continue
        nq[k] = nb
        fonte, mid, _mk, _bid = k.split("|", 3)
        vivi.add(fonte + "|" + mid)
    st["q"] = nq
    st["ev"] = {k: v for k, v in (st.get("ev") or {}).items() if k in vivi}
    # bookmaker: tieni solo quelli ancora citati
    usati = set()
    for k in nq:
        fonte, _mid, _mk, bid = k.split("|", 3)
        usati.add(fonte + "|" + bid)
    st["bk"] = {k: v for k, v in (st.get("bk") or {}).items() if k in usati}


def _aggiungi_snapshot(st, rows, ora_iso):
    idx = len(st["snap"])
    st["snap"].append(ora_iso)
    for row in rows:
        ek = f"{row['fonte']}|{row['id']}"
        st["ev"][ek] = {"casa": row["casa"], "ospite": row["ospite"], "comp": row["comp"], "inizio": row["inizio"]}
        st["bk"][f"{row['fonte']}|{row['bid']}"] = row["bkn"]
        qk = f"{row['fonte']}|{row['id']}|{row['mk']}|{row['bid']}"
        packed = [int(round(float(x) * 100)) for x in row["o"]]
        series = st["q"].get(qk)
        if not series:
            st["q"][qk] = {"p": idx, "h": [[idx, packed]]}
        else:
            series["p"] = idx
            last = series["h"][-1][1] if series.get("h") else None
            if last != packed:
                series["h"].append([idx, packed])


def _valore(series, i):
    v = None
    for idx, odds in series.get("h") or []:
        if idx <= i:
            v = odds
        else:
            break
    return v


def _quando_esito(series, base, last, i):
    quando = base
    base_v = _valore(series, base)
    prev = base_v[i] if base_v and i < len(base_v) else None
    for idx, odds in series.get("h") or []:
        if idx <= base or idx > last:
            continue
        if i < len(odds) and odds[i] != prev:
            prev = odds[i]
            quando = idx
    return quando


_ETICHETTE = {
    "1": lambda ev: [f"1 · {ev.get('casa') or '1'}", "X", f"2 · {ev.get('ospite') or '2'}"],
    "ou": lambda ev: ["Over 2.5", "Under 2.5"],
}
_TIPI = {"1": "1X2", "ou": "Over/Under 2.5"}


def _pub(m):
    return {k: m[k] for k in ("var", "dir", "da_q", "a_q", "bk", "tipo", "esito", "quando")}


def calcola_movimenti(st, ora=None):
    ora = ora or datetime.now(timezone.utc)
    snaps = st.get("snap") or []
    vuoto = {"generato": iso(ora), "da": None, "a": snaps[-1] if snaps else None, "ore": 0, "n_snap": len(snaps), "fonti": {"oddschecker": [], "oddspedia": []}}
    if len(snaps) < 2:
        if snaps:
            vuoto["da"] = snaps[0]
        return vuoto
    last = len(snaps) - 1
    try:
        latest = parse_dt(snaps[-1])
    except Exception:
        return vuoto
    limite = latest - timedelta(hours=ORE_MOVIMENTO)
    base = 0
    for i, t in enumerate(snaps[:-1]):
        try:
            if parse_dt(t) >= limite:
                base = i
                break
        except Exception:
            continue
    try:
        ore = round((latest - parse_dt(snaps[base])).total_seconds() / 3600, 1)
    except Exception:
        ore = 0
    # (fonte, id, mk) -> list of book records
    gruppi = {}
    for qk, series in (st.get("q") or {}).items():
        try:
            fonte, mid, mk, bid = qk.split("|", 3)
        except ValueError:
            continue
        if mk not in _TIPI:
            continue
        pidx = series.get("p", -1)
        if pidx < 0 or pidx >= len(snaps):
            continue
        try:
            if latest - parse_dt(snaps[pidx]) > timedelta(hours=6):
                continue
        except Exception:
            continue
        ev = (st.get("ev") or {}).get(f"{fonte}|{mid}")
        if not ev:
            continue
        try:
            if parse_dt(ev.get("inizio") or "") < ora - timedelta(minutes=5):
                continue
        except Exception:
            pass
        a = _valore(series, last)
        b = _valore(series, base)
        if not a or not b or len(a) != len(b):
            continue
        gruppi.setdefault((fonte, mid, mk), []).append({
            "bid": bid, "bkn": (st.get("bk") or {}).get(f"{fonte}|{bid}") or bid,
            "a": a, "b": b, "series": series, "ev": ev,
        })
    per = {}
    for (fonte, mid, mk), books in gruppi.items():
        ev = books[0]["ev"]
        labels = _ETICHETTE[mk](ev)
        tipo = _TIPI[mk]
        dest = per.setdefault((fonte, mid), {"ev": ev, "mov": []})
        for bk in books:
            for i, (oa, ob) in enumerate(zip(bk["a"], bk["b"])):
                if not ob or oa == ob:
                    continue
                var = (oa - ob) / ob * 100.0
                if abs(var) < 0.05:
                    continue
                qn = _quando_esito(bk["series"], base, last, i)
                dest["mov"].append({
                    "var": round(var, 1), "dir": "UP" if oa > ob else "DOWN",
                    "da_q": round(ob / 100, 2), "a_q": round(oa / 100, 2),
                    "bk": bk["bkn"], "tipo": tipo,
                    "esito": labels[i] if i < len(labels) else str(i + 1),
                    "quando": snaps[min(qn, last)],
                })
        nout = len(books[0]["a"])
        if len(books) >= 2:
            for i in range(nout):
                fs, ts, qn = [], [], base
                for bk in books:
                    if i < len(bk["b"]) and i < len(bk["a"]) and bk["b"][i] and bk["a"][i]:
                        fs.append(bk["b"][i])
                        ts.append(bk["a"][i])
                        if bk["a"][i] != bk["b"][i]:
                            qn = max(qn, _quando_esito(bk["series"], base, last, i))
                if len(fs) < 2:
                    continue
                ob, oa = sum(fs) / len(fs), sum(ts) / len(ts)
                if not ob or abs(oa - ob) < 0.5:
                    continue
                var = (oa - ob) / ob * 100.0
                if abs(var) < 0.05 or round(var, 1) == 0:
                    continue
                dest["mov"].append({
                    "var": round(var, 1), "dir": "UP" if oa > ob else "DOWN",
                    "da_q": round(ob / 100, 2), "a_q": round(oa / 100, 2),
                    "bk": "Media bookmaker", "tipo": tipo,
                    "esito": labels[i] if i < len(labels) else str(i + 1),
                    "quando": snaps[min(qn, last)],
                })
    fonti = {"oddschecker": [], "oddspedia": []}
    for (fonte, mid), pack in per.items():
        movs = [m for m in pack["mov"] if m["var"] != 0]
        if not movs:
            continue
        movs.sort(key=lambda m: (-abs(m["var"]), m["tipo"], m["esito"], m["bk"]))
        ev = pack["ev"]
        best, altri = movs[0], movs[1:40]
        if fonte not in fonti:
            fonti[fonte] = []
        fonti[fonte].append({
            "id": mid, "casa": ev.get("casa") or "", "ospite": ev.get("ospite") or "",
            "comp": ev.get("comp") or "", "inizio": ev.get("inizio") or "",
            **_pub(best), "altri": [_pub(a) for a in altri],
        })
    for k in fonti:
        fonti[k].sort(key=lambda r: (-abs(r["var"]), r.get("inizio") or ""))
        fonti[k] = fonti[k][:TOP_N]
    return {
        "generato": iso(ora), "da": snaps[base], "a": snaps[-1], "ore": ore,
        "n_snap": len(snaps), "fonti": fonti,
    }


def pubblica_movimenti(out, rows):
    """Aggiorna lo storico (se ci sono quote nuove) e scrive data/movimenti.json."""
    st = carica_storico()
    ora = datetime.now(timezone.utc)
    if rows:
        _aggiungi_snapshot(st, rows, iso(ora))
        _potatura(st, ora)
        _salva_storico(st)
    mov = calcola_movimenti(st, ora)
    dest = os.path.join(out, "data")
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "movimenti.json"), "w", encoding="utf-8") as f:
        json.dump(mov, f, ensure_ascii=False, separators=(",", ":"))
    return mov, st
