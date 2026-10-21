/* Comparatore Quote – logica comune (nessuna dipendenza esterna) */
const FONTI = {
  oddschecker: { nome: "Oddschecker", sito: "https://www.oddschecker.com/it/" },
  oddspedia:   { nome: "Oddspedia",   sito: "https://oddspedia.com/it/" },
};
const TZ = "Europe/Rome";
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const norm = s => String(s || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
const qs = new URLSearchParams(location.search);
const fmtOra = d => new Date(d).toLocaleTimeString("it-IT", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
const fmtGiorno = d => new Date(d).toLocaleDateString("it-IT", { timeZone: TZ, weekday: "long", day: "numeric", month: "long" });
const fmtDT = d => new Date(d).toLocaleString("it-IT", { timeZone: TZ, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const q2 = v => (v == null ? "–" : Number(v).toFixed(2));

async function getJSON(url) {
  const r = await fetch(url + (url.includes("?") ? "&" : "?") + "t=" + Math.floor(Date.now() / 60000), { cache: "no-store" });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

/* ---------- pulsanti flottanti: A+ A- e torna su ---------- */
function setupFab() {
  const fab = document.createElement("div");
  fab.className = "fab";
  fab.innerHTML = '<button id="su" title="Torna su" aria-label="Torna su">↑</button><button id="piu" title="Ingrandisci testo" aria-label="Ingrandisci testo">+</button><button id="meno" title="Riduci testo" aria-label="Riduci testo">−</button>';
  document.body.appendChild(fab);
  let fs = parseFloat(localStorage.getItem("cq_fs") || "16");
  const apply = () => { document.documentElement.style.setProperty("--fs", fs + "px"); localStorage.setItem("cq_fs", fs); };
  apply();
  $("#piu").onclick = () => { fs = Math.min(26, fs + 1); apply(); };
  $("#meno").onclick = () => { fs = Math.max(11, fs - 1); apply(); };
  $("#su").onclick = () => window.scrollTo({ top: 0, behavior: "smooth" });
  const onScroll = () => { $("#su").style.display = window.scrollY > 400 ? "block" : "none"; };
  window.addEventListener("scroll", onScroll, { passive: true }); onScroll();
}

function statoHTML(st, fonte) {
  if (!st) return `<div class="status ko">⚠️ Stato della fonte sconosciuto.</div>`;
  const link = `<a href="${esc(FONTI[fonte].sito)}" target="_blank" rel="noopener">${esc(st.nome || FONTI[fonte].nome)}</a>`;
  if (st.ok) return `<div class="status ok">✅ ${link} · aggiornato alle <b>${fmtOra(st.aggiornato_ok)}</b> (ora italiana) · ${st.n_partite} partite${st.n_errori ? ` · ${st.n_errori} pagine non lette` : ""}</div>`;
  if (st.vecchi) return `<div class="status old">⚠️ ${link} non ha risposto all'ultimo aggiornamento (${fmtOra(st.aggiornato)}). Mostro i dati dell'aggiornamento precedente delle <b>${fmtOra(st.aggiornato_ok)}</b>: le quote potrebbero essere cambiate.</div>`;
  return `<div class="status ko">⛔ ${link} <b>non disponibile</b> in questo momento (tentativo delle ${fmtOra(st.aggiornato)} ora italiana). Nessuna quota mostrata per questa fonte.${st.errori && st.errori.length ? `<br><small>${esc(st.errori[0])}</small>` : ""}</div>`;
}

/* ===================================================== HOME ===================================================== */
async function home() {
  setupFab();
  let fonte = FONTI[qs.get("f")] ? qs.get("f") : (localStorage.getItem("cq_fonte") || "oddschecker");
  if (!FONTI[fonte] && fonte !== "info") fonte = "oddschecker";
  const tabs = $("#tabs"), main = $("#main");
  const cache = {};
  let compSel = "";

  function drawTabs() {
    tabs.innerHTML = Object.entries(FONTI).map(([k, v]) => `<button class="tab ${k === fonte ? "on" : ""}" data-f="${k}">${v.nome}</button>`).join("") +
      `<button class="tab ${fonte === "info" ? "on" : ""}" data-f="info">ℹ️ Fonti</button>`;
    tabs.querySelectorAll(".tab").forEach(b => b.onclick = () => { fonte = b.dataset.f; compSel = ""; if (fonte !== "info") localStorage.setItem("cq_fonte", fonte); history.replaceState(null, "", "?f=" + fonte); $("#q").value = ""; load(); });
  }

  async function info() {
    let st = {};
    try { st = await getJSON("data/stato.json"); } catch (e) {}
    main.innerHTML = `<div class="src">` + Object.keys(FONTI).map(k => statoHTML((st.fonti || {})[k], k)).join("") + `</div>
      <div class="status"><b>Come funziona</b><br>Le quote vengono lette automaticamente ogni 30 minuti da GitHub Actions dalle versioni <b>italiane</b> dei comparatori (solo bookmaker con concessione ADM mostrati agli utenti italiani) e pubblicate qui. Nessuna quota è inventata: se una fonte non risponde viene indicata come non disponibile.<br><br>
      <b>Oddschecker Italia</b> (oddschecker.com/it): tutti i mercati della partita, ~20–25 bookmaker italiani.<br>
      <b>Oddspedia Italia</b> (oddspedia.com/it): mercati principali, bookmaker italiani filtrati da Oddspedia (di solito 5–8).<br><br>
      Le quote possono cambiare in qualsiasi momento: verifica sempre sul sito del bookmaker prima di giocare. Gioco vietato ai minori di 18 anni. Il gioco può causare dipendenza.</div>`;
    $("#comps").innerHTML = "";
  }

  async function load() {
    drawTabs();
    if (fonte === "info") return info();
    main.innerHTML = `<div class="empty">Caricamento quote…</div>`;
    try { cache[fonte] = cache[fonte] || await getJSON(`data/${fonte}/index.json`); }
    catch (e) { main.innerHTML = statoHTML({ nome: FONTI[fonte].nome, ok: false, aggiornato: new Date(), errori: ["Dati non ancora pubblicati"] }, fonte); return; }
    draw();
  }

  function draw() {
    if (fonte === "info") return;
    const d = cache[fonte]; if (!d) return;
    const q = norm($("#q").value.trim());
    const comps = [...new Set(d.partite.map(p => p.comp))];
    $("#comps").innerHTML = `<button class="chip ${compSel ? "" : "on"}" data-c="">Tutte</button>` + comps.map(c => `<button class="chip ${c === compSel ? "on" : ""}" data-c="${esc(c)}">${esc(c)}</button>`).join("");
    $("#comps").querySelectorAll(".chip").forEach(b => b.onclick = () => { compSel = b.dataset.c; draw(); });
    const list = d.partite.filter(p => (!compSel || p.comp === compSel) && (!q || norm(p.casa + " " + p.ospite + " " + p.comp).includes(q)) && new Date(p.inizio) > Date.now() - 5 * 60e3);
    let h = statoHTML(d.stato, fonte), lastDay = "", lastComp = "";
    if (!list.length) h += `<div class="empty">${d.partite.length ? "Nessuna partita trovata con questi filtri." : "Nessuna partita disponibile."}</div>`;
    for (const p of list) {
      const day = fmtGiorno(p.inizio);
      if (day !== lastDay) { h += `<div class="day">${esc(day)}</div>`; lastDay = day; lastComp = ""; }
      if (p.comp !== lastComp) { h += `<div class="comp">${esc(p.comp)}</div>`; lastComp = p.comp; }
      const b = p.best ? `<div class="b3"><span><small>1</small>${q2(p.best[0])}</span><span><small>X</small>${q2(p.best[1])}</span><span><small>2</small>${q2(p.best[2])}</span></div>` : `<div></div>`;
      h += `<a class="match" href="partita.html?f=${fonte}&id=${encodeURIComponent(p.id)}"><div class="h">${fmtOra(p.inizio)}</div><div><div class="t">${esc(p.casa)} – ${esc(p.ospite)}</div><div class="m">${p.nm} mercati · ${p.nb} bookmaker</div></div>${b}</a>`;
    }
    main.innerHTML = h;
  }

  /* ricerca squadra con suggerimenti */
  const inp = $("#q"), sug = $("#sugg"), clr = $("#clr");
  let act = -1;
  function suggest() {
    clr.style.display = inp.value ? "block" : "none";
    const d = cache[fonte]; const q = norm(inp.value.trim());
    if (!d || q.length < 2) { sug.style.display = "none"; return; }
    const res = d.partite.filter(p => norm(p.casa + " " + p.ospite).includes(q) && new Date(p.inizio) > Date.now() - 5 * 60e3).slice(0, 8);
    act = -1;
    sug.innerHTML = res.map(p => `<button data-id="${esc(p.id)}">${esc(p.casa)} – ${esc(p.ospite)}<div class="s2">${esc(p.comp)} · ${fmtDT(p.inizio)}</div></button>`).join("") || `<button disabled>Nessuna squadra trovata</button>`;
    sug.style.display = "block";
    sug.querySelectorAll("button[data-id]").forEach(b => b.onclick = () => location.href = `partita.html?f=${fonte}&id=${encodeURIComponent(b.dataset.id)}`);
  }
  inp.addEventListener("input", () => { suggest(); draw(); });
  inp.addEventListener("keydown", e => {
    const bs = [...sug.querySelectorAll("button[data-id]")]; if (!bs.length) return;
    if (e.key === "ArrowDown") { act = Math.min(bs.length - 1, act + 1); e.preventDefault(); }
    else if (e.key === "ArrowUp") { act = Math.max(0, act - 1); e.preventDefault(); }
    else if (e.key === "Enter") { (bs[act] || bs[0]).click(); return; }
    else if (e.key === "Escape") { sug.style.display = "none"; return; }
    bs.forEach((b, i) => b.classList.toggle("act", i === act));
  });
  document.addEventListener("click", e => { if (!e.target.closest(".search")) sug.style.display = "none"; });
  clr.onclick = () => { inp.value = ""; suggest(); draw(); inp.focus(); };
  load();
}

/* ===================================================== PARTITA ===================================================== */
const CHIPS = [
  { t: "Tutti" },
  { t: "1X2", re: /^(vincente|esito finale)$/ },
  { t: "Doppia chance", re: /^doppia chance$/ },
  { t: "U/O 0.5", re: /^(under\/over|goal totali)$/, l: "0.5" },
  { t: "U/O 1.5", re: /^(under\/over|goal totali)$/, l: "1.5" },
  { t: "U/O 2.5", re: /^(under\/over|goal totali)$/, l: "2.5" },
  { t: "U/O 3.5", re: /^(under\/over|goal totali)$/, l: "3.5" },
  { t: "U/O 4.5", re: /^(under\/over|goal totali)$/, l: "4.5" },
  { t: "Under/Over (tutti)", re: /(under\/over|goal totali|totale gol|gol totali)/ },
  { t: "Goal/NoGoal", re: /^(gol\/no gol|entrambe squadre segnano)$/ },
  { t: "Risultato esatto", re: /risultato esatto/ },
  { t: "Primo tempo", re: /(1° tempo|1º tempo|primo tempo|1 tempo)/ },
  { t: "Secondo tempo", re: /(2° tempo|2º tempo|secondo tempo|2 tempo)/ },
  { t: "Parziale/Finale", re: /(parziale \/ finale|esito parziale)/ },
  { t: "Handicap", re: /handicap/ },
  { t: "Rimborso se pari (DNB)", re: /(rimborso|draw no bet)/ },
  { t: "Combo", re: /( e over| e under|esito finale e|gol\/no gol e|entrambe a segno e|doppia chance e)/ },
  { t: "Pari/Dispari", re: /(pari|dispari)/ },
  { t: "Marcatori", re: /(marcatore|segnare|tripletta)/ },
  { t: "Calci d'angolo", re: /(angolo|corner)/ },
  { t: "Cartellini", re: /cartellin/ },
];
const numLine = l => { const m = String(l).match(/-?\d+(\.\d+)?/); return m ? parseFloat(m[0]) : 0; };

async function partita() {
  setupFab();
  const fonte = FONTI[qs.get("f")] ? qs.get("f") : "oddschecker";
  const id = qs.get("id");
  $("#back").href = "index.html?f=" + fonte;
  let d, idx;
  try { [d, idx] = await Promise.all([getJSON(`data/${fonte}/p/${encodeURIComponent(id)}.json`), getJSON(`data/${fonte}/index.json`).catch(() => null)]); }
  catch (e) { $("#main").innerHTML = `<div class="status ko">⛔ Partita non trovata o non più disponibile (forse è già iniziata). <a href="index.html?f=${fonte}">Torna all'elenco</a></div>`; return; }
  document.title = `${d.casa} – ${d.ospite} · Quote ${FONTI[fonte].nome}`;
  $("#mh").innerHTML = `<div class="c">${esc(d.comp)} · fonte ${esc(FONTI[fonte].nome)} Italia</div><h2>${esc(d.casa)} – ${esc(d.ospite)}</h2><div class="w">${esc(fmtDT(d.inizio))} · ${d.mercati.length} mercati · ${Object.keys(d.bk).length} bookmaker · <a href="${esc(d.url)}" target="_blank" rel="noopener">vedi su ${esc(FONTI[fonte].nome)} ↗</a></div>` + (idx ? statoHTML(idx.stato, fonte) : "");
  d.mercati.forEach((m, i) => m.i = i);
  const sel = $("#msel");
  sel.innerHTML = `<option value="">— Scegli il tipo di scommessa (${d.mercati.length}) —</option>` + d.mercati.map(m => `<option value="${m.i}">${esc(m.n)}</option>`).join("");
  let chip = 0, only = null;
  const chipsEl = $("#mchips");
  const avail = CHIPS.map((c, i) => i === 0 || d.mercati.some(m => c.re.test(norm(m.n)) && (!c.l || m.s.some(s => s.l === c.l))));
  chipsEl.innerHTML = CHIPS.map((c, i) => avail[i] ? `<button class="chip ${i === chip ? "on" : ""}" data-i="${i}">${esc(c.t)}</button>` : "").join("");
  chipsEl.querySelectorAll(".chip").forEach(b => b.onclick = () => { chip = +b.dataset.i; only = null; sel.value = ""; chipsEl.querySelectorAll(".chip").forEach(x => x.classList.toggle("on", x === b)); draw(); });
  sel.onchange = () => { only = sel.value === "" ? null : +sel.value; if (only != null) { chip = 0; chipsEl.querySelectorAll(".chip").forEach((x, i) => x.classList.toggle("on", i === 0)); $("#mq").value = ""; } draw(); };
  $("#mq").addEventListener("input", () => { only = null; sel.value = ""; draw(); });
  $("#mclr").onclick = () => { $("#mq").value = ""; draw(); };

  function tabella(m, sels) {
    const bks = Object.keys(d.bk).filter(b => sels.some(s => s.o[b] != null));
    const best = sels.map(s => Math.max(...Object.values(s.o)));
    const bestBk = sels.map((s, i) => Object.keys(s.o).filter(b => s.o[b] === best[i]));
    const bname = b => (d.bk[b] || {}).n || b;
    const dot = b => `<span class="dot" style="background:${esc((d.bk[b] || {}).c || "#666")}"></span>`;
    if (sels.length <= 3) {
      // righe = bookmaker, colonne = esiti
      bks.sort((a, b) => (sels[0].o[b] || 0) - (sels[0].o[a] || 0) || bname(a).localeCompare(bname(b)));
      let h = `<table><thead><tr><th class="k">Bookmaker</th>${sels.map(s => `<th>${esc(s.n)}</th>`).join("")}</tr></thead><tbody>`;
      h += `<tr class="bestrow"><td class="k">★ Quota migliore</td>${sels.map((s, i) => `<td>${q2(best[i])}<span class="bn">${esc(bestBk[i].map(bname).slice(0, 2).join(", "))}${bestBk[i].length > 2 ? " +" + (bestBk[i].length - 2) : ""}</span></td>`).join("")}</tr>`;
      for (const b of bks) h += `<tr><td class="k">${dot(b)}${esc(bname(b))}</td>${sels.map((s, i) => { const v = s.o[b]; return `<td class="${v != null && v === best[i] ? "best" : ""}">${v != null ? q2(v) : "–"}</td>`; }).join("")}</tr>`;
      if (sels.length >= 2) {
        const marg = best.reduce((a, v) => a + 1 / v, 0);
        h += `<tr><td class="k" style="color:var(--mut)">Payout con quote migliori</td><td colspan="${sels.length}" style="color:${marg < 1 ? "var(--best)" : "var(--mut)"}">${(100 / marg).toFixed(1)}%${marg < 1 ? " (surebet!)" : ""}</td></tr>`;
      }
      return h + `</tbody></table>`;
    }
    // righe = esiti, colonne = migliore + bookmaker
    bks.sort((a, b) => bname(a).localeCompare(bname(b)));
    let h = `<table><thead><tr><th class="k">Esito</th><th>★ Migliore</th>${bks.map(b => `<th>${dot(b)}${esc(bname(b))}</th>`).join("")}</tr></thead><tbody>`;
    sels.forEach((s, i) => {
      h += `<tr><td class="k">${esc(s.n)}</td><td class="bestc">${q2(best[i])}<span class="bn">${esc(bestBk[i].map(bname).slice(0, 1).join(""))}${bestBk[i].length > 1 ? " +" + (bestBk[i].length - 1) : ""}</span></td>${bks.map(b => { const v = s.o[b]; return `<td class="${v != null && v === best[i] ? "best" : ""}">${v != null ? q2(v) : "–"}</td>`; }).join("")}</tr>`;
    });
    return h + `</tbody></table>`;
  }

  function mercatoHTML(m, open, lineFilter, q) {
    let sels = m.s;
    if (q && !norm(m.n).includes(q)) sels = sels.filter(s => norm(s.n + " " + (s.l || "")).includes(q));
    if (lineFilter) sels = sels.filter(s => s.l === lineFilter);
    if (!sels.length) return "";
    let body = "";
    if (sels.some(s => s.l)) {
      const groups = new Map();
      for (const s of sels) { const k = s.l || ""; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(s); }
      const keys = [...groups.keys()].sort((a, b) => numLine(a) - numLine(b) || a.localeCompare(b));
      for (const k of keys) body += (k ? `<div class="ln">Linea ${esc(k)}</div>` : "") + `<div class="tw">${tabella(m, groups.get(k))}</div>`;
    } else body = `<div class="tw">${tabella(m, sels)}</div>`;
    return `<details class="mk" ${open ? "open" : ""}><summary><span>${esc(m.n)} <span class="g">${esc(m.g && m.g !== "Oddspedia" ? "· " + m.g : "")}</span></span></summary>${body}</details>`;
  }

  function draw() {
    const q = norm($("#mq").value.trim());
    $("#mclr").style.display = q ? "block" : "none";
    let list = d.mercati, lf = null;
    if (only != null) list = [d.mercati[only]];
    else {
      const c = CHIPS[chip];
      if (c.re) { list = list.filter(m => c.re.test(norm(m.n))); lf = c.l || null; if (lf) list = list.filter(m => m.s.some(s => s.l === lf)); }
      if (q) list = list.filter(m => norm(m.n).includes(q) || m.s.some(s => norm(s.n + " " + (s.l || "")).includes(q)));
    }
    const h = list.map((m, i) => mercatoHTML(m, only != null || i < 4 || list.length <= 6, lf, q)).join("");
    $("#markets").innerHTML = h || `<div class="empty">Nessun tipo di scommessa trovato. Prova con un'altra parola o scegli dal menu.</div>`;
  }
  draw();
}
