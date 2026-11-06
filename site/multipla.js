(() => {
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const norm = s => String(s || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g,"");
const q2 = n => Number(n).toFixed(2);
let source = "oddschecker", mode = "multi", indexData = null, matches = [], chosen = null, detail = null, legs = [];
let marketItems = [];
const cache = new Map();
const modeMulti = $("#mode-multi"), modeSame = $("#mode-same");
function matchLabel(p) {
  const dt = new Date(p.inizio);
  const when = isNaN(dt) ? "" : dt.toLocaleString("it-IT",{weekday:"short",day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});
  return p.casa + " – " + p.ospite + " · " + p.comp + (when ? " · " + when : "");
}
function setMode(next) {
  mode = next; legs = []; chosen = null; detail = null;
  modeMulti.classList.toggle("on", mode === "multi");
  modeSame.classList.toggle("on", mode === "same");
  $("#match-search").value = ""; $("#match-suggestions").innerHTML = "";
  $("#selected-match").innerHTML = ""; $("#market").innerHTML = '<option value="">Prima scegli una partita</option>';
  $("#market").disabled = true; $("#outcome").innerHTML = '<option value="">Scegli il mercato</option>'; $("#outcome").disabled = true;
  $("#same-lock").textContent = mode === "same" ? "Scegli una partita: tutte le selezioni aggiunte resteranno legate a quella partita." : "Puoi aggiungere selezioni di partite diverse.";
  render();
}
async function loadSource() {
  source = $("#source").value;
  chosen = null; detail = null; legs = []; cache.clear();
  $("#match-search").value = ""; $("#match-suggestions").innerHTML = ""; $("#selected-match").innerHTML = "";
  $("#market").disabled = true; $("#market").innerHTML = '<option value="">Caricamento partite…</option>';
  $("#outcome").disabled = true; $("#outcome").innerHTML = '<option value="">Scegli il mercato</option>';
  $("#add-leg").disabled = true; render();
  try {
    const r = await fetch("data/" + source + "/index.json?t=" + Date.now(), {cache:"no-store"});
    if (!r.ok) throw new Error("HTTP " + r.status);
    indexData = await r.json();
    matches = (indexData.partite || []).filter(p => new Date(p.inizio).getTime() > Date.now() - 5*60*1000);
    $("#market").innerHTML = '<option value="">Prima scegli una partita</option>';
    $("#same-lock").textContent = matches.length ? (mode === "same" ? "Scegli una partita: tutte le selezioni aggiunte resteranno legate a quella partita." : "Puoi aggiungere selezioni di partite diverse.") : "Nessuna partita disponibile per questa fonte.";
  } catch(e) {
    indexData = null; matches = [];
    $("#same-lock").textContent = "Impossibile caricare le partite (" + e.message + "). Riprova o cambia fonte.";
  }
  render();
}
function showSuggestions() {
  const q = norm($("#match-search").value.trim()), box = $("#match-suggestions");
  if (mode === "same" && legs.length && chosen) { box.innerHTML = ""; return; }
  const found = matches.filter(p => !q || norm(p.casa + " " + p.ospite + " " + p.comp).includes(q)).slice(0,30);
  box.innerHTML = found.map(p => '<button type="button" data-id="' + esc(p.id) + '">' + esc(p.casa + " – " + p.ospite) + '<small>' + esc(p.comp + " · " + new Date(p.inizio).toLocaleString("it-IT",{weekday:"short",day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"})) + '</small></button>').join("") || '<div class="gm-note">Nessuna partita trovata.</div>';
  box.querySelectorAll("button[data-id]").forEach(b => b.addEventListener("click", () => chooseMatch(b.dataset.id)));
}
async function chooseMatch(id) {
  if (mode === "same" && legs.length && chosen && chosen.id !== id) return;
  const p = matches.find(x => String(x.id) === String(id));
  if (!p) return;
  chosen = p; detail = null;
  $("#selected-match").innerHTML = '<div class="gm-selected">⏳ Caricamento quote: ' + esc(p.casa + " – " + p.ospite) + '</div>';
  $("#match-suggestions").innerHTML = "";
  $("#market").disabled = true; $("#outcome").disabled = true; $("#add-leg").disabled = true;
  try {
    if (!cache.has(id)) {
      const r = await fetch("data/" + source + "/p/" + encodeURIComponent(id) + ".json?t=" + Date.now(), {cache:"no-store"});
      if (!r.ok) throw new Error("HTTP " + r.status);
      cache.set(id, await r.json());
    }
    detail = cache.get(id);
    marketItems = [];
    (detail.mercati || []).forEach((m, mi) => (m.s || []).forEach((s, si) => {
      marketItems.push({mi:mi,si:si,market:m,selection:s,label:m.n + (s.l ? " · " + s.l : "") + " — " + s.n});
    }));
    const marketMap = [];
    marketItems.forEach(x => { if (!marketMap.some(y => y.mi === x.mi)) marketMap.push({mi:x.mi,n:x.market.n}); });
    $("#market").innerHTML = '<option value="">— Seleziona mercato —</option>' + marketMap.map(x => '<option value="' + x.mi + '">' + esc(x.n) + '</option>').join("");
    $("#market").disabled = false;
    $("#outcome").innerHTML = '<option value="">— Seleziona esito —</option>';
    $("#outcome").disabled = true;
    $("#selected-match").innerHTML = '<div class="gm-selected">' + esc(p.casa + " – " + p.ospite) + ' <small style="color:var(--mut);font-weight:400">· ' + esc(p.comp) + '</small></div>';
    if (mode === "same") $("#match-search").value = p.casa + " – " + p.ospite;
    $("#add-leg").disabled = true;
  } catch(e) {
    $("#selected-match").innerHTML = '<div class="status ko">Non riesco a leggere le quote di questa partita: ' + esc(e.message) + '</div>';
  }
  render();
}
function updateOutcomes() {
  const mi = $("#market").value;
  if (mi === "") { $("#outcome").disabled = true; $("#outcome").innerHTML = '<option value="">Scegli prima un mercato</option>'; $("#add-leg").disabled = true; return; }
  const opts = marketItems.filter(x => String(x.mi) === String(mi));
  $("#outcome").innerHTML = '<option value="">— Seleziona esito —</option>' + opts.map(x => '<option value="' + x.si + '">' + esc(x.label.replace(x.market.n + (x.selection.l ? " · " + x.selection.l : "") + " — ","")) + '</option>').join("");
  $("#outcome").disabled = false; $("#add-leg").disabled = true;
}
function addLeg() {
  if (!detail || !chosen || $("#market").value === "" || $("#outcome").value === "") return;
  const mi = Number($("#market").value), si = Number($("#outcome").value);
  const market = detail.mercati[mi], selection = market && market.s[si];
  if (!selection || !selection.o || !Object.keys(selection.o).length) return;
  legs.push({
    matchId: chosen.id, match: chosen.casa + " – " + chosen.ospite, competition: chosen.comp,
    market: market.n + (selection.l ? " · " + selection.l : ""), outcome: selection.n,
    odds: Object.assign({}, selection.o), books: Object.assign({}, detail.bk || {})
  });
  if (mode === "multi") {
    chosen = null; detail = null;
    $("#match-search").value = ""; $("#selected-match").innerHTML = "";
    $("#market").innerHTML = '<option value="">Prima scegli una partita</option>'; $("#market").disabled = true;
    $("#outcome").innerHTML = '<option value="">Scegli il mercato</option>'; $("#outcome").disabled = true;
    $("#add-leg").disabled = true;
  } else {
    $("#market").value = ""; $("#outcome").innerHTML = '<option value="">Scegli il mercato</option>'; $("#outcome").disabled = true; $("#add-leg").disabled = true;
  }
  if (mode === "same" && chosen) {
    $("#match-search").disabled = true;
    $("#same-lock").textContent = "Partita bloccata: " + chosen.casa + " – " + chosen.ospite + ". Per cambiarla, svuota le selezioni.";
  }
  render();
}
function removeLeg(i) {
  legs.splice(i,1);
  if (mode === "same" && !legs.length) {
    $("#match-search").disabled = false; chosen = null; detail = null;
    $("#match-search").value = ""; $("#selected-match").innerHTML = "";
    $("#market").innerHTML = '<option value="">Prima scegli una partita</option>'; $("#market").disabled = true;
    $("#outcome").innerHTML = '<option value="">Scegli il mercato</option>'; $("#outcome").disabled = true;
    $("#same-lock").textContent = "Scegli una partita: tutte le selezioni aggiunte resteranno legate a quella partita.";
  }
  render();
}
function render() {
  $("#leg-count").textContent = legs.length ? "(" + legs.length + ")" : "";
  $("#legs").innerHTML = legs.length ? legs.map((x,i) => '<div class="gm-leg"><div><strong>' + esc(x.match) + '</strong><small>' + esc(x.competition + " · " + x.market + " · " + x.outcome) + '</small></div><button class="gm-remove" data-remove="' + i + '" type="button">Rimuovi</button></div>').join("") : '<div class="empty">Non hai ancora aggiunto selezioni.</div>';
  $("#legs").querySelectorAll("[data-remove]").forEach(b => b.addEventListener("click", () => removeLeg(Number(b.dataset.remove))));
  const out = $("#results");
  if (!legs.length) { out.innerHTML = '<div class="empty">Aggiungi almeno una selezione per confrontare i bookmaker.</div>'; return; }
  let common = Object.keys(legs[0].odds);
  for (let i=1;i<legs.length;i++) common = common.filter(b => legs[i].odds[b] != null);
  const rows = common.map(code => {
    const values = legs.map(x => Number(x.odds[code]));
    return {code:code,name:(legs.find(x=>x.books[code]) || {}).books[code] ? legs.find(x=>x.books[code]).books[code].n : code,values:values,total:values.reduce((a,v)=>a*v,1)};
  }).sort((a,b)=>b.total-a.total);
  if (!rows.length) { out.innerHTML = '<div class="status ko">Nessun bookmaker presente su tutte le selezioni. Prova a cambiare una partita, un mercato o la fonte.</div>'; return; }
  const head = '<div class="gm-note">' + rows.length + ' bookmaker offrono tutte le ' + legs.length + ' selezioni. Ordinamento dalla quota totale più alta alla più bassa.</div><div class="gm-results"><table><thead><tr><th>Bookmaker</th>' + legs.map((x,i)=>'<th>Quota ' + (i+1) + '</th>').join("") + '<th>Quota totale</th></tr></thead><tbody>';
  const body = rows.map((r,i)=>'<tr class="' + (i===0?'gm-top':'') + '"><td class="k">' + (i===0?'★ ':'') + esc(r.name) + '</td>' + r.values.map(v=>'<td>' + q2(v) + '</td>').join("") + '<td class="gm-total">' + q2(r.total) + '</td></tr>').join("");
  out.innerHTML = head + body + '</tbody></table></div>';
}
modeMulti.addEventListener("click",()=>setMode("multi"));
modeSame.addEventListener("click",()=>setMode("same"));
$("#source").addEventListener("change",loadSource);
$("#match-search").addEventListener("input",showSuggestions);
$("#market").addEventListener("change",updateOutcomes);
$("#outcome").addEventListener("change",()=>$("#add-leg").disabled = $("#outcome").value === "");
$("#add-leg").addEventListener("click",addLeg);
$("#clear-all").addEventListener("click",()=>{
  legs=[]; chosen=null; detail=null; $("#match-search").disabled=false; $("#match-search").value="";
  $("#match-suggestions").innerHTML=""; $("#selected-match").innerHTML="";
  $("#market").innerHTML='<option value="">Prima scegli una partita</option>'; $("#market").disabled=true;
  $("#outcome").innerHTML='<option value="">Scegli il mercato</option>'; $("#outcome").disabled=true; $("#add-leg").disabled=true;
  $("#same-lock").textContent=mode==="same"?"Scegli una partita: tutte le selezioni aggiunte resteranno legate a quella partita.":"Puoi aggiungere selezioni di partite diverse.";
  render();
});
setMode("multi"); loadSource();
})();