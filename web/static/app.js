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
    if (ai.tokens_remaining !== null && ai.tokens_remaining !== undefined) {
      $("acc-ai").textContent = Number(ai.tokens_remaining).toLocaleString("en-US");
    } else if (ai.active) {
      $("acc-ai-label").textContent = "Provider AI Aktif";
      $("acc-ai").textContent = ai.active.provider + " · " + ai.active.model;
    } else {
      $("acc-ai").textContent = "—";
    }
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
    chartSymbol = s.symbol;
    $("chart-block").hidden = false;
    $("backtest-block").hidden = false;
    $("chart-title").textContent = "Grafik Candlestick — " + s.symbol;
    loadChart(s.symbol);
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
  tbody.innerHTML = '<tr><td colspan="7" class="muted">Memindai semua simbol, mohon tunggu…</td></tr>';
  try {
    const useAi = $("scan-ai").checked;
    const data = await api("/api/scan?top=15&ai=" + useAi);
    const rows = data.results || [];
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="muted">Tidak ada sinyal saat ini.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map((s) => `<tr>
      <td><button class="sym-link" onclick="openDetail('${s.symbol}')">${s.symbol}<span class="sym-arrow">›</span></button></td>
      <td class="${sideClass(s.side)}">${s.side}</td>
      <td>${fmtPrice(s.price)}</td>
      <td>${s.confidence}%</td>
      <td>${s.technical_score}</td>
      <td>${fmtPrice(s.stop_loss)}</td>
      <td>${fmtPrice(s.take_profit)}</td>
    </tr>`).join("");
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="7" class="neg">Gagal memindai: ' + e.message + "</td></tr>";
  } finally {
    btn.disabled = false;
    btn.textContent = "Pindai Sekarang";
  }
}

/* --------- buka detail simbol (dari pemindai / tombol analisis) ---------
   Satu pintu masuk untuk memuat detail: menampilkan animasi loading di
   panel analisis dan mengabaikan ketukan berikutnya selama pemuatan
   berjalan, sehingga detail simbol tidak pernah terinput dua kali. */
let detailInFlight = null;

async function openDetail(symbol) {
  symbol = (symbol || "").trim().toUpperCase();
  if (!symbol || detailInFlight) return;
  detailInFlight = symbol;
  $("symbol-input").value = symbol;
  $("scan-table").classList.add("is-loading");
  const overlay = $("detail-loading");
  $("detail-loading-symbol").textContent = symbol;
  overlay.hidden = false;
  $("panel-analysis").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    await analyze(symbol);
  } finally {
    overlay.hidden = true;
    $("scan-table").classList.remove("is-loading");
    detailInFlight = null;
  }
}

/* ---------------- grafik candlestick ---------------- */
let chartSymbol = null;
let lastCandles = [];

function emaSeries(values, period) {
  const k = 2 / (period + 1);
  const out = [];
  let prev = null;
  for (const v of values) {
    prev = prev === null ? v : (v - prev) * k + prev;
    out.push(prev);
  }
  return out;
}

async function loadChart(symbol) {
  try {
    const interval = $("chart-interval").value;
    const data = await api("/api/klines/" + encodeURIComponent(symbol) + "?interval=" + interval + "&limit=120");
    lastCandles = data.candles || [];
    drawCandles();
  } catch (e) {
    toast("Gagal memuat grafik: " + e.message);
  }
}

function drawCandles() {
  const canvas = $("candle-chart");
  if (!canvas || !lastCandles.length) return;
  const wrap = canvas.parentElement;
  const dpr = window.devicePixelRatio || 1;
  const W = wrap.clientWidth, H = wrap.clientHeight;
  canvas.width = W * dpr; canvas.height = H * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);

  const candles = lastCandles;
  const labelW = 64;                       // ruang label harga di kanan
  const plotW = W - labelW;
  const volH = Math.round(H * 0.16);       // area volume di bawah
  const priceH = H - volH - 16;

  let hi = -Infinity, lo = Infinity, vmax = 0;
  for (const c of candles) {
    hi = Math.max(hi, c.high); lo = Math.min(lo, c.low); vmax = Math.max(vmax, c.volume);
  }
  const pad = (hi - lo) * 0.06 || 1;
  hi += pad; lo -= pad;
  const y = (p) => 10 + priceH - ((p - lo) / (hi - lo)) * priceH;
  const step = plotW / candles.length;
  const bodyW = Math.max(1, Math.floor(step * 0.65));

  // grid + label harga
  ctx.strokeStyle = "rgba(132,142,156,.18)";
  ctx.fillStyle = "#848e9c";
  ctx.font = "10px sans-serif";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const p = lo + ((hi - lo) * i) / 4;
    const yy = y(p);
    ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(plotW, yy); ctx.stroke();
    ctx.fillText(fmtPrice(p), plotW + 6, yy + 3);
  }

  // volume
  candles.forEach((c, i) => {
    const x = i * step + step / 2;
    const up = c.close >= c.open;
    ctx.fillStyle = up ? "rgba(14,203,129,.45)" : "rgba(246,70,93,.45)";
    const vh = vmax ? (c.volume / vmax) * volH : 0;
    ctx.fillRect(x - bodyW / 2, H - vh, bodyW, vh);
  });

  // candle
  candles.forEach((c, i) => {
    const x = i * step + step / 2;
    const up = c.close >= c.open;
    ctx.strokeStyle = up ? "#0ecb81" : "#f6465d";
    ctx.fillStyle = up ? "#0ecb81" : "#f6465d";
    ctx.beginPath(); ctx.moveTo(x, y(c.high)); ctx.lineTo(x, y(c.low)); ctx.stroke();
    const top = y(Math.max(c.open, c.close));
    const bottom = y(Math.min(c.open, c.close));
    ctx.fillRect(x - bodyW / 2, top, bodyW, Math.max(1, bottom - top));
  });

  // garis EMA20
  const ema = emaSeries(candles.map((c) => c.close), 20);
  ctx.strokeStyle = "#f0b90b";
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  ema.forEach((v, i) => {
    const x = i * step + step / 2;
    if (i === 0) ctx.moveTo(x, y(v)); else ctx.lineTo(x, y(v));
  });
  ctx.stroke();

  // label harga terakhir
  const last = candles[candles.length - 1];
  ctx.fillStyle = last.close >= last.open ? "#0ecb81" : "#f6465d";
  ctx.font = "bold 11px sans-serif";
  ctx.fillText(fmtPrice(last.close), plotW + 6, y(last.close) + 3);
}

$("chart-interval").addEventListener("change", () => { if (chartSymbol) loadChart(chartSymbol); });
window.addEventListener("resize", () => { drawCandles(); drawEquity(); });

/* ---------------- backtest ---------------- */
let lastEquity = [];

async function runBacktest() {
  if (!chartSymbol) return;
  const btn = $("btn-backtest");
  btn.disabled = true;
  $("backtest-stats").textContent = "Menjalankan backtest…";
  try {
    const interval = $("chart-interval").value;
    const d = await api("/api/backtest/" + encodeURIComponent(chartSymbol) + "?interval=" + interval + "&limit=1000");
    const pf = d.profit_factor === null ? "∞" : d.profit_factor;
    $("backtest-stats").innerHTML =
      '<div class="kv"><span>Saldo awal → akhir</span><b>' + fmtUsd(d.initial_balance) + " → " + fmtUsd(d.final_balance) + '</b></div>' +
      '<div class="kv"><span>Return</span><b class="' + (d.return_pct >= 0 ? "pos" : "neg") + '">' + (d.return_pct >= 0 ? "+" : "") + d.return_pct + "%" + '</b></div>' +
      '<div class="kv"><span>Trade</span><b>' + d.trades_count + " (" + d.wins + " menang / " + d.losses + ' kalah)</b></div>' +
      '<div class="kv"><span>Win rate</span><b>' + d.win_rate + '%</b></div>' +
      '<div class="kv"><span>Profit factor</span><b>' + pf + '</b></div>' +
      '<div class="kv"><span>Max drawdown</span><b class="neg">' + d.max_drawdown_pct + '%</b></div>' +
      '<p class="muted small-note">Atas ' + d.candles + ' candle ' + d.interval + ' · risiko 1%/trade · fee taker 0,05%/sisi · SL 1,5×ATR / TP 2,5×ATR. Hasil masa lalu bukan jaminan hasil mendatang.</p>';
    lastEquity = d.equity_curve || [];
    $("equity-wrap").hidden = lastEquity.length < 2;
    drawEquity();
    const rows = (d.trades || []).slice(-10).reverse().map((t) =>
      "<tr><td>" + t.side + "</td><td>" + fmtPrice(t.entry_price) + "</td><td>" + fmtPrice(t.exit_price) +
      "</td><td>" + t.exit_reason + '</td><td class="' + (t.pnl >= 0 ? "pos" : "neg") + '">' + fmtUsd(t.pnl) + "</td></tr>"
    ).join("");
    $("backtest-trades").innerHTML = rows
      ? '<table style="margin-top:12px"><thead><tr><th>Sisi</th><th>Entri</th><th>Keluar</th><th>Alasan</th><th>PnL</th></tr></thead><tbody>' + rows + "</tbody></table>"
      : "";
  } catch (e) {
    $("backtest-stats").textContent = "Backtest gagal: " + e.message;
  } finally {
    btn.disabled = false;
  }
}

function drawEquity() {
  const canvas = $("equity-chart");
  if (!canvas || lastEquity.length < 2 || $("equity-wrap").hidden) return;
  const wrap = canvas.parentElement;
  const dpr = window.devicePixelRatio || 1;
  const W = wrap.clientWidth, H = wrap.clientHeight;
  canvas.width = W * dpr; canvas.height = H * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, W, H);
  const vals = lastEquity.map((p) => p.equity);
  let hi = Math.max(...vals), lo = Math.min(...vals);
  const pad = (hi - lo) * 0.1 || 1; hi += pad; lo -= pad;
  const x = (i) => (i / (vals.length - 1)) * (W - 70);
  const y = (v) => 9 + (H - 18) - ((v - lo) / (hi - lo)) * (H - 18);
  ctx.strokeStyle = "rgba(132,142,156,.18)";
  ctx.fillStyle = "#848e9c"; ctx.font = "10px sans-serif";
  for (let i = 0; i <= 3; i++) {
    const v = lo + ((hi - lo) * i) / 3, yy = y(v);
    ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(W - 70, yy); ctx.stroke();
    ctx.fillText(fmtUsd(v), W - 64, yy + 3);
  }
  const rising = vals[vals.length - 1] >= vals[0];
  ctx.strokeStyle = rising ? "#0ecb81" : "#f6465d";
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  vals.forEach((v, i) => { if (i === 0) ctx.moveTo(x(i), y(v)); else ctx.lineTo(x(i), y(v)); });
  ctx.stroke();
}

$("btn-backtest").addEventListener("click", runBacktest);

/* ---------------- tautan langsung (deep link) ---------------- */
// Contoh: /?symbol=BTCUSDT&backtest=1 — langsung menganalisis simbol tersebut
// (dan menjalankan backtest bila diminta). Berguna untuk membagikan tampilan.
(function () {
  const params = new URLSearchParams(window.location.search);
  const symbol = (params.get("symbol") || "").toUpperCase();
  if (symbol) {
    openDetail(symbol).then(() => {
      if (params.get("backtest") === "1") runBacktest();
    });
  }
})();

/* ---------------- ikat event ---------------- */
$("btn-analyze").addEventListener("click", () => {
  openDetail($("symbol-input").value);
});
$("symbol-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("btn-analyze").click();
});
$("btn-scan").addEventListener("click", scan);
$("btn-refresh").addEventListener("click", loadAccount);

loadConfig();
loadSymbols();
loadAccount();
