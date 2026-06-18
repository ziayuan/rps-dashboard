const tableOrder = [
  ["us_1d_signals", "美股信号"],
  ["us_1d_watchlist", "美股观察池"],
  ["crypto_4h_signals", "Crypto 4H 信号"],
  ["crypto_4h_watchlist", "Crypto 4H 观察池"],
  ["macro_1d_watchlist", "宏观 RPS"],
];

const BASE_COLUMNS = [
  "symbol",
  "tier",
  "date",
  "close",
  "rps_max",
];

const US_RPS_COLUMNS = ["rps30", "rps50", "rps120", "rps250"];
const CRYPTO_RPS_COLUMNS = ["rps30", "rps90", "rps180"];
const MACRO_RPS_COLUMNS = ["rps20", "rps60", "rps120"];
const MACRO_COLUMNS = [
  "symbol",
  "asset_name",
  "macro_group",
  "tier",
  "date",
  "close",
  "rps_max",
  ...MACRO_RPS_COLUMNS,
  "strong_trend",
  "volume",
];
const RPS_THRESHOLD_INPUTS = [
  ["rps30", "rps30Min"],
  ["rps50", "rps50Min"],
  ["rps120", "rps120Min"],
  ["rps250", "rps250Min"],
];

const TRAILING_COLUMNS = [
  "strong_trend",
  "pocket_pivot",
  "core_watchlist",
  "volume_signature",
  "volume",
];

const columnLabels = {
  symbol: "Symbol",
  tier: "Tier",
  date: "Date",
  close: "Close",
  rps_max: "RPS Max",
  rps_short: "RPS Short",
  rps50: "RPS 50",
  rps20: "RPS 20",
  rps60: "RPS 60",
  rps120: "RPS 120",
  rps250: "RPS 250",
  rps30: "RPS 30",
  rps90: "RPS 90",
  rps180: "RPS 180",
  asset_name: "Asset",
  macro_group: "Group",
  strong_trend: "Strong Trend",
  pocket_pivot: "Pocket Pivot",
  core_watchlist: "Core Watchlist",
  volume_signature: "Volume Signature",
  volume: "Volume",
};

const helpText = {
  symbol: "股票或加密货币代码。",
  tier: "研究优先级：A=核心强势且出现口袋支点；B=趋势较强但未完全满足核心条件；C=进入观察池但结构还不完整。",
  date: "该行信号对应的行情 K 线时间。美股通常显示为交易日收盘对应的 UTC 时间。",
  close: "该 K 线收盘价。美股为 Polygon adjusted close，加密为 Binance 对应周期收盘价。",
  rps_max: "多个 RPS 周期中的最高相对强弱分数。越接近 100，表示在当前股票池/币种池里越强。",
  rps_short: "短周期 RPS。美股是 RPS50；加密 4H 是 RPS30，即最近 30 根 4H K 线的相对表现。",
  rps50: "美股最近 50 根日线的相对强弱排名。100 表示在当前可交易股票池里最强的一批。",
  rps20: "宏观资产最近 20 根日线的相对强弱排名，用来看约 1 个月的大类资产强度。",
  rps60: "宏观资产最近 60 根共同交易日的相对强弱排名，用来看约 1 个季度的大类资产强度。",
  rps120: "最近 120 根 K 线/共同交易日的相对强弱排名。美股约半年，宏观资产约半年。",
  rps250: "美股最近 250 根日线的相对强弱排名，用来看接近一年维度的强度。",
  rps30: "美股最近 30 根日线的相对强弱排名；加密货币是最近 30 根 4H K 线。",
  rps90: "加密货币最近 90 根 4H K 线的相对强弱排名。",
  rps180: "加密货币最近 180 根 4H K 线的相对强弱排名。",
  asset_name: "宏观资产名称，例如 Nasdaq 100、Gold、Bitcoin。",
  macro_group: "宏观资产类别，例如科技成长、黄金、美元、长债、商品或加密。",
  strong_trend: "趋势结构是否健康。美股大致要求价格在关键均线之上且接近高位；加密要求价格在 MA20/MA50 上方。",
  pocket_pivot: "是否出现口袋支点：上涨 K 线成交量超过过去 10 根下跌 K 线中的最大成交量，并且价格位置不过度延展。",
  core_watchlist: "是否进入核心观察池。美股当前要求 RPS Max >= 95 且 RPS50 >= 90，表示既有一个周期非常强，短期强度也没有明显掉队。",
  volume_signature: "是否满足口袋支点的量能特征。它只说明量能合格，不代表整体买点一定成立。",
  volume: "成交量。美股是股票成交量；加密脚本使用 Binance quote volume，更接近 USDT 成交额。",
};

let state = {
  payload: null,
  panels: null,
  active: "us_1d_signals",
  selectedReport: null,
  sortKey: "rps_max",
  sortDir: "desc",
  lastLoadedFinishedAt: null,
};

const $ = (id) => document.getElementById(id);
const actionButtonIds = [
  "backfillUsBtn",
  "backfillCryptoBtn",
  "backfillMacroBtn",
  "repairMissingUsBtn",
  "rankUsBtn",
  "rankCryptoBtn",
  "rankMacroBtn",
  "updateResearchPanelsBtn",
];

function isTrue(value) {
  return String(value).toLowerCase() === "true";
}

function numeric(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function tableColumns(tableKey) {
  if (tableKey.startsWith("macro")) return MACRO_COLUMNS;
  const rpsColumns = tableKey.startsWith("crypto") ? CRYPTO_RPS_COLUMNS : US_RPS_COLUMNS;
  return [...BASE_COLUMNS, ...rpsColumns, ...TRAILING_COLUMNS];
}

function getSelectedTiers() {
  return Array.from(document.querySelectorAll(".tier-choice:checked")).map((input) => input.dataset.tier);
}

function updateTierButton() {
  const selected = getSelectedTiers();
  const label = selected.length ? selected.join(", ") : "无";
  $("tierFilter").firstChild.textContent = label;
}

function setTierMenuOpen(open) {
  $("tierMenu").hidden = !open;
  $("tierFilter").setAttribute("aria-expanded", String(open));
  $("tierFilter").closest(".tier-filter").classList.toggle("open", open);
}

function tierMatches(row, selectedTiers) {
  const tier = String(row.tier || "").toUpperCase();
  return selectedTiers.includes(tier);
}

function rpsThresholdPasses(row) {
  if (!state.active.startsWith("us")) return true;
  for (const [key, inputId] of RPS_THRESHOLD_INPUTS) {
    const raw = $(inputId).value.trim();
    if (!raw) continue;
    const threshold = Number(raw);
    if (!Number.isFinite(threshold)) continue;
    const value = numeric(row[key]);
    if (value == null || value < threshold) return false;
  }
  return true;
}

function formatValue(key, value) {
  if (value == null || value === "") return "-";
  if (["rps_max", "rps_short", "rps20", "rps50", "rps60", "rps120", "rps250", "rps30", "rps90", "rps180"].includes(key)) {
    const n = numeric(value);
    return n == null ? value : n.toFixed(1);
  }
  if (["close", "ma10", "ma20", "ma50"].includes(key)) {
    const n = numeric(value);
    return n == null ? value : n.toFixed(2);
  }
  if (key === "volume") {
    const n = numeric(value);
    return n == null ? value : Intl.NumberFormat("en", { notation: "compact" }).format(n);
  }
  if (key === "date") {
    return String(value).replace("T", " ").replace("+00:00", "").replace("Z", "");
  }
  return value;
}

function renderTabs() {
  const tabs = $("tabs");
  tabs.innerHTML = "";
  for (const [key, label] of tableOrder) {
    const rows = state.payload?.tables?.[key]?.rows || [];
    const total = state.payload?.summary?.[key] ?? rows.length;
    const button = document.createElement("button");
    button.className = `tab ${state.active === key ? "active" : ""}`;
    button.innerHTML = `<span>${label}</span><strong>${total}</strong>`;
    button.addEventListener("click", () => {
      state.active = key;
      render();
    });
    tabs.appendChild(button);
  }
}

function currentRows() {
  const rows = [...(state.payload?.tables?.[state.active]?.rows || [])];
  const query = $("searchInput").value.trim().toLowerCase();
  const selectedTiers = getSelectedTiers();
  const onlyStrong = $("onlyStrong").checked;
  const onlyCore = $("onlyCore").checked;
  return rows
    .filter((row) => {
      if (query && !Object.values(row).some((value) => String(value).toLowerCase().includes(query))) return false;
      if (!tierMatches(row, selectedTiers)) return false;
      if (!rpsThresholdPasses(row)) return false;
      if (onlyStrong && !isTrue(row.strong_trend)) return false;
      if (onlyCore && !isTrue(row.core_watchlist)) return false;
      return true;
    })
    .sort((a, b) => {
      const av = numeric(a[state.sortKey]) ?? String(a[state.sortKey] || "");
      const bv = numeric(b[state.sortKey]) ?? String(b[state.sortKey] || "");
      const result = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
      return state.sortDir === "asc" ? result : -result;
    });
}

function renderTable() {
  const columns = tableColumns(state.active);
  if (!columns.includes(state.sortKey)) {
    state.sortKey = "rps_max";
    state.sortDir = "desc";
  }
  const rows = currentRows();
  const [_, label] = tableOrder.find(([key]) => key === state.active) || [state.active, state.active];
  $("tableTitle").textContent = label;
  $("tableMeta").textContent = `当前显示 ${rows.length} 行`;
  const table = state.payload?.tables?.[state.active];
  $("downloadLink").href = table?.path ? `/api/download?path=${encodeURIComponent(table.path)}` : "#";

  const head = $("tableHead");
  head.innerHTML = "";
  for (const key of columns) {
    const th = document.createElement("th");
    const label = columnLabels[key] || key;
    th.textContent = key === state.sortKey ? `${label} ${state.sortDir === "desc" ? "↓" : "↑"}` : label;
    th.title = helpText[key] || key;
    th.addEventListener("click", () => {
      $("fieldHelp").textContent = `${label}: ${helpText[key] || "暂无说明。"}`;
      if (state.sortKey === key) state.sortDir = state.sortDir === "desc" ? "asc" : "desc";
      else {
        state.sortKey = key;
        state.sortDir = "desc";
      }
      renderTable();
    });
    head.appendChild(th);
  }

  const body = $("tableBody");
  body.innerHTML = "";
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const key of columns) {
      const td = document.createElement("td");
      const value = row[key];
      if (key === "tier") {
        const badge = document.createElement("span");
        badge.className = `tier ${String(value).toLowerCase() === "a" ? "tier-a" : ""}`;
        badge.textContent = value || "-";
        td.appendChild(badge);
      } else if (String(value).toLowerCase() === "true" || String(value).toLowerCase() === "false") {
        td.className = isTrue(value) ? "bool-true" : "bool-false";
        td.textContent = isTrue(value) ? "true" : "false";
      } else {
        td.textContent = formatValue(key, value);
      }
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

function renderSummary() {
  const reports = state.payload?.availableReports || [];
  const select = $("reportSelect");
  const current = state.payload?.reportDate || state.selectedReport || "";
  select.innerHTML = "";
  for (const report of reports) {
    const option = document.createElement("option");
    option.value = report;
    option.textContent = report;
    option.selected = report === current;
    select.appendChild(option);
  }
  if (!reports.length) {
    const option = document.createElement("option");
    option.textContent = "-";
    select.appendChild(option);
  }
  $("marketDate").textContent = state.payload?.marketDate || "-";
  $("signalCount").textContent = String(state.payload?.summary?.us_1d_signals ?? 0);
  $("watchlistCount").textContent = String(state.payload?.summary?.us_1d_watchlist ?? 0);
}

function renderStatus(status) {
  $("runState").textContent = status.running ? "更新中" : status.exitCode === 0 ? "完成" : status.error ? "失败" : "空闲";
  for (const id of actionButtonIds) {
    $(id).disabled = status.running;
  }
  $("logs").textContent = (status.logs || []).join("\n");
  $("logs").scrollTop = $("logs").scrollHeight;
}

function compactNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? Intl.NumberFormat("en", { notation: "compact" }).format(n) : "-";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function panelNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(1) : "-";
}

function renderMiniTable(targetId, columns, rows, emptyText) {
  const target = $(targetId);
  if (!rows || !rows.length) {
    target.className = "mini-table-wrap empty-panel";
    target.textContent = emptyText;
    return;
  }
  target.className = "mini-table-wrap";
  const head = columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join("");
  const body = rows
    .map((row) => {
      const cells = columns
        .map((column) => `<td>${escapeHtml(column.format ? column.format(row[column.key], row) : row[column.key])}</td>`)
        .join("");
      return `<tr>${cells}</tr>`;
    })
    .join("");
  target.innerHTML = `<table class="mini-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderResearchPanels() {
  const panels = state.panels;
  if (!panels?.available) {
    $("researchPanelMeta").textContent = panels?.message || "尚未生成，点击按钮后才会更新。";
    $("themePanelCount").textContent = "-";
    $("leadershipPanelCount").textContent = "-";
    $("macroRegimeBadge").textContent = "-";
    $("macroRegimeSummary").textContent = "暂无缓存。";
    renderMiniTable("themePanelBody", [], [], "点击“更新研究面板”后生成。");
    renderMiniTable("currentLeadersBody", [], [], "暂无缓存。");
    renderMiniTable("formerLeadersBody", [], [], "暂无缓存。");
    renderMiniTable("macroRegimeBody", [], [], "点击“更新研究面板”后生成。");
    return;
  }

  const source = panels.source || {};
  $("researchPanelMeta").textContent =
    `报告 ${panels.reportDate || "-"} / 行情 ${panels.marketDate || "-"} / A+B+Core ${source.abCoreCount ?? 0} 只 / ` +
    `公司资料补充 ${source.fetched ?? 0} 只`;

  const themeRows = panels.themePanel?.rows || [];
  $("themePanelCount").textContent = `${themeRows.length} 个主题`;
  renderMiniTable(
    "themePanelBody",
    [
      { key: "theme", label: "主题" },
      { key: "count", label: "数量" },
      { key: "aCount", label: "A" },
      { key: "bCount", label: "B" },
      { key: "avgRps30", label: "均30", format: panelNumber },
      { key: "avgRps50", label: "均50", format: panelNumber },
      { key: "avgRps120", label: "均120", format: panelNumber },
      { key: "medianRps50", label: "中50", format: panelNumber },
      { key: "topSymbols", label: "代表标的" },
    ],
    themeRows,
    "暂无主题数据。"
  );

  const current = panels.leadership?.current || [];
  const former = panels.leadership?.former || [];
  $("leadershipPanelCount").textContent = `${current.length} 现任 / ${former.length} 老领导`;
  renderMiniTable(
    "currentLeadersBody",
    [
      { key: "symbol", label: "代码" },
      { key: "theme", label: "主题" },
      { key: "tier", label: "Tier" },
      { key: "rpsMax", label: "RPS", format: panelNumber },
      { key: "rps50", label: "RPS50", format: panelNumber },
      { key: "reason", label: "理由" },
    ],
    current.slice(0, 20),
    "暂无现任领导股数据。"
  );
  renderMiniTable(
    "formerLeadersBody",
    [
      { key: "symbol", label: "代码" },
      { key: "status", label: "状态" },
      { key: "drawdownPct", label: "回撤%", format: panelNumber },
      { key: "lastLeaderDate", label: "上次领先" },
      { key: "source", label: "来源" },
    ],
    former.slice(0, 20),
    "暂无老领导股数据。"
  );

  const macro = panels.macroRegime || {};
  $("macroRegimeBadge").textContent = macro.regime || "-";
  $("macroRegimeSummary").textContent = macro.summary || "暂无宏观摘要。";
  renderMiniTable(
    "macroRegimeBody",
    [
      { key: "name", label: "指标" },
      { key: "value", label: "数值", format: (value) => (Number.isFinite(Number(value)) ? panelNumber(value) : value || "-") },
      { key: "signal", label: "信号" },
      { key: "detail", label: "说明" },
    ],
    macro.indicators || [],
    "暂无宏观指标。"
  );
}

function renderHealth(health) {
  const us = health?.us || {};
  const crypto = health?.crypto || {};
  const macro = health?.macro || {};
  const usPanel = $("usHealthText").parentElement;
  usPanel.classList.remove("warn", "alert");
  if (us.status === "warn" || us.status === "alert") usPanel.classList.add(us.status);
  $("usHealthText").textContent =
    `Universe ${compactNumber(us.universeCount)} / CSV ${compactNumber(us.localCsvCount)} / ` +
    `Rankable ${compactNumber(us.rankableHistoryCount)} / 缺 CSV ${compactNumber(us.missingCsvCount)} / ` +
    `短历史 ${compactNumber(us.shortHistoryCount)} / 最新 ${us.latestDate || "-"}`;

  const cryptoPanel = $("cryptoHealthText").parentElement;
  cryptoPanel.classList.remove("warn", "alert");
  if (crypto.status === "warn" || crypto.status === "alert") cryptoPanel.classList.add(crypto.status);
  $("cryptoHealthText").textContent =
    `CSV ${compactNumber(crypto.localCsvCount)} / Rankable ${compactNumber(crypto.rankableHistoryCount)} / ` +
    `短历史 ${compactNumber(crypto.shortHistoryCount)} / 最新 ${crypto.latestDate || "-"}`;

  const macroPanel = $("macroHealthText").parentElement;
  macroPanel.classList.remove("warn", "alert");
  if (macro.status === "warn" || macro.status === "alert") macroPanel.classList.add(macro.status);
  $("macroHealthText").textContent =
    `CSV ${compactNumber(macro.localCsvCount)} / Rankable ${compactNumber(macro.rankableHistoryCount)} / ` +
    `短历史 ${compactNumber(macro.shortHistoryCount)} / 最新 ${macro.latestDate || "-"}`;
}

async function loadHealth() {
  const response = await fetch("/api/health");
  renderHealth(await response.json());
}

async function loadPanels() {
  const query = new URLSearchParams();
  if (state.selectedReport) query.set("date", state.selectedReport);
  const response = await fetch(`/api/panels?${query.toString()}`);
  state.panels = await response.json();
  renderResearchPanels();
}

function render() {
  renderSummary();
  renderTabs();
  renderTable();
}

async function loadTables() {
  const query = new URLSearchParams({ limit: "1000" });
  if (state.selectedReport) query.set("date", state.selectedReport);
  const response = await fetch(`/api/tables?${query.toString()}`);
  state.payload = await response.json();
  state.selectedReport = state.payload.reportDate || state.selectedReport;
  if (!state.payload.tables?.[state.active]) state.active = "us_1d_signals";
  render();
}

async function pollStatus() {
  const response = await fetch("/api/status");
  const status = await response.json();
  renderStatus(status);
  if (!status.running && status.exitCode === 0 && status.finishedAt !== state.lastLoadedFinishedAt) {
    state.lastLoadedFinishedAt = status.finishedAt;
    await loadTables();
    await loadHealth();
    await loadPanels();
  }
}

async function runAction(action) {
  const query = new URLSearchParams({ action });
  if (action === "research-panels" && state.selectedReport) query.set("date", state.selectedReport);
  const response = await fetch(`/api/refresh?${query.toString()}`, { method: "POST" });
  if (!response.ok && response.status !== 409) {
    $("logs").textContent = `refresh failed: ${response.status}`;
  }
  await pollStatus();
}

$("backfillUsBtn").addEventListener("click", () => runAction("us-backfill"));
$("backfillCryptoBtn").addEventListener("click", () => runAction("crypto-backfill"));
$("backfillMacroBtn").addEventListener("click", () => runAction("macro-backfill"));
$("repairMissingUsBtn").addEventListener("click", () => runAction("us-repair-missing"));
$("rankUsBtn").addEventListener("click", () => runAction("us-rank"));
$("rankCryptoBtn").addEventListener("click", () => runAction("crypto-rank"));
$("rankMacroBtn").addEventListener("click", () => runAction("macro-rank"));
$("updateResearchPanelsBtn").addEventListener("click", () => runAction("research-panels"));
$("reloadBtn").addEventListener("click", async () => {
  await loadTables();
  await loadPanels();
});
$("reportSelect").addEventListener("change", async (event) => {
  state.selectedReport = event.target.value;
  await loadTables();
  await loadPanels();
});
$("searchInput").addEventListener("input", renderTable);
$("tierFilter").addEventListener("click", () => {
  setTierMenuOpen($("tierMenu").hidden);
});
for (const input of document.querySelectorAll(".tier-choice")) {
  input.addEventListener("change", () => {
    updateTierButton();
    renderTable();
  });
}
for (const [, inputId] of RPS_THRESHOLD_INPUTS) {
  $(inputId).addEventListener("input", renderTable);
}
document.addEventListener("click", (event) => {
  if (!$("tierMenu").hidden && !$("tierFilter").closest(".tier-filter").contains(event.target)) {
    setTierMenuOpen(false);
  }
});
$("onlyStrong").addEventListener("change", renderTable);
$("onlyCore").addEventListener("change", renderTable);

updateTierButton();
loadTables();
loadHealth();
loadPanels();
pollStatus();
setInterval(pollStatus, 2500);
