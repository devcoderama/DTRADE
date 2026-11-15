/* binance-ai-future — logika frontend dashboard (vanilla JS, tanpa build step) */
"use strict";

const $ = (id) => document.getElementById(id);

function fmtPrice(v) {
  if (v === null || v === undefined) return "—";
  const a = Math.abs(v);
  const d = a >= 1000 ? 2 : a >= 1 ? 4 : a >= 0.01 ? 6 : 8;
  return Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}
function fmtUsd(v) {
  if (v === null || v === undefined) return "—";
  return Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " USDT";
}
function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 4200);
}
async function api(path, options) {
  const resp = await fetch(path, options);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || ("HTTP " + resp.status));
  return data;
}
function sideClass(side) {
  return side === "LONG" ? "side-long" : side === "SHORT" ? "side-short" : "side-neutral";
}

/* ---------------- muat awal ---------------- */
async function loadConfig() {
  try {
    const cfg = await api("/api/config");
    $("badge-network").textContent = cfg.binance_network.toUpperCase();
    const mode = $("badge-mode");
    mode.textContent = cfg.trading_mode === "live" ? "● LIVE" : "DRY-RUN (simulasi)";
    mode.className = "badge " + (cfg.trading_mode === "live" ? "live" : "dry");
    const ai = $("badge-ai");
    ai.textContent = cfg.ai_configured ? "AI: " + cfg.ai_model : "AI: belum dikonfigurasi";
    if (cfg.ai_configured) ai.classList.add("ok");
  } catch (e) { /* server belum siap */ }
}

async function loadAccount() {
  try {
    const acc = await api("/api/account");
    $("acc-wallet").textContent = fmtUsd(acc.wallet_balance);
    $("acc-available").textContent = fmtUsd(acc.available);
    const upnl = $("acc-upnl");
    upnl.textContent = fmtUsd(acc.unrealized_pnl);
    upnl.className = acc.unrealized_pnl >= 0 ? "pos" : "neg";
    renderPositions(acc.positions || []);
  } catch (e) {
    $("acc-wallet").textContent = "—";
    toast("Akun Binance belum bisa dibaca: " + e.message);
  }
  try {
    const ai = await api("/api/ai/account");
    $("acc-ai").textContent = Number(ai.tokens_remaining).toLocaleString("en-US");
  } catch (e) { $("acc-ai").textContent = "—"; }
}

async function loadSymbols() {
  try {
    const data = await api("/api/symbols");
    $("symbol-list").innerHTML = data.symbols.map((s) => `<option value="${s}">`).join("");
  } catch (e) { /* abaikan */ }
}

/* ---------------- posisi ---------------- */
function renderPositions(positions) {
  const tbody = document.querySelector("#pos-table tbody");
  if (!positions.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="muted">Tidak ada posisi terbuka.</td></tr>';
    return;
  }
  tbody.innerHTML = positions.map((p) => {
    const amt = parseFloat(p.positionAmt);
    const side = amt > 0 ? "LONG" : "SHORT";
    const upnl = parseFloat(p.unRealizedProfit);
    return `<tr>
      <td>${p.symbol}</td>
      <td class="${sideClass(side)}">${side}</td>
      <td>${Math.abs(amt)}</td>
      <td>${fmtPrice(parseFloat(p.entryPrice))}</td>
      <td>${fmtPrice(parseFloat(p.markPrice))}</td>
      <td class="${upnl >= 0 ? "pos" : "neg"}">${fmtUsd(upnl)}</td>
      <td>${p.leverage}x</td>
      <td><button class="small danger" onclick="closePosition('${p.symbol}')">Tutup</button></td>
    </tr>`;
  }).join("");
}

async function closePosition(symbol) {
  if (!confirm("Tutup posisi " + symbol + " pada harga pasar?")) return;
  try {
    const res = await api("/api/close/" + symbol, { method: "POST" });
    toast(res.dry_run ? "[DRY-RUN] Penutupan " + symbol + " hanya disimulasikan." : "Posisi " + symbol + " ditutup.");
    loadAccount();
  } catch (e) { toast("Gagal menutup: " + e.message); }
}

/* ---------------- analisis ---------------- */
async function analyze(symbol) {
  const box = $("analysis");
  box.innerHTML = '<span class="muted">Menganalisis ' + symbol + " …</span>";
  try {
    const s = await api("/api/analyze/" + encodeURIComponent(symbol));
    const snap = s.snapshot;
    const aiHtml = s.ai
      ? `<div class="kv"><span>AI (${s.ai.model_used})</span><strong class="${sideClass(s.ai.signal)}">${s.ai.signal} · ${s.ai.confidence}%</strong></div>`
      : `<div class="kv"><span>AI</span><strong>—</strong></div>`;
    box.innerHTML = `
      <div class="analysis-grid">
        <div class="kv"><span>Harga</span><strong>${fmtPrice(s.price)}</strong></div>
        <div class="kv"><span>Tren</span><strong>${snap.trend}</strong></div>
        <div class="kv"><span>RSI 14</span><strong>${snap.rsi14 ? snap.rsi14.toFixed(1) : "—"}</strong></div>
        <div class="kv"><span>Skor Teknikal</span><strong>${s.technical_score}</strong></div>
        ${aiHtml}
        <div class="kv"><span>SL / TP</span><strong>${fmtPrice(s.stop_loss)} / ${fmtPrice(s.take_profit)}</strong></div>
      </div>
      <div class="signal-banner ${s.side.toLowerCase()}">
        Sinyal gabungan: <b>${s.side}</b> — keyakinan ${s.confidence}%
        ${s.ai ? "<br><small>" + s.ai.reasoning + "</small>" : ""}
      </div>
      <ul class="reasons">${s.reasons.map((r) => "<li>" + r + "</li>").join("")}</ul>
      <div class="row" style="margin-top:12px">
        <button class="primary" onclick="tradeSymbol('${s.symbol}', null)">Eksekusi Sinyal Ini</button>
        <button onclick="tradeSymbol('${s.symbol}', 'LONG')">Paksa LONG</button>
        <button onclick="tradeSymbol('${s.symbol}', 'SHORT')">Paksa SHORT</button>
      </div>`;
  } catch (e) {
    box.innerHTML = '<span class="neg">Gagal menganalisis: ' + e.message + "</span>";
  }
}

async function tradeSymbol(symbol, side) {
  const label = side ? side + " " + symbol : "sinyal untuk " + symbol;
  if (!confirm("Eksekusi " + label + "?\nOrder tetap melewati RiskManager. Dalam mode LIVE ini order SUNGGUHAN.")) return;
  try {
    const res = await api("/api/trade", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol, side }),
    });
    if (res.executed) {
      toast((res.dry_run ? "[DRY-RUN] " : "[LIVE] ") + (res.notes || []).join(" "));
    } else {
      toast(res.note + " " + (res.reasons || []).join(" "));
    }
    loadAccount();
  } catch (e) { toast("Gagal eksekusi: " + e.message); }
}

/* ---------------- pemindai ---------------- */
async function scan() {
  const btn = $("btn-scan");
  btn.disabled = true;
  btn.textContent = "Memindai…";
  const tbody = document.querySelector("#scan-table tbody");
  tbody.innerHTML = '<tr><td colspan="8" class="muted">Memindai semua simbol, mohon tunggu…</td></tr>';
  try {
    const useAi = $("scan-ai").checked;
    const data = await api("/api/scan?top=15&ai=" + useAi);
    const rows = data.results || [];
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="muted">Tidak ada sinyal saat ini.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map((s) => `<tr>
      <td>${s.symbol}</td>
      <td class="${sideClass(s.side)}">${s.side}</td>
      <td>${fmtPrice(s.price)}</td>
      <td>${s.confidence}%</td>
      <td>${s.technical_score}</td>
      <td>${fmtPrice(s.stop_loss)}</td>
      <td>${fmtPrice(s.take_profit)}</td>
      <td><button class="small" onclick="analyze('${s.symbol}')">Detail</button></td>
    </tr>`).join("");
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="8" class="neg">Gagal memindai: ' + e.message + "</td></tr>";
  } finally {
    btn.disabled = false;
    btn.textContent = "Pindai Sekarang";
  }
}

/* ---------------- ikat event ---------------- */
$("btn-analyze").addEventListener("click", () => {
  const symbol = $("symbol-input").value.trim().toUpperCase();
  if (symbol) analyze(symbol);
});
$("symbol-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("btn-analyze").click();
});
$("btn-scan").addEventListener("click", scan);
$("btn-refresh").addEventListener("click", loadAccount);

loadConfig();
loadSymbols();
loadAccount();
