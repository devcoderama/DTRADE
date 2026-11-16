/* DTRADE — logika frontend dashboard (vanilla JS, tanpa build step) */
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
/* Escape WAJIB untuk semua string dinamis sebelum masuk innerHTML
   (alasan AI, pesan error, simbol, model — semuanya data luar). */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 4200);
}
async function api(path, options, _retried) {
  // Autentikasi HANYA lewat cookie HttpOnly `baf_token` dari /api/auth/login
  // (fetch mengirim cookie same-origin secara bawaan). Token TIDAK disimpan
  // di localStorage — skrip apa pun di halaman tidak boleh bisa membacanya.
  const resp = await fetch(path, options || {});
  const data = await resp.json().catch(() => ({}));
  if (resp.status === 401 && !_retried) {
    // Server mewajibkan token (WEB_API_TOKEN): minta sekali → login → ulangi.
    const entered = window.prompt("Server ini dilindungi token API. Masukkan WEB_API_TOKEN:");
    if (entered) {
      const login = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: entered.trim() }),
      }).catch(() => null);
      if (login && login.ok) return api(path, options, true);
      throw new Error("Token salah — login ditolak server.");
    }
  }
  if (!resp.ok) throw new Error(data.detail || ("HTTP " + resp.status));
  return data;
}
function sideClass(side) {
  return side === "LONG" ? "side-long" : side === "SHORT" ? "side-short" : "side-neutral";
}

/* ---------------- gauge setengah lingkaran (SVG) ---------------- */
const ARC_LEN = 163.4; // panjang busur setengah lingkaran r=52 pada viewBox 120x68

function gaugeCard(id, title, frac, color, value, label) {
  const f = Math.max(0, Math.min(1, frac || 0));
  $(id).innerHTML =
    '<div class="g-title">' + title + ' <i class="fa-solid fa-circle-info g-info"></i></div>' +
    '<div class="gauge-wrap"><svg class="gauge-svg" viewBox="0 0 120 68">' +
    '<path d="M 8 62 A 52 52 0 0 1 112 62" fill="none" stroke="#2b3139" stroke-width="9" stroke-linecap="round"/>' +
    '<path d="M 8 62 A 52 52 0 0 1 112 62" fill="none" stroke="' + color + '" stroke-width="9" stroke-linecap="round" ' +
    'stroke-dasharray="' + ARC_LEN + '" stroke-dashoffset="' + (ARC_LEN * (1 - f)).toFixed(1) + '"/></svg></div>' +
    '<div class="g-value">' + value + '</div><div class="g-label">' + label + "</div>";
}

function renderSentiment(s) {
  let pct, sideLabel;
  if (s.side !== "NEUTRAL") { pct = s.confidence; sideLabel = s.side; }
  else if (s.ai) { pct = s.ai.confidence; sideLabel = s.ai.signal; }
  else {
    pct = Math.abs(s.technical_score);
    sideLabel = s.technical_score > 0 ? "LONG" : s.technical_score < 0 ? "SHORT" : "NEUTRAL";
  }
  pct = Math.max(0, Math.min(100, pct || 0));
  $("sent-arc").style.strokeDashoffset = (ARC_LEN * (1 - pct / 100)).toFixed(1);
  $("sent-pct").textContent = pct + "%";
  const sideEl = $("sent-side");
  sideEl.textContent = sideLabel;
  sideEl.className = sideLabel === "LONG" ? "pos" : sideLabel === "SHORT" ? "neg" : "muted";
  $("sent-position").innerHTML = sideLabel === "LONG"
    ? '<span class="pos">●</span> Net Long'
    : sideLabel === "SHORT" ? '<span class="neg">●</span> Net Short' : '<span class="muted">●</span> Netral';
  $("sent-insight").textContent = s.ai
    ? s.ai.reasoning
    : "Analisis teknikal murni (AI tidak aktif). " + (s.reasons[0] || "");
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
    if (cfg.default_interval) {
      $("chart-interval").value = cfg.default_interval;
      syncTfTabs();
    }
    const bc = $("badge-broadcast");
    if (cfg.broadcast_enabled && cfg.telegram_configured) {
      bc.textContent = "Siaran Otomatis " + cfg.broadcast_interval_minutes + " mnt";
      bc.classList.add("ok");
    } else {
      bc.textContent = "Siaran Nonaktif";
      bc.classList.remove("ok");
    }
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
    $("symbol-list").innerHTML = data.symbols.map((s) => `<option value="${esc(s)}">`).join("");
  } catch (e) { /* abaikan */ }
}

/* ---------------- provider & model AI (runtime) ---------------- */
let aiModelsData = null;

function fillModelOptions(provider) {
  if (!aiModelsData) return;
  const list = $("ai-model-options");
  let models = (aiModelsData.known_models || {})[provider] || [];
  if (provider === "bangunweb" && (aiModelsData.bangunweb_live_models || []).length) {
    models = [...new Set([...aiModelsData.bangunweb_live_models, ...models])];
  }
  list.innerHTML = models.map((m) => `<option value="${esc(m)}">`).join("");
}

async function loadAiPanel() {
  try {
    aiModelsData = await api("/api/ai/models");
    const sel = $("ai-provider");
    sel.innerHTML = (aiModelsData.providers || []).map((p) =>
      `<option value="${esc(p.provider)}" ${p.provider === aiModelsData.current.provider ? "selected" : ""}>` +
      `${p.provider}${p.configured ? "" : " (belum ada kunci)"}</option>`).join("");
    $("ai-model").value = aiModelsData.current.model || "";
    $("ai-current").textContent =
      "Aktif: " + aiModelsData.current.provider + " · " + aiModelsData.current.model +
      (aiModelsData.current.source === "file" ? " · tersimpan" : " · dari .env");
    fillModelOptions(aiModelsData.current.provider);
  } catch (e) { /* panel AI tidak kritis */ }
}

async function applyAiModel() {
  const provider = $("ai-provider").value;
  const model = $("ai-model").value.trim();
  if (!model) { toast("Isi nama model terlebih dahulu."); return; }
  const btn = $("btn-ai-apply");
  btn.disabled = true;
  btn.textContent = "Menerapkan…";
  try {
    const res = await api("/api/ai/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, model }),
    });
    toast(res.warning ? "⚠️ " + res.warning : "🤖 AI diganti ke " + res.provider + " · " + res.model);
    loadAiPanel();
    loadConfig();
  } catch (e) {
    toast("Gagal mengganti model: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Terapkan";
  }
}
$("btn-ai-apply").addEventListener("click", applyAiModel);
$("ai-provider").addEventListener("change", () => {
  fillModelOptions($("ai-provider").value);
  const known = aiModelsData && (aiModelsData.known_models || {})[$("ai-provider").value];
  if (known && known.length) $("ai-model").value = known[0];
});

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
      <td>${esc(p.symbol)}</td>
      <td class="${sideClass(side)}">${side}</td>
      <td>${Math.abs(amt)}</td>
      <td>${fmtPrice(parseFloat(p.entryPrice))}</td>
      <td>${fmtPrice(parseFloat(p.markPrice))}</td>
      <td class="${upnl >= 0 ? "pos" : "neg"}">${fmtUsd(upnl)}</td>
      <td>${esc(p.leverage)}x</td>
      <td><button class="small danger" data-close-symbol="${esc(p.symbol)}">Tutup</button></td>
    </tr>`;
  }).join("");
}

/* Delegasi klik tabel posisi (tanpa inline onclick berisi string simbol). */
document.querySelector("#pos-table tbody").addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-close-symbol]");
  if (btn) closePosition(btn.getAttribute("data-close-symbol"));
});

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
  box.className = "analysis-empty";
  box.innerHTML = '<span class="muted">Menganalisis ' + esc(symbol) + " …</span>";
  try {
    const s = await api("/api/analyze/" + encodeURIComponent(symbol));
    const snap = s.snapshot;

    /* --- ubin metrik (kolom kiri) --- */
    const chg = snap.change_24h_pct || 0;
    const chgHtml = '<span class="delta ' + (chg >= 0 ? "pos" : "neg") + '">' +
      (chg >= 0 ? "▲ +" : "▼ ") + chg.toFixed(2) + "%</span>";
    const trendArrow = snap.trend === "UPTREND" ? "↗" : snap.trend === "DOWNTREND" ? "↘" : "→";
    const trendCls = snap.trend === "UPTREND" ? "pos" : snap.trend === "DOWNTREND" ? "neg" : "";
    const score = s.technical_score;
    const ringFrac = Math.min(1, Math.abs(score) / 100);
    const ringColor = score > 0 ? "#0ecb81" : score < 0 ? "#f6465d" : "#8a94a6";
    const ring = '<svg class="score-ring" width="40" height="40" viewBox="0 0 40 40">' +
      '<circle class="ring-bg" cx="20" cy="20" r="16" fill="none" stroke-width="4.5"/>' +
      '<circle cx="20" cy="20" r="16" fill="none" stroke="' + ringColor + '" stroke-width="4.5" stroke-linecap="round" ' +
      'stroke-dasharray="100.5" stroke-dashoffset="' + (100.5 * (1 - ringFrac)).toFixed(1) + '"/></svg>';
    const aiTile = s.ai
      ? '<div class="t-value ' + (s.ai.signal === "LONG" ? "pos" : s.ai.signal === "SHORT" ? "neg" : "") + '">' +
        esc(s.ai.signal) + " · " + esc(s.ai.confidence) + '%</div><div class="t-sub muted">' + esc(s.ai.model_used) + "</div>"
      : '<div class="t-value">—</div><div class="t-sub muted">AI tidak aktif</div>';
    box.className = "";
    box.innerHTML =
      '<div class="tiles">' +
      '<div class="tile"><span class="t-label">Harga</span><div class="t-value">' + fmtPrice(s.price) + chgHtml + "</div></div>" +
      '<div class="tile"><span class="t-label">Tren</span><div class="t-value ' + trendCls + '">' + esc(snap.trend) +
      ' <span style="float:right">' + trendArrow + "</span></div></div>" +
      '<div class="tile"><span class="t-label">RSI 14</span><div class="t-value">' +
      (snap.rsi14 != null ? snap.rsi14.toFixed(1) : "—") + "</div></div>" +
      '<div class="tile"><span class="t-label">Skor Teknikal</span><div class="tile-flex"><div class="t-value">' +
      score + ' <span class="muted" style="font-size:12px">/ 100</span></div>' + ring + "</div></div>" +
      '<div class="tile"><span class="t-label">AI Signal</span>' + aiTile + "</div>" +
      '<div class="tile"><span class="t-label">SL / TP</span><div class="t-value">' + fmtPrice(s.stop_loss) +
      '</div><div class="t-sub muted">TP: ' + fmtPrice(s.take_profit) + "</div></div>" +
      "</div>";

    /* --- panel sinyal gabungan --- */
    $("signal-panel").innerHTML =
      '<div class="signal-head"><i class="fa-solid fa-circle-check" style="color:var(--green)"></i> Sinyal Gabungan: <span class="pill ' + s.side.toLowerCase() + '">' + esc(s.side) + "</span>" +
      (s.side !== "NEUTRAL" ? '<span class="muted" style="font-size:13px">keyakinan ' + esc(s.confidence) + "%</span>" : "") + "</div>" +
      (s.ai ? '<p class="signal-why">' + esc(s.ai.reasoning) + "</p>" : "") +
      '<ul class="reasons">' + s.reasons.map((r) => "<li>" + esc(r) + "</li>").join("") + "</ul>";

    /* --- gauge indikator --- */
    const histPct = snap.macd_histogram != null && snap.price
      ? (snap.macd_histogram / snap.price) * 100 : null;
    gaugeCard("gauge-macd", "MACD", histPct == null ? 0 : Math.min(1, Math.abs(histPct)),
      histPct != null && histPct < 0 ? "#f6465d" : "#0ecb81",
      histPct == null ? "—" : histPct.toFixed(2) + "%",
      histPct == null ? "—" : histPct >= 0 ? "Positive" : "Negative");
    const rsi = snap.rsi14;
    gaugeCard("gauge-rsi", "RSI", rsi == null ? 0 : rsi / 100, "#a371f7",
      rsi == null ? "—" : rsi.toFixed(1),
      rsi == null ? "—" : rsi < 30 ? "Oversold" : rsi < 40 ? "Lemah" : rsi <= 60 ? "Neutral" : rsi <= 70 ? "Kuat" : "Jenuh Beli");
    const bbp = snap.bb_pct_b;
    gaugeCard("gauge-bb", "BBands", bbp == null ? 0 : bbp, "#4dabf7",
      bbp == null ? "—" : bbp.toFixed(2),
      bbp == null ? "—" : bbp >= 0.75 ? "Upper Band" : bbp <= 0.25 ? "Lower Band" : "Middle Band");

    /* --- kartu eksekusi (tombol data-* + delegasi, tanpa inline onclick) --- */
    $("execute-card").innerHTML =
      '<button class="primary exec-main" data-trade-symbol="' + esc(s.symbol) + '"><i class="fa-solid fa-bolt"></i> Eksekusi Sinyal <i class="fa-solid fa-chevron-right"></i></button>' +
      '<div class="exec-sub"><button data-trade-symbol="' + esc(s.symbol) + '" data-trade-side="LONG">Paksa LONG</button>' +
      '<button data-trade-symbol="' + esc(s.symbol) + '" data-trade-side="SHORT">Paksa SHORT</button></div>' +
      '<div class="exec-note"><i class="fa-solid fa-circle-info"></i> Pastikan analisis sebelum trading.</div>';
    $("signal-row").hidden = false;

    renderSentiment(s);
    chartSymbol = s.symbol;
    $("chart-block").hidden = false;
    $("backtest-block").hidden = false;
    $("chart-title").textContent = "Grafik Candlestick — " + s.symbol;
    loadChart(s.symbol);
    /* Paritas aplikasi Android: panel multi-timeframe + salin teks. */
    lastSignalPayload = s;
    $("btn-share-text").hidden = false;
    loadMtfWeb(s.symbol);
  } catch (e) {
    box.innerHTML = '<span class="neg">Gagal menganalisis: ' + esc(e.message) + "</span>";
  }
}

/* Delegasi klik kartu eksekusi & tabel scan (tanpa inline onclick). */
$("execute-card").addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-trade-symbol]");
  if (btn) tradeSymbol(btn.getAttribute("data-trade-symbol"), btn.getAttribute("data-trade-side") || null);
});
document.querySelector("#scan-table tbody").addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-detail-symbol]");
  if (btn) openDetail(btn.getAttribute("data-detail-symbol"));
});

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
      <td><button class="sym-link" data-detail-symbol="${esc(s.symbol)}">${esc(s.symbol)}<span class="sym-arrow">›</span></button></td>
      <td class="${sideClass(s.side)}">${esc(s.side)}</td>
      <td>${fmtPrice(s.price)}</td>
      <td>${s.confidence}%</td>
      <td>${s.technical_score}</td>
      <td>${fmtPrice(s.stop_loss)}</td>
      <td>${fmtPrice(s.take_profit)}</td>
    </tr>`).join("");
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="7" class="neg">Gagal memindai: ' + esc(e.message) + "</td></tr>";
  } finally {
    btn.disabled = false;
    btn.textContent = "Pindai Sekarang";
  }
}

/* ---------------- siaran sinyal ke Telegram ---------------- */
async function broadcast() {
  const btn = $("btn-broadcast");
  const label = btn.querySelector("span");
  btn.disabled = true;
  label.textContent = "Menyaring & mengirim…";
  try {
    const res = await api("/api/broadcast", { method: "POST" });
    if (res.sent && res.sent.length) {
      toast("📣 Sinyal terkirim ke Telegram: " + res.sent.join(", "));
    } else if (!res.telegram_enabled) {
      toast("Telegram belum dikonfigurasi di .env backend.");
    } else {
      toast("Tidak ada sinyal kuat yang lolos konfirmasi AI saat ini — tidak ada siaran.");
    }
  } catch (e) {
    toast("Siaran gagal: " + e.message);
  } finally {
    btn.disabled = false;
    label.textContent = "Siaran ke Telegram";
  }
}
$("btn-broadcast").addEventListener("click", broadcast);

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

/* Grafik memakai TradingView Lightweight Charts (vendor lokal):
   candlestick interaktif + EMA20/EMA50 + volume, crosshair dengan
   legenda OHLCV, zoom roda mouse & geser timeline bawaan library.
   Ukuran mengikuti container secara otomatis (autoSize). */
let lwChart = null, lwCandles = null, lwEma20 = null, lwEma50 = null, lwVolume = null;
let candleByTime = new Map();

function fmtVol(v) {
  if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (v >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return String(Math.round(v));
}

function ensureChart() {
  if (lwChart) return lwChart;
  if (typeof LightweightCharts === "undefined") return null;
  lwChart = LightweightCharts.createChart($("candle-chart"), {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: "#848e9c", fontSize: 11 },
    grid: {
      vertLines: { color: "rgba(37,43,53,.55)" },
      horzLines: { color: "rgba(37,43,53,.55)" },
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: "#252b35" },
    timeScale: { borderColor: "#252b35", timeVisible: true, secondsVisible: false },
  });
  lwCandles = lwChart.addCandlestickSeries({
    upColor: "#0ecb81", downColor: "#f6465d",
    wickUpColor: "#0ecb81", wickDownColor: "#f6465d", borderVisible: false,
  });
  lwEma20 = lwChart.addLineSeries({ color: "#f0b90b", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  lwEma50 = lwChart.addLineSeries({ color: "#848e9c", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  lwVolume = lwChart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "" });
  lwChart.priceScale("").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
  lwChart.priceScale("right").applyOptions({ scaleMargins: { top: 0.06, bottom: 0.2 } });
  lwChart.subscribeCrosshairMove((param) => {
    updateLegend(param && param.time ? candleByTime.get(param.time) : null);
  });
  return lwChart;
}

function updateLegend(c) {
  const el = $("chart-legend");
  if (!el) return;
  if (!c && lastCandles.length) c = lastCandles[lastCandles.length - 1];
  if (!c) { el.textContent = ""; return; }
  const chg = c.open ? ((c.close - c.open) / c.open) * 100 : 0;
  el.innerHTML =
    "<b>" + (chartSymbol || "") + "</b> · " + $("chart-interval").value +
    " &nbsp;O " + fmtPrice(c.open) + " &nbsp;H " + fmtPrice(c.high) +
    " &nbsp;L " + fmtPrice(c.low) + " &nbsp;C " + fmtPrice(c.close) +
    ' &nbsp;<span class="' + (chg >= 0 ? "pos" : "neg") + '">' + (chg >= 0 ? "+" : "") + chg.toFixed(2) + "%</span>" +
    " &nbsp;Vol " + fmtVol(c.volume);
}

async function loadChart(symbol) {
  try {
    const interval = $("chart-interval").value;
    const data = await api("/api/klines/" + encodeURIComponent(symbol) + "?interval=" + interval + "&limit=200");
    lastCandles = data.candles || [];
    renderChart();
    connectLive(symbol, interval);
  } catch (e) {
    toast("Gagal memuat grafik: " + e.message);
  }
}

/* ---------------- grafik real-time (WebSocket) ----------------
   Riwayat dimuat lewat REST di atas; socket ini hanya mendorong candle
   yang sedang berjalan. Bila socket gagal/putus, grafik REST tetap
   berfungsi — reconnect dicoba sekali tiap 5 detik selama simbol yang
   sama masih ditampilkan. */
let liveSocket = null, liveKey = "", liveRetry = null;

function disconnectLive() {
  if (liveRetry) { clearTimeout(liveRetry); liveRetry = null; }
  if (liveSocket) {
    const ws = liveSocket;
    liveSocket = null;
    try { ws.close(); } catch (e) { /* sudah tertutup */ }
  }
  liveKey = "";
}

function connectLive(symbol, interval) {
  const key = symbol + "@" + interval;
  if (liveSocket && liveKey === key) return;
  disconnectLive();
  liveKey = key;
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  let url = proto + location.host + "/api/ws/klines/" + encodeURIComponent(symbol) +
    "?interval=" + encodeURIComponent(interval);
  let ws;
  try { ws = new WebSocket(url); } catch (e) { return; }
  liveSocket = ws;
  ws.onmessage = (ev) => {
    try {
      const payload = JSON.parse(ev.data);
      if (payload && payload.candle) applyLiveCandle(payload.candle);
    } catch (e) { /* abaikan pesan yang tidak valid */ }
  };
  ws.onclose = () => {
    if (liveSocket === ws) {
      liveSocket = null;
      liveRetry = setTimeout(() => {
        if (chartSymbol === symbol) connectLive(symbol, interval);
      }, 5000);
    }
  };
  ws.onerror = () => { try { ws.close(); } catch (e) { /* pemicu onclose */ } };
}

function applyLiveCandle(c) {
  if (!lwChart || !lwCandles || !lastCandles.length) return;
  const tsec = Math.floor(c.time / 1000);
  const last = lastCandles[lastCandles.length - 1];
  if (Math.floor(last.time / 1000) === tsec) {
    lastCandles[lastCandles.length - 1] = c;
  } else if (c.time > last.time) {
    lastCandles.push(c);
    if (lastCandles.length > 300) lastCandles.shift();
  } else {
    return;
  }
  candleByTime.set(tsec, c);
  lwCandles.update({ time: tsec, open: c.open, high: c.high, low: c.low, close: c.close });
  const closes = lastCandles.map((x) => x.close);
  const idx = lastCandles.length - 1;
  const e20 = emaSeries(closes, 20)[idx];
  const e50 = emaSeries(closes, 50)[idx];
  if (typeof e20 === "number") lwEma20.update({ time: tsec, value: e20 });
  if (typeof e50 === "number") lwEma50.update({ time: tsec, value: e50 });
  lwVolume.update({
    time: tsec, value: c.volume,
    color: c.close >= c.open ? "rgba(14,203,129,.5)" : "rgba(246,70,93,.5)",
  });
  updateLegend(c);
}

function renderChart() {
  if (!ensureChart() || !lastCandles.length) return;
  const t = (ms) => Math.floor(ms / 1000);
  const bars = lastCandles.map((c) => ({ time: t(c.time), open: c.open, high: c.high, low: c.low, close: c.close }));
  candleByTime = new Map(lastCandles.map((c) => [t(c.time), c]));
  lwCandles.setData(bars);
  const closes = lastCandles.map((c) => c.close);
  const toLine = (vals) => vals.map((v, i) => ({ time: bars[i].time, value: v }));
  lwEma20.setData(toLine(emaSeries(closes, 20)));
  lwEma50.setData(toLine(emaSeries(closes, 50)));
  lwVolume.setData(lastCandles.map((c) => ({
    time: t(c.time), value: c.volume,
    color: c.close >= c.open ? "rgba(14,203,129,.5)" : "rgba(246,70,93,.5)",
  })));
  lwChart.timeScale().fitContent();
  updateLegend(null);
}

$("chart-interval").addEventListener("change", () => { if (chartSymbol) loadChart(chartSymbol); });
window.addEventListener("resize", () => { drawEquity(); });

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
      "<tr><td>" + esc(t.side) + "</td><td>" + fmtPrice(t.entry_price) + "</td><td>" + fmtPrice(t.exit_price) +
      "</td><td>" + esc(t.exit_reason) + '</td><td class="' + (t.pnl >= 0 ? "pos" : "neg") + '">' + fmtUsd(t.pnl) + "</td></tr>"
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

/* ---------------- tab interval grafik ---------------- */
function syncTfTabs() {
  const cur = $("chart-interval").value;
  document.querySelectorAll(".tf-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.interval === cur));
}
document.querySelectorAll(".tf-tab").forEach((b) => b.addEventListener("click", () => {
  const sel = $("chart-interval");
  if (sel.value === b.dataset.interval) return;
  sel.value = b.dataset.interval;
  syncTfTabs();
  sel.dispatchEvent(new Event("change"));
}));

/* ---------------- navigasi: sidebar, menu bawah, pencarian, hero ---------------- */
function closeSidebar() {
  $("sidebar").classList.remove("open");
  $("scrim").hidden = true;
}
function goTo(target) {
  const el = document.getElementById(target);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  document.querySelectorAll("[data-target]").forEach((b) =>
    b.classList.toggle("active", b.dataset.target === target));
  closeSidebar();
}
document.querySelectorAll("[data-target]").forEach((b) =>
  b.addEventListener("click", () => goTo(b.dataset.target)));
$("btn-menu").addEventListener("click", () => {
  const open = $("sidebar").classList.toggle("open");
  $("scrim").hidden = !open;
});
$("scrim").addEventListener("click", closeSidebar);

function startAnalysis() {
  goTo("panel-analysis");
  const v = $("symbol-input").value.trim();
  if (v) openDetail(v); else $("symbol-input").focus();
}
$("btn-start").addEventListener("click", startAnalysis);
$("promo-start").addEventListener("click", startAnalysis);
$("top-search").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const v = e.target.value.trim().toUpperCase();
  if (v) openDetail(v);
});

/* ---------------- workspace analisis yang bisa diubah ukurannya ----------------
   Desktop (≥1241px): seret garis pemisah untuk mengatur lebar panel
   analisis/sentimen, tinggi workspace, dan lebar panel sinyal/kartu
   eksekusi. Klik ganda pada garis = ukuran bawaan. Panel bisa
   disembunyikan (rel emas untuk menampilkan lagi), grafik bisa
   dimaksimalkan, dan seluruh tata letak tersimpan di localStorage.
   Mobile: tata letak bertumpuk seperti biasa; tombol maximize grafik
   membuka grafik layar penuh. */
const LAYOUT_KEY = "baf-layout-v1";
const LAYOUT_DEFAULTS = { left: 328, right: 292, height: 620, sig: null, exec: null, hideLeft: false, hideRight: false, max: false };
let layoutState = (() => {
  try { return Object.assign({}, LAYOUT_DEFAULTS, JSON.parse(localStorage.getItem(LAYOUT_KEY) || "{}")); }
  catch (e) { return Object.assign({}, LAYOUT_DEFAULTS); }
})();
let savedHideState = null;
const desktopLayout = () => window.matchMedia("(min-width: 1241px)").matches;
const clampNum = (v, a, b) => Math.min(b, Math.max(a, v));

function saveLayout() {
  try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(layoutState)); } catch (e) { /* abaikan */ }
}

function applyLayout() {
  const L = $("analysis-layout");
  if (!L) return;
  const S = $("signal-row");
  const desk = desktopLayout();
  if (!desk) {
    L.style.gridTemplateColumns = "";
    L.style.height = "";
    if (S) S.style.gridTemplateColumns = "";
    $("an-left").style.display = "";
    $("an-right").style.display = "";
    $("rail-left").hidden = true;
    $("rail-right").hidden = true;
    return;
  }
  const hl = layoutState.hideLeft, hr = layoutState.hideRight;
  L.style.gridTemplateColumns =
    (hl ? "0px 0px" : layoutState.left + "px 6px") +
    " minmax(0,1fr) " +
    (hr ? "0px 0px" : "6px " + layoutState.right + "px");
  L.style.height = layoutState.height + "px";
  $("an-left").style.display = hl ? "none" : "";
  $("an-right").style.display = hr ? "none" : "";
  $("rz-left").style.display = hl ? "none" : "";
  $("rz-right").style.display = hr ? "none" : "";
  $("rail-left").hidden = !hl;
  $("rail-right").hidden = !hr;
  if (S && layoutState.sig && layoutState.exec) {
    S.style.gridTemplateColumns = layoutState.sig + "px 6px repeat(3, minmax(110px, 1fr)) 6px " + layoutState.exec + "px";
  }
  const maxBtn = $("btn-chart-max");
  if (maxBtn && desk) {
    maxBtn.innerHTML = '<i class="fa-solid fa-' + (layoutState.max ? "compress" : "expand") + '"></i>';
  }
}

function dragHandle(el, opts) {
  if (!el) return;
  el.addEventListener("pointerdown", (e) => {
    if (!desktopLayout()) return;
    e.preventDefault();
    el.setPointerCapture(e.pointerId);
    el.classList.add("dragging");
    const startX = e.clientX, startY = e.clientY;
    const start = opts.start();
    const move = (ev) => opts.move(ev.clientX - startX, ev.clientY - startY, start);
    const up = () => {
      el.classList.remove("dragging");
      el.removeEventListener("pointermove", move);
      saveLayout();
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up, { once: true });
    el.addEventListener("pointercancel", up, { once: true });
  });
  el.addEventListener("dblclick", () => { opts.reset(); applyLayout(); saveLayout(); });
}

function toggleChartMax() {
  const block = $("chart-block");
  if (!desktopLayout()) {
    const on = block.classList.toggle("chart-fullscreen");
    $("btn-chart-max").innerHTML = '<i class="fa-solid fa-' + (on ? "compress" : "expand") + '"></i>';
    return;
  }
  if (!layoutState.max) {
    savedHideState = { l: layoutState.hideLeft, r: layoutState.hideRight };
    layoutState.hideLeft = true;
    layoutState.hideRight = true;
    layoutState.max = true;
  } else {
    if (savedHideState) {
      layoutState.hideLeft = savedHideState.l;
      layoutState.hideRight = savedHideState.r;
    }
    layoutState.max = false;
  }
  applyLayout();
  saveLayout();
}

function initLayout() {
  dragHandle($("rz-left"), {
    start: () => layoutState.left,
    move: (dx, _dy, s) => {
      const maxLeft = $("analysis-layout").clientWidth - (layoutState.hideRight ? 0 : layoutState.right) - 400;
      layoutState.left = clampNum(s + dx, 232, Math.max(260, maxLeft));
      applyLayout();
    },
    reset: () => { layoutState.left = LAYOUT_DEFAULTS.left; },
  });
  dragHandle($("rz-right"), {
    start: () => layoutState.right,
    move: (dx, _dy, s) => { layoutState.right = clampNum(s - dx, 224, 520); applyLayout(); },
    reset: () => { layoutState.right = LAYOUT_DEFAULTS.right; },
  });
  dragHandle($("rz-height"), {
    start: () => layoutState.height,
    move: (_dx, dy, s) => { layoutState.height = clampNum(s + dy, 430, 1100); applyLayout(); },
    reset: () => { layoutState.height = LAYOUT_DEFAULTS.height; },
  });
  dragHandle($("rz-sig-1"), {
    start: () => $("signal-panel").offsetWidth,
    move: (dx, _dy, s) => {
      if (!layoutState.exec) layoutState.exec = $("execute-card").offsetWidth;
      layoutState.sig = clampNum(s + dx, 250, 760);
      applyLayout();
    },
    reset: () => { layoutState.sig = null; const S = $("signal-row"); if (S) S.style.gridTemplateColumns = ""; },
  });
  dragHandle($("rz-sig-2"), {
    start: () => $("execute-card").offsetWidth,
    move: (dx, _dy, s) => {
      if (!layoutState.sig) layoutState.sig = $("signal-panel").offsetWidth;
      layoutState.exec = clampNum(s - dx, 200, 520);
      applyLayout();
    },
    reset: () => { layoutState.exec = null; const S = $("signal-row"); if (S) S.style.gridTemplateColumns = ""; },
  });
  $("btn-hide-left").addEventListener("click", () => { layoutState.hideLeft = true; layoutState.max = false; applyLayout(); saveLayout(); });
  $("btn-hide-right").addEventListener("click", () => { layoutState.hideRight = true; layoutState.max = false; applyLayout(); saveLayout(); });
  $("rail-left").addEventListener("click", () => { layoutState.hideLeft = false; layoutState.max = false; applyLayout(); saveLayout(); });
  $("rail-right").addEventListener("click", () => { layoutState.hideRight = false; layoutState.max = false; applyLayout(); saveLayout(); });
  $("btn-chart-max").addEventListener("click", toggleChartMax);
  $("btn-reset-layout").addEventListener("click", () => {
    layoutState = Object.assign({}, LAYOUT_DEFAULTS);
    savedHideState = null;
    const S = $("signal-row");
    if (S) S.style.gridTemplateColumns = "";
    $("chart-block").classList.remove("chart-fullscreen");
    $("btn-chart-max").innerHTML = '<i class="fa-solid fa-expand"></i>';
    applyLayout();
    saveLayout();
    toast("Tata letak dikembalikan ke ukuran bawaan.");
  });
  window.matchMedia("(min-width: 1241px)").addEventListener("change", () => {
    $("chart-block").classList.remove("chart-fullscreen");
    applyLayout();
  });
  applyLayout();
}

initLayout();
loadConfig();
loadAiPanel();
loadSymbols();
loadAccount();

/* =====================================================================
   Pasar · Multi-Timeframe · Salin Teks · Berita
   Paritas fitur crypto dengan aplikasi Android D TRADE: daftar semua
   simbol + skor teknikal 1h + favorit, panel MTF di analisis, teks
   analisis siap tempel, dan agregasi berita RSS lewat backend.
   ===================================================================== */

/* --- mesin skor teknikal (port persis dari aplikasi Android) --- */
function rsiLast(closes, period) {
  period = period || 14;
  if (closes.length < period + 1) return null;
  let avgG = 0, avgL = 0;
  for (let i = 1; i <= period; i++) {
    const d = closes[i] - closes[i - 1];
    if (d > 0) avgG += d / period; else avgL -= d / period;
  }
  for (let i = period + 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    avgG = (avgG * (period - 1) + Math.max(d, 0)) / period;
    avgL = (avgL * (period - 1) + Math.max(-d, 0)) / period;
  }
  if (avgL === 0) return 100;
  return 100 - 100 / (1 + avgG / avgL);
}
function macdLast(closes) {
  const fast = emaSeries(closes, 12), slow = emaSeries(closes, 26);
  const line = closes.map((_, i) => (fast[i] !== null && slow[i] !== null) ? fast[i] - slow[i] : null);
  const valid = line.filter(v => v !== null);
  const sig = emaSeries(valid, 9);
  const m = valid[valid.length - 1], s = sig[sig.length - 1];
  if (m === undefined || s === null || s === undefined) return null;
  const prevM = valid[valid.length - 2], prevS = sig[sig.length - 2];
  return {
    line: m, signal: s, hist: m - s,
    prevHist: (prevM !== undefined && prevS !== null && prevS !== undefined) ? prevM - prevS : null
  };
}
function bollLast(closes, period) {
  period = period || 20;
  if (closes.length < period) return null;
  const w = closes.slice(-period);
  const mid = w.reduce((a, b) => a + b, 0) / period;
  const sd = Math.sqrt(w.reduce((a, b) => a + (b - mid) * (b - mid), 0) / period);
  const upper = mid + 2 * sd, lower = mid - 2 * sd;
  const last = closes[closes.length - 1];
  return { mid, upper, lower, pctB: upper === lower ? 0.5 : (last - lower) / (upper - lower) };
}
function atrWilder(highs, lows, closes, period) {
  if (!highs || !lows || closes.length < period + 1) return null;
  const trs = [];
  for (let i = 1; i < closes.length; i++) {
    trs.push(Math.max(
      highs[i] - lows[i],
      Math.abs(highs[i] - closes[i - 1]),
      Math.abs(lows[i] - closes[i - 1])
    ));
  }
  let atr = trs.slice(0, period).reduce((a, b) => a + b, 0) / period;
  for (let i = period; i < trs.length; i++) atr = (atr * (period - 1) + trs[i]) / period;
  return atr;
}

/* ADX(14) + ±DI Wilder: kekuatan & arah tren. */
function adxLast(highs, lows, closes, period) {
  period = period || 14;
  const n = closes.length;
  if (!highs || !lows || n < period * 2 + 2) return null;
  const trs = [], pdm = [], mdm = [];
  for (let i = 1; i < n; i++) {
    trs.push(Math.max(
      highs[i] - lows[i],
      Math.abs(highs[i] - closes[i - 1]),
      Math.abs(lows[i] - closes[i - 1])
    ));
    const up = highs[i] - highs[i - 1], dn = lows[i - 1] - lows[i];
    pdm.push(up > dn && up > 0 ? up : 0);
    mdm.push(dn > up && dn > 0 ? dn : 0);
  }
  let sTR = 0, sP = 0, sM = 0;
  for (let i = 0; i < period; i++) { sTR += trs[i]; sP += pdm[i]; sM += mdm[i]; }
  const dxs = [];
  for (let i = period; i < trs.length; i++) {
    sTR = sTR - sTR / period + trs[i];
    sP = sP - sP / period + pdm[i];
    sM = sM - sM / period + mdm[i];
    const pDI = sTR === 0 ? 0 : 100 * sP / sTR;
    const mDI = sTR === 0 ? 0 : 100 * sM / sTR;
    dxs.push(pDI + mDI === 0 ? 0 : 100 * Math.abs(pDI - mDI) / (pDI + mDI));
    if (i === trs.length - 1) {
      // ADX = rata-rata Wilder dari DX terakhir
      let adx = dxs.slice(0, period).reduce((a, b) => a + b, 0) / period;
      for (let j = period; j < dxs.length; j++) adx = (adx * (period - 1) + dxs[j]) / period;
      return { adx, plusDI: pDI, minusDI: mDI };
    }
  }
  return null;
}

/* Supertrend(10, 3): arah pita ATR. */
function supertrendLast(highs, lows, closes, period, mult) {
  period = period || 10; mult = mult || 3;
  const n = closes.length;
  if (!highs || !lows || n < period + 2) return null;
  const atr = atrWilder(highs, lows, closes, period);
  if (atr === null) return null;
  // ATR per-candle (Wilder berjalan) diperlukan untuk pita historis;
  // pendekatan: hitung ulang seri ATR sederhana di sini.
  const trs = [];
  for (let i = 1; i < n; i++) {
    trs.push(Math.max(highs[i] - lows[i],
      Math.abs(highs[i] - closes[i - 1]), Math.abs(lows[i] - closes[i - 1])));
  }
  let a = trs.slice(0, period).reduce((x, y) => x + y, 0) / period;
  let upperPrev = null, lowerPrev = null, dir = 1;
  for (let i = period; i < trs.length; i++) {
    a = i === period ? a : (a * (period - 1) + trs[i]) / period;
    const idx = i + 1; // candle yang sesuai dengan trs[i]
    const mid = (highs[idx] + lows[idx]) / 2;
    let upper = mid + mult * a, lower = mid - mult * a;
    if (upperPrev !== null) {
      if (closes[idx - 1] <= upperPrev) upper = Math.min(upper, upperPrev);
      if (closes[idx - 1] >= lowerPrev) lower = Math.max(lower, lowerPrev);
    }
    if (upperPrev !== null) {
      if (closes[idx] > upperPrev) dir = 1;
      else if (closes[idx] < lowerPrev) dir = -1;
    }
    upperPrev = upper; lowerPrev = lower;
  }
  return { dir, line: dir === 1 ? lowerPrev : upperPrev };
}

/* Stochastic(14, 3): %K yang dihaluskan + %D. */
function stochLast(highs, lows, closes, kPeriod, smooth) {
  kPeriod = kPeriod || 14; smooth = smooth || 3;
  const n = closes.length;
  if (!highs || !lows || n < kPeriod + smooth) return null;
  const rawK = [];
  for (let i = kPeriod - 1; i < n; i++) {
    let hh = -Infinity, ll = Infinity;
    for (let j = i - kPeriod + 1; j <= i; j++) {
      if (highs[j] > hh) hh = highs[j];
      if (lows[j] < ll) ll = lows[j];
    }
    rawK.push(hh === ll ? 50 : 100 * (closes[i] - ll) / (hh - ll));
  }
  const sma = (arr, p, end) => {
    let s = 0; for (let j = end - p + 1; j <= end; j++) s += arr[j];
    return s / p;
  };
  const k = sma(rawK, smooth, rawK.length - 1);
  const kPrev = sma(rawK, smooth, rawK.length - 2);
  const kSeries = rawK.map((_, i) => i >= smooth - 1 ? sma(rawK, smooth, i) : null).filter(v => v !== null);
  const d = kSeries.length >= 3 ? (kSeries[kSeries.length - 1] + kSeries[kSeries.length - 2] + kSeries[kSeries.length - 3]) / 3 : k;
  return { k, d, kPrev };
}

/* VWAP bergulir 24 candle (harga tipikal × volume). */
function vwapLast(highs, lows, closes, vols, window) {
  if (!vols || !vols.length) return null;
  window = window || 24;
  const n = Math.min(window, closes.length);
  let pv = 0, v = 0;
  for (let i = closes.length - n; i < closes.length; i++) {
    const tp = (highs[i] + lows[i] + closes[i]) / 3;
    pv += tp * (vols[i] || 0);
    v += vols[i] || 0;
  }
  return v > 0 ? pv / v : null;
}

/* Skor teknikal v2 (paritas D TRADE v1.25.0) — 12 komponen: tren EMA, MACD, ADX/DI,
   Supertrend, RSI, Stochastic, Bollinger, momentum, VWAP, konfirmasi
   volume, pola candle, support/resistance. Blok tren diskalakan oleh
   kekuatan tren (ADX). vols & opens opsional. */
function scoreSymbolTech(closes, highs, lows, vols, opens) {
  const parts = []; // {pts, text}
  const last = closes[closes.length - 1];
  const n = closes.length;
  const atr = atrWilder(highs, lows, closes, 14);
  const adxR = adxLast(highs, lows, closes, 14);
  const adx = adxR ? adxR.adx : null;
  const trendScale = adx === null ? 1 : adx >= 22 ? 1 : adx >= 15 ? 0.9 : 0.78;

  // --- Blok tren (diskalakan ADX) ---
  let trend = 0;
  const e20s = emaSeries(closes, 20), e50s = emaSeries(closes, 50);
  const e20 = e20s[e20s.length - 1], e50 = e50s[e50s.length - 1];
  if (e20 !== null && e50 !== null) {
    if (last > e20) { trend += 6; parts.push({ p: 6, t: "Harga di atas EMA20" }); }
    else { trend -= 6; parts.push({ p: -6, t: "Harga di bawah EMA20" }); }
    if (e20 > e50) { trend += 10; parts.push({ p: 10, t: "EMA20 di atas EMA50 — tren naik" }); }
    else { trend -= 10; parts.push({ p: -10, t: "EMA20 di bawah EMA50 — tren turun" }); }
    if (last > e50) { trend += 8; parts.push({ p: 8, t: "Harga di atas EMA50" }); }
    else { trend -= 8; parts.push({ p: -8, t: "Harga di bawah EMA50" }); }
    const e50prev = e50s[e50s.length - 6];
    if (e50prev !== null && e50prev !== undefined) {
      if (e50 > e50prev) { trend += 4; parts.push({ p: 4, t: "EMA50 menanjak" }); }
      else if (e50 < e50prev) { trend -= 4; parts.push({ p: -4, t: "EMA50 menurun" }); }
    }
  }
  const m = macdLast(closes);
  if (m) {
    if (m.hist > 0) { trend += 10; parts.push({ p: 10, t: "MACD histogram positif" }); }
    else { trend -= 10; parts.push({ p: -10, t: "MACD histogram negatif" }); }
    if (m.line > 0) { trend += 4; parts.push({ p: 4, t: "MACD di atas nol" }); }
    else { trend -= 4; parts.push({ p: -4, t: "MACD di bawah nol" }); }
    if (m.prevHist !== null) {
      if (m.hist > m.prevHist && m.hist > 0) { trend += 3; parts.push({ p: 3, t: "Histogram MACD menguat" }); }
      else if (m.hist < m.prevHist && m.hist < 0) { trend -= 3; parts.push({ p: -3, t: "Histogram MACD melemah" }); }
    }
  }
  const st = supertrendLast(highs, lows, closes, 10, 3);
  if (st) {
    trend += st.dir * 9;
    parts.push({ p: st.dir * 9, t: st.dir > 0 ? "Supertrend naik (harga di atas pita)" : "Supertrend turun (harga di bawah pita)" });
  }
  const trendPts = Math.round(trend * trendScale);

  // --- ADX/DI (arah + kekuatan) ---
  let rest = 0;
  if (adxR) {
    const f = adx < 15 ? 0.25 : adx >= 25 ? 1 : 0.25 + (adx - 15) / 10 * 0.75;
    const pts = Math.round(8 * f) * (adxR.plusDI >= adxR.minusDI ? 1 : -1);
    rest += pts;
    parts.push({ p: pts, t: "ADX " + adx.toFixed(0) + (adx >= 25 ? " — tren kuat, " : adx >= 15 ? " — tren sedang, " : " — tren lemah, ") + (pts >= 0 ? "+DI dominan" : "−DI dominan") });
  }

  // --- Osilator & momentum ---
  const rsi = rsiLast(closes);
  if (rsi !== null) {
    let pts = Math.max(-12, Math.min(12, (rsi - 50) * 0.6));
    if (rsi > 75) pts = Math.min(pts, 4);
    if (rsi < 25) pts = Math.max(pts, -4);
    pts = Math.round(pts);
    rest += pts;
    parts.push({ p: pts, t: "RSI " + rsi.toFixed(1) });
  }
  const stoch = stochLast(highs, lows, closes, 14, 3);
  if (stoch) {
    let pts = Math.round((stoch.k - 50) / 50 * 6);
    if (stoch.kPrev <= stoch.d && stoch.k > stoch.d) pts += 2;
    else if (stoch.kPrev >= stoch.d && stoch.k < stoch.d) pts -= 2;
    if (adx !== null && adx >= 25) pts = Math.round(pts * 0.5);
    rest += pts;
    parts.push({ p: pts, t: "Stochastic %K " + stoch.k.toFixed(0) });
  }
  const b = bollLast(closes);
  if (b) {
    const pts = Math.round((b.pctB - 0.5) * 16);
    rest += pts;
    parts.push({ p: pts, t: "Posisi Bollinger %B " + b.pctB.toFixed(2) });
  }
  if (n > 10) {
    const mom = (last / closes[n - 11] - 1) * 100;
    const pts = Math.round(Math.max(-10, Math.min(10, mom * 2)));
    rest += pts;
    parts.push({ p: pts, t: "Momentum 10 candle " + mom.toFixed(2) + "%" });
  }

  // --- VWAP + konfirmasi volume ---
  const vwap = vwapLast(highs, lows, closes, vols, 24);
  if (vwap !== null && atr) {
    const dev = (last - vwap) / atr;
    const pts = Math.max(-6, Math.min(6, Math.round(dev * 4)));
    rest += pts;
    parts.push({ p: pts, t: "Harga " + Math.abs(dev).toFixed(1) + "×ATR " + (dev >= 0 ? "di atas" : "di bawah") + " VWAP" });
  }
  if (vols && vols.length >= 21) {
    const smaV = vols.slice(-21, -1).reduce((a, x) => a + x, 0) / 20;
    if (smaV > 0) {
      const ratio = vols[vols.length - 1] / smaV;
      const dirC = Math.sign(last - closes[n - 2]);
      if (dirC !== 0 && ratio >= 1.15) {
        const pts = dirC * (ratio >= 1.5 ? 4 : 2);
        rest += pts;
        parts.push({ p: pts, t: "Volume " + ratio.toFixed(1) + "× rataan " + (dirC > 0 ? "mengonfirmasi kenaikan" : "mengonfirmasi penurunan") });
      }
    }
  }

  // --- Pola candle terakhir (perlu opens) ---
  if (opens && n >= 2 && highs && lows) {
    const i = n - 1, j = n - 2;
    const o = opens[i], c = closes[i], po = opens[j], pc = closes[j];
    const range = highs[i] - lows[i];
    const body = Math.abs(c - o);
    if (range > 0) {
      const bullEngulf = pc < po && c > o && o <= pc && c >= po;
      const bearEngulf = pc > po && c < o && o >= pc && c <= po;
      const upperWick = highs[i] - Math.max(o, c);
      const lowerWick = Math.min(o, c) - lows[i];
      if (bullEngulf) { rest += 4; parts.push({ p: 4, t: "Pola bullish engulfing" }); }
      else if (bearEngulf) { rest -= 4; parts.push({ p: -4, t: "Pola bearish engulfing" }); }
      else if (body > 0 && lowerWick > body * 2 && upperWick < range * 0.25) {
        rest += 3; parts.push({ p: 3, t: "Pola hammer — penolakan harga bawah" });
      } else if (body > 0 && upperWick > body * 2 && lowerWick < range * 0.25) {
        rest -= 3; parts.push({ p: -3, t: "Pola shooting star — penolakan harga atas" });
      }
    }
  }

  // --- Support / Resistance (ayunan 30 candle) ---
  if (highs && lows && n >= 31 && atr) {
    let swH = -Infinity, swL = Infinity;
    for (let i = n - 31; i < n - 1; i++) {
      if (highs[i] > swH) swH = highs[i];
      if (lows[i] < swL) swL = lows[i];
    }
    if (last > swH) { rest += 5; parts.push({ p: 5, t: "Breakout di atas resistance 30 candle" }); }
    else if (last < swL) { rest -= 5; parts.push({ p: -5, t: "Breakdown di bawah support 30 candle" }); }
    else if (swH - last <= 0.35 * atr) { rest -= 2; parts.push({ p: -2, t: "Harga mendekati resistance" }); }
    else if (last - swL <= 0.35 * atr) { rest += 2; parts.push({ p: 2, t: "Harga mendekati support" }); }
  }

  let score = Math.round((trendPts + rest) * 1.18);
  score = Math.max(-100, Math.min(100, score));
  const side = score >= 25 ? "LONG" : score <= -25 ? "SHORT" : "NETRAL";
  const reasons = parts
    .slice()
    .sort((a, b2) => Math.abs(b2.p) - Math.abs(a.p))
    .map(x => x.t + " (" + (x.p >= 0 ? "+" : "−") + Math.abs(x.p) + ")");
  return { score, side, rsi, macd: m, boll: b, ema20: e20, ema50: e50, atr, adx, stoch, vwap, reasons };
}
async function fetchScore(symbol, interval) {
  const d = await api("/api/klines/" + encodeURIComponent(symbol) +
    "?interval=" + interval + "&limit=100");
  const cd = d.candles || [];
  if (cd.length < 55) return null;
  return scoreSymbolTech(cd.map((c) => c.close), cd.map((c) => c.high), cd.map((c) => c.low), cd.map((c) => c.volume), cd.map((c) => c.open));
}

/* ---------------- Pasar ---------------- */
const MKT_SCORE_LIMIT = 80;
let mktRows = [];
let mktFavs = new Set();
try { mktFavs = new Set(JSON.parse(localStorage.getItem("baf-favs") || "[]")); } catch (e) { /* abaikan */ }
let mktFavOnly = false;
let mktScoresAt = 0;
let mktBusy = false;

function fmtVol(v) {
  return Number(v || 0).toLocaleString("id-ID", { notation: "compact", maximumFractionDigits: 1 });
}
function mktChip(o) {
  if (o) {
    const cls = o.side === "LONG" ? "long" : o.side === "SHORT" ? "short" : "neu";
    return '<span class="pill ' + cls + '">' + o.side + " · " + (o.score > 0 ? "+" : "") + o.score + "</span>";
  }
  return '<span class="muted">…</span>';
}
function mktVisible() {
  const q = $("market-q").value.trim().toUpperCase();
  let arr = mktRows.filter((r) => (!q || r.symbol.includes(q)) && (!mktFavOnly || mktFavs.has(r.symbol)));
  const mode = $("market-sort").value;
  if (mode === "signal") arr = arr.slice().sort((a, b) =>
    Math.abs((b.scoreObj || { score: -1 }).score) - Math.abs((a.scoreObj || { score: -1 }).score));
  else if (mode === "change") arr = arr.slice().sort((a, b) => b.change - a.change);
  else arr = arr.slice().sort((a, b) => b.volume - a.volume);
  return arr;
}
function renderMarket() {
  const tb = document.querySelector("#market-table tbody");
  const vis = mktVisible();
  if (!vis.length) {
    tb.innerHTML = '<tr><td colspan="6" class="muted">' +
      (mktFavOnly ? "Belum ada favorit — ketuk ☆ pada baris untuk menambahkan." : "Tidak ada simbol yang cocok.") +
      "</td></tr>";
    return;
  }
  tb.innerHTML = vis.map((r) =>
    '<tr data-msym="' + esc(r.symbol) + '" class="mkt-row">' +
    '<td><span class="mkt-star' + (mktFavs.has(r.symbol) ? " on" : "") + '" data-fav="' + esc(r.symbol) + '">' +
    (mktFavs.has(r.symbol) ? "★" : "☆") + "</span></td>" +
    "<td><b>" + esc(r.symbol.replace("USDT", "")) + '</b><span class="muted">/USDT</span></td>' +
    '<td data-mc="price">' + fmtPrice(r.price) + "</td>" +
    '<td data-mc="change" class="' + (r.change >= 0 ? "pos" : "neg") + '">' +
    (r.change >= 0 ? "+" : "") + r.change.toFixed(2) + "%</td>" +
    '<td data-mc="volume">' + fmtVol(r.volume) + "</td>" +
    '<td data-mc="chip">' + mktChip(r.scoreObj) + "</td></tr>"
  ).join("");
}
function paintMarketRow(r) {
  const tr = document.querySelector('#market-table tr[data-msym="' + r.symbol + '"]');
  if (!tr) return;
  tr.querySelector('[data-mc="price"]').textContent = fmtPrice(r.price);
  const ch = tr.querySelector('[data-mc="change"]');
  ch.textContent = (r.change >= 0 ? "+" : "") + r.change.toFixed(2) + "%";
  ch.className = r.change >= 0 ? "pos" : "neg";
  tr.querySelector('[data-mc="volume"]').textContent = fmtVol(r.volume);
  tr.querySelector('[data-mc="chip"]').innerHTML = mktChip(r.scoreObj);
}
function saveMarketCache() {
  try {
    localStorage.setItem("baf-market-cache", JSON.stringify({
      ts: Date.now(),
      rows: mktRows.map((r) => ({ symbol: r.symbol, price: r.price, change: r.change, volume: r.volume, scoreObj: r.scoreObj })),
    }));
  } catch (e) { /* abaikan */ }
}
function restoreMarketCache() {
  try {
    const c = JSON.parse(localStorage.getItem("baf-market-cache") || "null");
    if (c && c.rows && c.rows.length) {
      mktRows = c.rows;
      mktScoresAt = c.ts || 0;
      renderMarket();
      $("market-updated").textContent = "data tersimpan per " +
        new Date(c.ts).toLocaleTimeString("id-ID") + " · memperbarui…";
    }
  } catch (e) { /* abaikan */ }
}
async function computeMarketScores() {
  const targets = mktRows.slice().sort((a, b) => b.volume - a.volume).slice(0, MKT_SCORE_LIMIT);
  const queue = targets.slice();
  async function worker() {
    while (queue.length) {
      const r = queue.shift();
      try { r.scoreObj = await fetchScore(r.symbol, "1h"); } catch (e) { /* biarkan kosong */ }
      paintMarketRow(r);
    }
  }
  await Promise.all([worker(), worker(), worker(), worker(), worker(), worker()]);
  mktScoresAt = Date.now();
  saveMarketCache();
  if ($("market-sort").value === "signal") renderMarket();
}
async function loadMarket(forceScores) {
  if (mktBusy) return;
  mktBusy = true;
  try {
    const d = await api("/api/tickers");
    const by = {};
    (d.tickers || []).forEach((t) => { by[t.symbol] = t; });
    const prev = {};
    mktRows.forEach((r) => { prev[r.symbol] = r; });
    mktRows = (d.tickers || []).map((t) => ({
      symbol: t.symbol, price: t.price, change: t.change_pct, volume: t.quote_volume,
      scoreObj: prev[t.symbol] ? prev[t.symbol].scoreObj : null,
    }));
    renderMarket();
    $("market-updated").textContent = mktRows.length + " simbol · data per " +
      new Date().toLocaleTimeString("id-ID");
    saveMarketCache();
    if (forceScores || Date.now() - mktScoresAt > 300000) await computeMarketScores();
  } catch (e) {
    if (!mktRows.length) {
      document.querySelector("#market-table tbody").innerHTML =
        '<tr><td colspan="6" class="muted">Gagal memuat pasar: ' + esc(e.message) + "</td></tr>";
    } else {
      $("market-updated").textContent = "gagal memperbarui · menampilkan data tersimpan";
    }
  } finally {
    mktBusy = false;
  }
}
document.querySelector("#market-table tbody").addEventListener("click", (ev) => {
  const star = ev.target.closest("[data-fav]");
  if (star) {
    const sym = star.getAttribute("data-fav");
    if (mktFavs.has(sym)) mktFavs.delete(sym); else mktFavs.add(sym);
    try { localStorage.setItem("baf-favs", JSON.stringify([...mktFavs])); } catch (e) { /* abaikan */ }
    if (mktFavOnly) renderMarket();
    else {
      star.classList.toggle("on", mktFavs.has(sym));
      star.textContent = mktFavs.has(sym) ? "★" : "☆";
    }
    return;
  }
  const tr = ev.target.closest("[data-msym]");
  if (tr) openDetail(tr.getAttribute("data-msym"));
});
$("market-q").addEventListener("input", renderMarket);
$("market-sort").addEventListener("change", renderMarket);
$("market-favtog").addEventListener("click", () => {
  mktFavOnly = !mktFavOnly;
  $("market-favtog").textContent = mktFavOnly ? "★ Favorit" : "☆ Favorit";
  $("market-favtog").classList.toggle("on", mktFavOnly);
  renderMarket();
});
$("market-refresh").addEventListener("click", () => loadMarket(true));
restoreMarketCache();
loadMarket(false);
setInterval(() => { if (!document.hidden) loadMarket(false); }, 30000);

/* ---------------- Multi-Timeframe di Analisis ---------------- */
const MTF_TFS_WEB = ["15m", "1h", "4h", "1d"];
const mtfCacheWeb = {};
let mtfTokenWeb = 0;
async function loadMtfWeb(symbol) {
  const my = ++mtfTokenWeb;
  const block = $("mtf-block");
  block.hidden = false;
  const key = symbol;
  const hit = mtfCacheWeb[key];
  const paint = (data) => {
    if (my !== mtfTokenWeb) return;
    const active = $("chart-interval").value;
    $("mtf-row").innerHTML = MTF_TFS_WEB.map((tf) => {
      const o = data[tf];
      const cls = !o ? "neu" : o.side === "LONG" ? "long" : o.side === "SHORT" ? "short" : "neu";
      return '<div class="mtf-chip ' + cls + (tf === active ? " act" : "") + '">' +
        '<div class="tf">' + tf + (tf === active ? " · aktif" : "") + "</div>" +
        '<div class="sd">' + (o ? o.side : "…") + "</div>" +
        '<div class="sc">' + (o ? "skor " + (o.score > 0 ? "+" : "") + o.score : "menghitung") + "</div></div>";
    }).join("");
    const got = MTF_TFS_WEB.map((tf) => data[tf]).filter(Boolean);
    if (!got.length) { $("mtf-note").textContent = ""; return; }
    const L = got.filter((o) => o.side === "LONG").length;
    const S = got.filter((o) => o.side === "SHORT").length;
    $("mtf-note").textContent =
      L >= 3 ? "Selaras: " + L + " dari 4 timeframe mengarah LONG." :
      S >= 3 ? "Selaras: " + S + " dari 4 timeframe mengarah SHORT." :
      (L && S) ? "Terbelah: " + L + " LONG vs " + S + " SHORT — sinyal campuran." :
      L > S ? "Cenderung LONG: " + L + " dari 4 timeframe LONG, sisanya NETRAL." :
      S > L ? "Cenderung SHORT: " + S + " dari 4 timeframe SHORT, sisanya NETRAL." :
      "Mayoritas NETRAL antar-timeframe.";
  };
  if (hit && Date.now() - hit.ts < 300000) { paint(hit.data); return; }
  const data = {};
  paint(data);
  await Promise.all(MTF_TFS_WEB.map(async (tf) => {
    try { data[tf] = await fetchScore(symbol, tf); } catch (e) { data[tf] = null; }
    paint(data);
  }));
  if (my === mtfTokenWeb) mtfCacheWeb[key] = { ts: Date.now(), data };
}

/* ---------------- Salin teks analisis ---------------- */
let lastSignalPayload = null;
$("btn-share-text").addEventListener("click", async () => {
  const s = lastSignalPayload;
  if (!s) return;
  const snap = s.snapshot || {};
  const panah = s.side === "LONG" ? "📈" : s.side === "SHORT" ? "📉" : "➡️";
  const L = [];
  L.push(panah + " " + s.symbol + " — " + s.side + " · keyakinan " + s.confidence +
    "% · skor teknikal " + (s.technical_score > 0 ? "+" : "") + s.technical_score);
  L.push("🏛️ Binance Futures · harga " + fmtPrice(s.price) +
    (snap.change_24h_pct != null ? " · 24j " + (snap.change_24h_pct >= 0 ? "+" : "") + Number(snap.change_24h_pct).toFixed(2) + "%" : ""));
  L.push("");
  L.push("Indikator:");
  if (snap.rsi14 != null) L.push("• RSI (14): " + Number(snap.rsi14).toFixed(1));
  if (snap.macd_histogram != null) L.push("• MACD histogram: " + (snap.macd_histogram >= 0 ? "positif" : "negatif"));
  if (snap.trend) L.push("• Tren: " + snap.trend);
  if (s.side !== "NEUTRAL" && s.stop_loss && s.take_profit) {
    L.push("");
    L.push("Rencana mesin:");
    L.push("• Entry: " + fmtPrice(s.price));
    L.push("• TP: " + fmtPrice(s.take_profit));
    L.push("• SL: " + fmtPrice(s.stop_loss));
  }
  if (s.ai) {
    L.push("");
    L.push("AI (" + (s.ai.model_used || "model") + "): " + s.ai.signal + " · " + s.ai.confidence + "%");
    if (s.ai.reasoning) L.push(s.ai.reasoning);
  }
  if (s.reasons && s.reasons.length) {
    L.push("");
    L.push("Alasan utama:");
    s.reasons.slice(0, 3).forEach((r) => L.push("• " + r));
  }
  L.push("");
  L.push("Sinyal teknikal + AI — bukan saran keuangan.");
  L.push("Dibuat dengan DTRADE · " + new Date().toLocaleString("id-ID",
    { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) + " WIB");
  try {
    await navigator.clipboard.writeText(L.join("\n"));
    toast("Teks analisis disalin — tempel di WhatsApp/Telegram/dll.");
  } catch (e) {
    toast("Gagal menyalin teks: " + e.message);
  }
});

/* ---------------- Berita ---------------- */
let newsItems = [];
let newsLoaded = false;
let newsSource = "Semua";
function fmtRel(ts) {
  if (!ts) return "";
  const m = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
  if (m < 60) return m + " mnt lalu";
  const j = Math.floor(m / 60);
  if (j < 24) return j + " jam lalu";
  return Math.floor(j / 24) + " hari lalu";
}
function renderNews() {
  const q = $("news-q").value.trim().toLowerCase();
  const arr = newsItems.filter((n) =>
    (newsSource === "Semua" || n.source === newsSource) &&
    (!q || (n.title + " " + n.desc).toLowerCase().includes(q)));
  const grid = $("news-grid");
  if (!arr.length) {
    grid.innerHTML = '<p class="muted">Tidak ada berita yang cocok.</p>';
    return;
  }
  grid.innerHTML = arr.map((n) =>
    '<a class="news-card" href="' + esc(n.link) + '" target="_blank" rel="noopener">' +
    (n.img ? '<img loading="lazy" src="' + esc(n.img) + '" alt="" onerror="this.remove()">' : "") +
    '<div class="news-body"><div class="news-meta"><b>' + esc(n.source) + "</b> · " + fmtRel(n.ts) + "</div>" +
    '<div class="news-title">' + esc(n.title) + "</div>" +
    '<div class="news-desc">' + esc(n.desc) + "</div></div></a>"
  ).join("");
  const sources = ["Semua"].concat([...new Set(newsItems.map((n) => n.source))]);
  $("news-chips").innerHTML = sources.map((s) =>
    '<button class="news-chip' + (s === newsSource ? " on" : "") + '" data-source="' + esc(s) + '">' + esc(s) + "</button>"
  ).join("");
}
async function loadNews() {
  try {
    const d = await api("/api/news");
    newsItems = d.items || [];
    newsLoaded = true;
    renderNews();
  } catch (e) {
    $("news-grid").innerHTML = '<p class="muted">Gagal memuat berita: ' + esc(e.message) + "</p>";
  }
}
$("news-q").addEventListener("input", renderNews);
$("news-chips").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-source]");
  if (b) { newsSource = b.getAttribute("data-source"); renderNews(); }
});
$("btn-news-refresh").addEventListener("click", loadNews);
if ("IntersectionObserver" in window) {
  const io = new IntersectionObserver((es) => {
    if (es.some((e) => e.isIntersecting) && !newsLoaded) { loadNews(); io.disconnect(); }
  }, { rootMargin: "200px" });
  io.observe($("panel-news"));
}
