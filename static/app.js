/* Интерфейс работает с JSON API Flask. Данные исследований хранятся на сервере. */
"use strict";

const state = {datasets: [], runs: [], run: null, datasetId: null, chartMode: "future", compared: [], dirty: false, sensitivityPoints: null, sensitivityVersion: 0};
const modelNames = {linear_ridge: "Линейная Ridge", seasonal_naive: "Сезонный наивный", mean7: "Среднее за 7 дней"};
const defaults = {name: "", demand_change_pct: 0, price_change_pct: 0, elasticity: 1, promo_uplift_pct: 0,
  initial_stock_days: 7, target_stock_days: 14, lead_time_days: 3, supply_delay_days: 0,
  review_period_days: 7, unit_cost_ratio: 0.65, holding_cost_rate_daily: 0.001, order_fixed_cost: 2, promo_budget: 0};
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
const number = (value, digits = 1) => value !== null && value !== undefined && Number.isFinite(Number(value))
  ? Number(value).toLocaleString("ru-RU", {maximumFractionDigits: digits, minimumFractionDigits: digits}) : "—";
const chartColors = {actual: "#5264d8", forecast: "#149b91", deficit: "#c48a43", grid: "#e9edf3", axis: "#68768e", ink: "#34415a"};
const dateLabel = value => { const date = new Date(String(value).slice(0, 10) + "T12:00:00"); return Number.isNaN(date.getTime()) ? String(value || "—") : date.toLocaleDateString("ru-RU"); };
const currency = () => state.run?.currency || state.datasets.find(item => Number(item.id) === Number(state.run?.dataset_id))?.currency || "USD";
const money = value => `${number(value, 2)} ${currency()}`;
const percent = value => value === null || value === undefined ? "—" : `${number(Number(value) * 100, 1)}%`;
let toastTimer;

function notify(message) {
  $("toast").textContent = message;
  $("toast").classList.remove("hidden");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => $("toast").classList.add("hidden"), 5000);
}
function reportError(error) {
  $("global-error").textContent = error.message || "Не удалось выполнить действие. Повторите попытку.";
  $("global-error").classList.remove("hidden");
  $("global-error").scrollIntoView({behavior: "smooth", block: "nearest"});
}
async function api(path, options = {}) {
  $("global-error").classList.add("hidden");
  const headers = {...options.headers};
  const method = options.method || "GET";
  if (method !== "GET") headers["X-CSRF-Token"] = document.querySelector('meta[name="csrf-token"]').content;
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  let response;
  try { response = await fetch(path, {...options, method, headers, credentials: "same-origin"}); }
  catch (_) { throw new Error("Сервер недоступен. Проверьте, что приложение запущено, и повторите действие."); }
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : {};
  if (!response.ok) throw new Error(data.error || `Сервер не выполнил запрос (${response.status}).`);
  return data;
}
async function busy(button, action, label = "Выполняется…") {
  const old = button.innerHTML;
  button.disabled = true;
  button.textContent = label;
  button.classList.add("busy-label");
  let success = false;
  try { await action(); success = true; }
  catch (error) { reportError(error); }
  finally { button.innerHTML = old; button.disabled = false; button.classList.remove("busy-label"); }
  return success;
}
function changeView(name) {
  if (!["data", "forecast", "scenarios", "history"].includes(name)) return;
  document.querySelectorAll(".view").forEach(view => view.classList.toggle("hidden", view.id !== `view-${name}`));
  document.querySelectorAll(".nav-item").forEach(button => {
    const active = button.dataset.view === name;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  const titles = {data: "Данные", forecast: "Прогноз продаж", scenarios: "Сценарии", history: "История исследований"};
  $("breadcrumb-title").textContent = titles[name];
  document.title = `${titles[name]} — Контур`;
  if (name === "history") loadHistory().catch(reportError);
  if (state.run && name === "forecast") renderForecast();
  if (state.run && name === "scenarios") { renderComparison(); renderDaily(); }
  window.scrollTo({top: 0, behavior: "smooth"});
}
function table(headers, rows, classes = []) {
  if (!rows.length) return '<div class="empty-inline">Нет данных для отображения.</div>';
  const numeric = value => /^[−+–-]?\d[\d\s\u00a0\u202f.,]*(?:\s?%)?$/.test(String(value));
  const numericColumns = headers.map((_, index) => index > 0 && rows.some(row => numeric(row[index])));
  return `<table><thead><tr>${headers.map((item, index) => `<th scope="col"${numericColumns[index] ? ' class="cell-number"' : ""}>${esc(item)}</th>`).join("")}</tr></thead><tbody>${rows.map((row, index) => `<tr class="${classes[index] || ""}">${row.map((value, column) => `<td${numericColumns[column] ? ' class="cell-number"' : ""}>${esc(value)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
function options(select, values, label, current) {
  select.innerHTML = values.map(item => `<option value="${esc(item.id ?? item)}">${esc(label(item))}</option>`).join("");
  if (current !== undefined && [...select.options].some(option => String(option.value) === String(current))) select.value = current;
}
function uniqueSkus() { return [...new Set((state.run?.forecast || []).map(row => row.sku))].sort(); }

async function loadDatasets(preferredId) {
  const data = await api("/api/datasets");
  state.datasets = data.datasets || [];
  const wanted = preferredId ?? state.datasetId ?? state.datasets[0]?.id;
  options($("dataset-select"), state.datasets, item => item.name, wanted);
  state.datasetId = $("dataset-select").value;
  $("create-run").disabled = !state.datasetId;
  if (state.datasetId) await selectDataset(state.datasetId);
  else { $("dataset-info").innerHTML = '<div class="empty-inline">Добавьте первый CSV с реальными продажами.</div>'; $("data-preview").innerHTML = ""; }
}
async function selectDataset(id) {
  state.datasetId = id;
  const item = state.datasets.find(dataset => String(dataset.id) === String(id));
  if (!item) return;
  const products = Array.isArray(item.products) ? item.products.length : (item.products ?? item.product_count ?? 0);
  const productWord = {one: "товар", few: "товара", many: "товаров", other: "товара"}[new Intl.PluralRules("ru-RU").select(Number(products))];
  const source = typeof item.source === "string" ? item.source : (item.source?.name || "Загруженные данные");
  $("dataset-info").innerHTML = `<div class="dataset-summary"><strong>${esc(item.name)}</strong><div class="dataset-source">${esc(source)} · валюта ${esc(item.currency || "USD")}</div><div class="dataset-period">${esc(dateLabel(item.start_date))} — ${esc(dateLabel(item.end_date))}</div></div><div class="dataset-stats"><div><strong>${number(item.rows ?? item.row_count, 0)}</strong><span>наблюдений</span></div><div><strong>${number(products, 0)}</strong><span>${productWord}</span></div><div><strong>${number(item.days ?? Math.round((new Date(item.end_date) - new Date(item.start_date)) / 86400000) + 1, 0)}</strong><span>дней истории</span></div></div><div class="quality-list"><span class="quality-tag">✓ Проверка формата пройдена</span><span class="quality-tag">✓ Полный календарь</span><span class="quality-tag warn">Запасы и затраты задаются отдельно</span></div>`;
  $("preview-count").textContent = "Загрузка…";
  try {
    const preview = await api(`/api/datasets/${encodeURIComponent(id)}/preview`);
    const rows = preview.rows || [];
    const columns = ["date", "sku", "store_id", "sales", "price", "snap", "event"].filter(key => rows.some(row => key in row));
    const labels = {date: "Дата", sku: "Товар", store_id: "Магазин", sales: "Продажи, ед.", price: `Цена, ${item.currency || "USD"}`, snap: "SNAP", event: "Событие"};
    $("data-preview").innerHTML = table(columns.map(key => labels[key]), rows.map(row => columns.map(key => key === "date" ? dateLabel(row[key]) : row[key])));
    $("preview-count").textContent = `${rows.length} строк`;
  } catch (error) { $("preview-count").textContent = "Ошибка"; throw error; }
}
function parseCalendar(text) {
  if (!text.trim()) return undefined;
  const lines = text.trim().split(/\r?\n/).filter(line => line.trim());
  if (lines[0].trim().toLowerCase() !== "date,snap,event") throw new Error("В календаре первая строка должна быть date,snap,event, разделитель — запятая.");
  return lines.slice(1).map((line, index) => {
    const parts = line.split(",").map(part => part.trim());
    if (parts.length !== 3 || !/^\d{4}-\d{2}-\d{2}$/.test(parts[0]) || !["0", "1"].includes(parts[1]) || !["0", "1"].includes(parts[2])) throw new Error(`Некорректная строка календаря ${index + 2}. Нужны дата, 0/1, 0/1.`);
    return {date: parts[0], snap: Number(parts[1]), event: Number(parts[2])};
  });
}
function canDiscard() { return !state.dirty || window.confirm("В форме сценария есть несохранённые изменения. Открыть другое исследование и сбросить черновик?"); }
async function syncRunInputs(run) {
  if (!state.datasets.some(item => String(item.id) === String(run.dataset_id))) {
    await loadDatasets(run.dataset_id);
  } else {
    $("dataset-select").value = run.dataset_id;
    await selectDataset(run.dataset_id);
  }
  $("run-name").value = run.name;
  $("model-select").value = run.model;
  $("horizon").value = run.horizon;
  $("future-calendar").value = run.future_calendar_supplied
    ? "date,snap,event\n" + (run.future_calendar || []).map(row => `${row.date},${row.snap},${row.event}`).join("\n")
    : "";
}
async function openRun(id, view = "forecast", ask = true) {
  if (ask && !canDiscard()) return;
  const data = await api(`/api/runs/${encodeURIComponent(id)}`);
  await syncRunInputs(data.run);
  setRun(data.run);
  changeView(view);
}
function setRun(run) {
  state.run = run;
  state.compared = [...(run.scenarios || [])];
  document.querySelectorAll(".run-content").forEach(node => node.classList.toggle("hidden", !run));
  document.querySelectorAll(".run-empty").forEach(node => node.classList.toggle("hidden", Boolean(run)));
  document.querySelectorAll(".needs-run").forEach(node => node.disabled = !run);
  if (!run) return;
  $("forecast-subtitle").textContent = `${run.name} · ${run.dataset_name || state.datasets.find(item => Number(item.id) === Number(run.dataset_id))?.name || "Набор продаж"} · ${modelNames[run.model] || run.model} · ${run.horizon} дней`;
  $("footer-run").textContent = run.name;
  $("export-forecast").href = `/api/runs/${run.id}/export?kind=forecast`;
  $("export-comparison").href = `/api/runs/${run.id}/export?kind=comparison`;
  $("export-forecast").classList.remove("hidden");
  $("export-comparison").classList.remove("hidden");
  const skus = uniqueSkus();
  options($("forecast-sku"), skus, value => value);
  options($("metrics-scope"), ["ALL", ...skus], value => value === "ALL" ? "Все товары вместе" : value);
  options($("daily-sku"), skus, value => value);
  $("validation-period").value = "last";
  $("validation-period").querySelector('option[value="pooled"]').disabled = !Array.isArray(run.rolling_metrics) || !run.rolling_metrics.length;
  const assumptions = run.assumptions;
  let text = "Будущая цена равна последней наблюдаемой. Если отдельный будущий календарь не задан, будущие события и SNAP приняты равными 0.";
  if (typeof assumptions === "string") text = assumptions;
  else if (Array.isArray(assumptions)) text = assumptions.join(" ");
  else if (assumptions && typeof assumptions === "object") text = Object.values(assumptions).filter(value => typeof value === "string").join(" · ") || text;
  $("forecast-assumptions").innerHTML = `<strong>Допущения будущего периода.</strong> ${esc(text)}`;
  renderForecast(); renderMetrics(); renderScenarios(); resetScenario();
}
function renderForecast() {
  if (!state.run) return;
  const run = state.run;
  const scores = (run.metrics || []).find(row => row.sku === "ALL" && row.model === run.model) || {};
  const sum = (run.forecast || []).reduce((total, row) => total + Number(row.prediction || 0), 0);
  const windows = Array.isArray(run.validation_windows) ? run.validation_windows.length : 1;
  $("forecast-kpis").innerHTML = [
    ["Прогноз на горизонт", number(sum, 0), "ед.", `${uniqueSkus().length} товара · ${run.horizon} дней`],
    ["WAPE на последнем окне", number(scores.WAPE_percent, 2), "%", "Суммарная относительная ошибка"],
    ["MAE на последнем окне", number(scores.MAE, 2), "ед.", "Среднее абсолютное отклонение"],
    ["Временных проверок", String(windows), "", "Обучение предшествует проверке"],
  ].map(([label, value, unit, description]) => `<div class="metric-card"><span class="metric-label">${esc(label)}</span><div class="metric-value">${esc(value)}<small>${esc(unit)}</small></div><span class="metric-description">${esc(description)}</span></div>`).join("");
  const sku = $("forecast-sku").value;
  let actual, predicted, boundary;
  if (state.chartMode === "backtest") {
    const backtest = (run.backtest || []).filter(row => row.sku === sku && row.model === run.model);
    actual = backtest.map(row => ({date: row.date, value: row.actual}));
    predicted = backtest.map(row => ({date: row.date, value: row.prediction}));
    $("forecast-chart-description").textContent = "Отложенные исторические продажи и прогноз выбранной модели из одной точки начала.";
  } else {
    actual = (run.history || []).filter(row => row.sku === sku).slice(-56).map(row => ({date: row.date, value: row.sales}));
    predicted = (run.forecast || []).filter(row => row.sku === sku).map(row => ({date: row.date, value: row.prediction}));
    boundary = predicted[0]?.date;
    $("forecast-chart-description").textContent = "Последние 56 дней истории и будущий прогноз. Подсвечен расчётный период после конца истории.";
  }
  lineChart($("forecast-chart"), [{points: actual, color: chartColors.actual}, {points: predicted, color: chartColors.forecast, dashed: true}], boundary);
}
function renderMetrics() {
  if (!state.run) return;
  const pooled = $("validation-period").value === "pooled";
  const source = pooled ? state.run.rolling_metrics : state.run.metrics;
  if (pooled) $("metrics-scope").value = "ALL";
  for (const option of $("metrics-scope").options) option.disabled = pooled && option.value !== "ALL";
  const scope = $("metrics-scope").value;
  const metrics = (source || []).filter(row => row.sku === scope).sort((a, b) => Number(a.WAPE_percent ?? Infinity) - Number(b.WAPE_percent ?? Infinity));
  $("metrics-table").innerHTML = table(["Модель", "MAE, ед.", "RMSE, ед.", "WAPE, %", "Смещение, ед."], metrics.map(row => [modelNames[row.model] || row.model, number(row.MAE, 2), number(row.RMSE, 2), number(row.WAPE_percent, 2), number(row.bias_units, 2)]), metrics.map(row => row.model === state.run.model ? "row-highlight" : ""));
  const windows = state.run.validation_windows || [];
  const last = windows[windows.length - 1] || state.run.validation || state.run.experiment || {};
  const firstDate = last.test_start || [...new Set((state.run.backtest || []).map(row => row.date))].sort()[0];
  const lastDate = last.test_end || [...new Set((state.run.backtest || []).map(row => row.date))].sort().at(-1);
  $("validation-description").textContent = pooled
    ? `Ошибки объединены по всем ${windows.length || 1} проверочным окнам; WAPE считается по суммам ошибок и продаж.`
    : `Последнее проверочное окно: ${dateLabel(firstDate)} — ${dateLabel(lastDate)}. Выбранная модель выделена.`;
}
function markDirty() {
  state.dirty = true;
  $("draft-status").textContent = "Есть несохранённые изменения";
  $("draft-status").classList.add("dirty");
  $("draft-badge").textContent = "Не сохранён";
}
function fillScenario(values, id = "") {
  const form = $("scenario-form");
  for (const [key, value] of Object.entries({...defaults, ...values})) {
    const input = form.elements.namedItem(key);
    if (input) input.value = value;
  }
  $("scenario-id").value = id;
  $("scenario-editor-title").textContent = id ? "Редактирование сценария" : "Новый сценарий";
  $("save-scenario").textContent = id ? "Пересчитать и сохранить" : "Рассчитать и сохранить";
  $("draft-status").textContent = id ? "Измените условия и сохраните расчёт." : "Заполните условия и сохраните вариант.";
  $("draft-status").classList.remove("dirty");
  $("draft-badge").textContent = id ? "Сохранён" : "Черновик";
  state.dirty = false;
}
function resetScenario() { fillScenario(defaults); }
function scenarioParameters() {
  const form = $("scenario-form");
  const result = {};
  for (const key of Object.keys(defaults)) result[key] = key === "name" ? form.elements.namedItem(key).value.trim() : Number(form.elements.namedItem(key).value);
  return result;
}
function scenarioDescription(scenario) {
  const p = scenario.parameters || {};
  const parts = [`спрос ${Number(p.demand_change_pct) > 0 ? "+" : ""}${number(p.demand_change_pct || 0, 0)}%`, `цена ${Number(p.price_change_pct) > 0 ? "+" : ""}${number(p.price_change_pct || 0, 0)}%`];
  if (Number(p.supply_delay_days)) parts.push(`задержка ${number(p.supply_delay_days, 0)} дн.`);
  else parts.push(`поставка ${number(p.lead_time_days, 0)} дн.`);
  return parts.join(" · ");
}
function renderScenarios() {
  const list = state.run?.scenarios || [];
  const checked = new Set(state.compared.map(item => String(item.id)));
  $("scenario-count").textContent = `${list.length} вариантов`;
  $("scenario-list").innerHTML = list.length ? list.map(item => `<div class="scenario-item${item.is_baseline ? " is-baseline" : ""}"><input type="checkbox" class="scenario-check" value="${esc(item.id)}" ${checked.has(String(item.id)) ? "checked" : ""} aria-label="Сравнить ${esc(item.name)}"><div class="scenario-item-content"><span class="scenario-item-name">${esc(item.name)}${item.is_baseline ? ' <span class="tag">Базовый</span>' : ""}</span><p class="scenario-item-description">${esc(scenarioDescription(item))}</p><div class="scenario-item-bottom"><span class="scenario-profit ${Number(item.summary?.profit) < 0 ? "negative" : ""}">${esc(money(item.summary?.profit))}</span><div class="scenario-item-actions">${item.is_baseline ? `<button class="text-action" data-copy-scenario="${esc(item.id)}">Создать копию</button>` : `<button class="text-action" data-edit-scenario="${esc(item.id)}">Изменить</button><button class="text-action danger" data-delete-scenario="${esc(item.id)}">Удалить</button>`}</div></div></div></div>`).join("") : '<p class="empty-inline">Создайте первый сценарий с помощью формы.</p>';
  updateCompareButton();
  const currentDaily = $("daily-scenario").value;
  const currentSensitivity = $("sensitivity-scenario").value;
  options($("daily-scenario"), list, item => item.name, currentDaily);
  options($("sensitivity-scenario"), list, item => item.name, currentSensitivity);
  $("sensitivity-form").querySelector("button").disabled = !list.length;
  resetSensitivity();
  renderComparison(); renderDaily();
}
function renderComparison() {
  const items = state.compared;
  const ids = items.map(item => item.id).join(",");
  $("export-comparison").href = `/api/runs/${state.run.id}/export?kind=comparison&scenario_ids=${encodeURIComponent(ids)}`;
  $("export-comparison").title = "Скачать сценарии, показанные в текущей таблице сравнения";
  $("export-comparison").classList.toggle("hidden", !items.length);
  $("comparison-caption").textContent = items.length ? `${items.length} вариантов · горизонт ${state.run.horizon} дней · валюта ${currency()}` : "Выберите хотя бы один вариант для сравнения.";
  barChart($("comparison-chart"), items.map(item => ({label: item.name, value: Number(item.summary?.profit || 0), baseline: item.is_baseline})), currency());
  $("comparison-table").innerHTML = table(["Сценарий", `Результат, ${currency()}`, "Продажи, ед.", "Дефицит, ед.", "Остаток, ед.", "В пути, ед.", "Сервис", `Заказы, ${currency()}`], items.map(item => [item.name, number(item.summary?.profit, 2), number(item.summary?.sales_units, 1), number(item.summary?.lost_sales_units, 1), number(item.summary?.ending_stock_units, 1), number(item.summary?.pipeline_units, 1), percent(item.summary?.service_level), number(item.summary?.procurement_spend, 2)]));
}
function resetSensitivity() {
  state.sensitivityVersion += 1;
  state.sensitivityPoints = null;
  $("sensitivity-output").className = "empty-inline";
  $("sensitivity-output").textContent = "Выберите сохранённый сценарий и запустите проверку.";
  $("sensitivity-result-caption").classList.add("hidden");
  $("sensitivity-result-caption").textContent = "";
}
function updateCompareButton() {
  const count = document.querySelectorAll(".scenario-check:checked").length;
  $("compare-button").disabled = count < 2 || count > 8;
  $("compare-button").textContent = (state.run?.scenarios || []).length < 2 ? "Добавьте вариант для сравнения" : "Сравнить выбранные";
  $("compare-button").title = `Выбрано ${count}. Для сравнения нужно от 2 до 8 сценариев.`;
}
function renderDaily() {
  const id = $("daily-scenario").value;
  const item = (state.run?.scenarios || []).find(scenario => String(scenario.id) === String(id));
  const sku = $("daily-sku").value;
  const rows = (item?.daily || []).filter(row => row.sku === sku);
  lineChart($("daily-chart"), [{points: rows.map(row => ({date: row.date, value: row.closing_stock})), color: chartColors.actual}, {points: rows.map(row => ({date: row.date, value: row.lost_sales})), color: chartColors.deficit, dashed: true}]);
  const visibleRows = $("show-all-daily").checked ? rows : rows.slice(0, 7);
  $("daily-table").innerHTML = table(["Дата", "Начало, ед.", "Поступило", "Спрос", "Продано", "Дефицит", "Заказано", "Остаток", "В пути"], visibleRows.map(row => [dateLabel(row.date), ...["opening_stock", "received", "demand", "sales", "lost_sales", "ordered", "closing_stock", "pipeline"].map(key => number(row[key], 1))]));
  $("export-daily").href = item ? `/api/runs/${state.run.id}/export?kind=daily&scenario_id=${item.id}` : "#";
  $("export-daily").classList.toggle("hidden", !item);
}
async function refreshCurrentRun(preserveSelection = true) {
  const oldIds = new Set(state.compared.map(item => item.id));
  const data = await api(`/api/runs/${state.run.id}`);
  state.run = data.run;
  state.compared = preserveSelection ? (state.run.scenarios || []).filter(item => oldIds.has(item.id)) : [...(state.run.scenarios || [])];
  renderScenarios();
}
async function loadHistory() {
  const data = await api("/api/runs");
  state.runs = data.runs || [];
  $("history-list").innerHTML = state.runs.length ? state.runs.map(item => `<article class="card history-item"><div class="history-main"><span class="history-symbol" aria-hidden="true">↗</span><div><h2>${esc(item.name)}</h2><p>${esc(item.dataset_name || "Набор продаж")} · ${esc(modelNames[item.model] || item.model)} · ${esc(item.horizon)} дней</p></div></div><div class="history-meta"><span class="history-date">${esc(item.created_at ? new Date(item.created_at).toLocaleString("ru-RU", {day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit"}) : "")}</span><button class="button button-secondary" data-open-run="${esc(item.id)}">Открыть <span aria-hidden="true">→</span></button></div></article>`).join("") : '<div class="card run-empty"><div class="empty-icon" aria-hidden="true">↗</div><h2>Здесь появятся ваши исследования</h2><p>Создайте первое исследование по реальной истории продаж.</p><button class="button button-primary" data-go="data">Перейти к данным</button></div>';
}

/* Графики SVG: без внешних библиотек, работают без интернета. */
function chartNumber(value, digits = 0, maxLength = 7) {
  const full = number(value, digits);
  return full.length > maxLength ? Number(value).toLocaleString("ru-RU", {notation: "compact", maximumFractionDigits: 1}) : full;
}
function chartScale(minimum, maximum, count = 4) {
  // Круглые деления легче читать, чем произвольные числа на каждой линии сетки.
  const span = Math.max(maximum - minimum, 1);
  const raw = span / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const fraction = raw / power;
  const step = (fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10) * power;
  const low = Math.floor(minimum / step) * step;
  const high = Math.max(low + step, Math.ceil(maximum / step) * step);
  const ticks = [];
  for (let value = low; value <= high + step / 2; value += step) ticks.push(Math.abs(value) < step / 100 ? 0 : value);
  let digits = 0;
  while (digits < 4 && Math.abs(step * 10 ** digits - Math.round(step * 10 ** digits)) > 1e-8) digits += 1;
  return {low, high, ticks, digits};
}
function lineChart(container, series, boundary) {
  const clean = series.map(item => ({...item, points: item.points.filter(point => point.date && point.value !== null && Number.isFinite(Number(point.value))).map(point => ({date: String(point.date).slice(0, 10), value: Number(point.value)}))}));
  const dates = [...new Set(clean.flatMap(item => item.points.map(point => point.date)))].sort();
  if (!dates.length) { container.innerHTML = '<div class="empty-inline">Нет данных для графика.</div>'; return; }
  const width = Math.max(280, Math.min(1400, container.clientWidth || 1000));
  const compact = width < 500;
  const height = compact ? 246 : 294, left = compact ? 45 : 60, right = 22, top = 28, bottom = 38;
  const chartWidth = width - left - right, chartHeight = height - top - bottom;
  const values = clean.flatMap(item => item.points.map(point => point.value));
  const scale = chartScale(Math.min(0, ...values), Math.max(1, ...values) * 1.05);
  const x = date => left + dates.indexOf(String(date).slice(0, 10)) / Math.max(1, dates.length - 1) * chartWidth;
  const y = value => top + (scale.high - value) / (scale.high - scale.low) * chartHeight;
  let svg = `<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">`;
  if (boundary && dates.includes(boundary)) {
    const bx = x(boundary);
    svg += `<rect x="${bx}" y="${top}" width="${width - right - bx}" height="${chartHeight}" rx="3" fill="#f2f5fc"/><line x1="${bx}" y1="${top}" x2="${bx}" y2="${height - bottom}" stroke="#c8d2e8" stroke-dasharray="4 5"/><text x="${Math.min(bx + 10, width - 86)}" y="16" font-size="11" font-weight="500" fill="${chartColors.axis}">Прогноз</text>`;
  }
  for (const value of scale.ticks) {
    svg += `<line x1="${left}" y1="${y(value)}" x2="${width - right}" y2="${y(value)}" stroke="${chartColors.grid}" ${value !== 0 ? 'stroke-dasharray="3 5"' : ""}/><text x="${left - 12}" y="${y(value) + 4}" text-anchor="end" font-size="11" fill="${chartColors.axis}">${esc(chartNumber(value, scale.digits))}</text>`;
  }
  const count = Math.min(compact ? 4 : 7, dates.length);
  for (let index = 0; index < count; index++) {
    const position = Math.round(index * (dates.length - 1) / Math.max(1, count - 1));
    const date = dates[position];
    svg += `<text x="${x(date)}" y="${height - 12}" text-anchor="middle" font-size="11" fill="${chartColors.axis}">${esc(dateLabel(date).slice(0, 5))}</text>`;
  }
  for (const item of clean) {
    if (!item.points.length) continue;
    const path = item.points.map((point, index) => `${index ? "L" : "M"}${x(point.date).toFixed(2)},${y(point.value).toFixed(2)}`).join(" ");
    svg += `<path d="${path}" fill="none" stroke="${item.color}" stroke-width="2.3" stroke-linejoin="round" stroke-linecap="round" ${item.dashed ? 'stroke-dasharray="5 4"' : ""}/>`;
    // Невидимые области дают подсказку при наведении без десятков видимых точек.
    for (const point of item.points) svg += `<circle cx="${x(point.date)}" cy="${y(point.value)}" r="6" fill="transparent"><title>${esc(dateLabel(point.date))}: ${esc(number(point.value, 2))}</title></circle>`;
    const last = item.points.at(-1);
    svg += `<circle cx="${x(last.date)}" cy="${y(last.value)}" r="3.5" fill="${item.color}" stroke="white" stroke-width="2"/>`;
  }
  container.innerHTML = svg + "</svg>";
}
function barChart(container, items, unit = "") {
  if (!items.length) { container.innerHTML = '<div class="empty-inline">Выберите варианты для сравнения.</div>'; return; }
  const width = Math.max(280, Math.min(1400, container.clientWidth || 900));
  const scale = chartScale(Math.min(0, ...items.map(item => item.value)), Math.max(0, ...items.map(item => item.value)) || 1);
  // Длинные названия сценариев читаются лучше в горизонтальном сравнении.
  if (!items.every(item => /^[-+−]?\d+%$/.test(item.label))) {
    const compactValue = value => chartNumber(value, 0, width < 420 ? 10 : 13);
    const valueWidth = Math.max(...items.map(item => compactValue(item.value).length)) * 7 + 16;
    const left = 2, right = Math.min(width * 0.45, Math.max(width < 420 ? 74 : 94, valueWidth)), top = 16, rowHeight = 54;
    const plotWidth = width - left - right, height = top + items.length * rowHeight + 28;
    const x = value => left + (value - scale.low) / (scale.high - scale.low) * plotWidth;
    let svg = `<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">`;
    for (const value of scale.ticks) svg += `<line x1="${x(value)}" y1="${top + 16}" x2="${x(value)}" y2="${height - 28}" stroke="${value === 0 ? "#ccd4e2" : chartColors.grid}" ${value !== 0 ? 'stroke-dasharray="3 5"' : ""}/><text x="${x(value)}" y="${height - 6}" text-anchor="${value === scale.low ? "start" : "middle"}" font-size="10" fill="${chartColors.axis}">${esc(chartNumber(value, scale.digits))}</text>`;
    items.forEach((item, index) => {
      const y = top + index * rowHeight;
      const maxLabel = Math.max(20, Math.floor((width - 92) / 6.7));
      const label = item.label.length > maxLabel ? item.label.slice(0, maxLabel - 1) + "…" : item.label;
      const color = item.value < 0 ? chartColors.deficit : item.baseline ? "#a9b5d1" : chartColors.actual;
      const start = Math.min(x(0), x(item.value));
      svg += `<text x="${left}" y="${y + 4}" font-size="12" font-weight="500" fill="${chartColors.ink}"><title>${esc(item.label)}</title>${esc(label)}</text><rect x="${left}" y="${y + 14}" width="${plotWidth}" height="12" rx="4" fill="#f3f5f9"/><rect x="${start}" y="${y + 14}" width="${Math.max(1, Math.abs(x(item.value) - x(0)))}" height="12" rx="4" fill="${color}"><title>${esc(item.label)}: ${esc(number(item.value, 2))} ${esc(unit)}</title></rect><text x="${width - 2}" y="${y + 25}" text-anchor="end" font-size="12" font-weight="600" fill="${chartColors.ink}"><title>${esc(number(item.value, 2))} ${esc(unit)}</title>${esc(compactValue(item.value))}</text>`;
    });
    container.innerHTML = svg + "</svg>";
    return;
  }
  const height = 240, left = width < 500 ? 48 : 66, right = 18, top = 28, bottom = 34;
  const y = value => top + (scale.high - value) / (scale.high - scale.low) * (height - top - bottom);
  const space = (width - left - right) / items.length;
  const barWidth = Math.min(60, space * 0.52);
  let svg = `<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">`;
  for (const value of scale.ticks) svg += `<line x1="${left}" y1="${y(value)}" x2="${width - right}" y2="${y(value)}" stroke="${chartColors.grid}" ${value !== 0 ? 'stroke-dasharray="3 5"' : ""}/><text x="${left - 10}" y="${y(value) + 4}" text-anchor="end" fill="${chartColors.axis}" font-size="11">${esc(chartNumber(value, scale.digits))}</text>`;
  items.forEach((item, index) => {
    const x = left + space * (index + 0.5);
    const topY = Math.min(y(0), y(item.value));
    const color = item.value < 0 ? chartColors.deficit : item.label === "0%" ? "#28375c" : chartColors.actual;
    svg += `<rect x="${x - barWidth / 2}" y="${topY}" width="${barWidth}" height="${Math.max(1, Math.abs(y(item.value) - y(0)))}" rx="4" fill="${color}"><title>${esc(item.label)}: ${esc(number(item.value, 2))} ${esc(unit)}</title></rect><text x="${x}" y="${item.value >= 0 ? topY - 8 : y(item.value) + 16}" text-anchor="middle" fill="${chartColors.ink}" font-size="11" font-weight="500"><title>${esc(number(item.value, 2))} ${esc(unit)}</title>${esc(chartNumber(item.value, 0, width < 420 ? 6 : 9))}</text><text x="${x}" y="${height - 10}" text-anchor="middle" fill="${chartColors.axis}" font-size="11">${esc(item.label)}</text>`;
  });
  container.innerHTML = svg + "</svg>";
}

/* Действия пользователя. */
document.addEventListener("click", async event => {
  const nav = event.target.closest("[data-view], [data-go]");
  if (nav) changeView(nav.dataset.view || nav.dataset.go);
  const mode = event.target.closest("[data-chart-mode]");
  if (mode) {
    state.chartMode = mode.dataset.chartMode;
    document.querySelectorAll("[data-chart-mode]").forEach(button => { const active = button === mode; button.classList.toggle("active", active); button.setAttribute("aria-pressed", String(active)); });
    renderForecast();
  }
  const open = event.target.closest("[data-open-run]");
  if (open) await busy(open, () => openRun(open.dataset.openRun), "Открываем…");
  const edit = event.target.closest("[data-edit-scenario]");
  if (edit) {
    const item = state.run.scenarios.find(scenario => String(scenario.id) === edit.dataset.editScenario);
    if (item && (!state.dirty || window.confirm("Заменить несохранённый черновик выбранным сценарием?"))) { fillScenario({...item.parameters, name: item.name}, item.id); $("scenario-name").focus(); }
  }
  const copy = event.target.closest("[data-copy-scenario]");
  if (copy) {
    const item = state.run.scenarios.find(scenario => String(scenario.id) === copy.dataset.copyScenario);
    if (item && (!state.dirty || window.confirm("Заменить несохранённый черновик копией базового сценария?"))) {
      fillScenario({...item.parameters, name: "Альтернативный сценарий"}); markDirty(); $("scenario-name").focus();
    }
  }
  const remove = event.target.closest("[data-delete-scenario]");
  if (remove) {
    const item = state.run.scenarios.find(scenario => String(scenario.id) === remove.dataset.deleteScenario);
    if (item && window.confirm(`Удалить сценарий «${item.name}»? Сохранённый расчёт будет удалён из этого исследования.`)) await busy(remove, async () => {
      await api(`/api/scenarios/${item.id}`, {method: "DELETE"});
      await refreshCurrentRun();
      if (String($("scenario-id").value) === String(item.id)) resetScenario();
      notify("Сценарий удалён");
    }, "Удаляем…");
  }
  const preset = event.target.closest("[data-preset]");
  if (preset) {
    if (state.dirty && !window.confirm("Заменить текущий черновик примером условий?")) return;
    const values = {growth: {name: "Рост спроса на 20%", demand_change_pct: 20}, delay: {name: "Задержка поставки на 3 дня", supply_delay_days: 3}, discount: {name: "Снижение цены на 10%", price_change_pct: -10}};
    fillScenario(values[preset.dataset.preset]); markDirty();
  }
});
$("dataset-select").addEventListener("change", event => selectDataset(event.target.value).catch(reportError));
$("run-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!canDiscard()) return;
  await busy($("create-run"), async () => {
    const name = $("run-name").value.trim() || `План на ${$("horizon").value} дней`;
    const body = {dataset_id: Number(state.datasetId), name, model: $("model-select").value, horizon: Number($("horizon").value)};
    const calendar = parseCalendar($("future-calendar").value);
    if (calendar) body.future_calendar = calendar;
    const data = await api("/api/runs", {method: "POST", body});
    await syncRunInputs(data.run);
    setRun(data.run); changeView("forecast"); notify("Исследование сохранено. Прогноз и базовый сценарий готовы.");
  }, "Строим прогноз…");
});
$("upload-form").addEventListener("submit", async event => {
  event.preventDefault();
  await busy(event.target.querySelector('button[type="submit"]'), async () => {
    const form = new FormData(event.target);
    const data = await api("/api/datasets", {method: "POST", body: form});
    await loadDatasets(data.dataset?.id || data.id);
    event.target.reset();
    notify("Данные проверены и добавлены. Можно создавать исследование.");
  }, "Проверяем данные…");
});
$("forecast-sku").addEventListener("change", renderForecast);
$("metrics-scope").addEventListener("change", renderMetrics);
$("validation-period").addEventListener("change", renderMetrics);
$("scenario-form").addEventListener("input", markDirty);
$("scenario-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!state.run) return;
  const saved = await busy($("save-scenario"), async () => {
    const id = $("scenario-id").value;
    const result = await api(id ? `/api/scenarios/${id}` : `/api/runs/${state.run.id}/scenarios`, {method: id ? "PUT" : "POST", body: scenarioParameters()});
    await refreshCurrentRun(false);
    if (result.scenario?.id) $("daily-scenario").value = result.scenario.id;
    renderDaily();
    notify(id ? "Сценарий пересчитан и сохранён" : "Новый сценарий сохранён");
  }, "Считаем сценарий…");
  if (saved) resetScenario();
});
$("reset-scenario").addEventListener("click", () => { if (!state.dirty || window.confirm("Сбросить несохранённые изменения сценария?")) resetScenario(); });
$("compare-button").addEventListener("click", async event => {
  await busy(event.currentTarget, async () => {
    const ids = [...document.querySelectorAll(".scenario-check:checked")].map(input => Number(input.value));
    if (ids.length < 2 || ids.length > 8) throw new Error("Для сравнения отметьте от 2 до 8 сценариев.");
    const data = await api(`/api/runs/${state.run.id}/compare`, {method: "POST", body: {scenario_ids: ids}});
    state.compared = data.scenarios || []; renderComparison(); notify("Сравнение обновлено");
  }, "Сравниваем…");
});
$("daily-scenario").addEventListener("change", renderDaily);
$("scenario-list").addEventListener("change", event => {
  if (event.target.matches(".scenario-check")) updateCompareButton();
});
$("daily-sku").addEventListener("change", renderDaily);
$("show-all-daily").addEventListener("change", renderDaily);
$("sensitivity-scenario").addEventListener("change", resetSensitivity);
$("sensitivity-form").addEventListener("submit", async event => {
  event.preventDefault();
  await busy(event.target.querySelector('button[type="submit"]'), async () => {
    resetSensitivity();
    const runId = state.run.id;
    const scenarioId = Number($("sensitivity-scenario").value);
    const version = state.sensitivityVersion;
    const scenarioName = state.run.scenarios.find(item => Number(item.id) === scenarioId)?.name || "Выбранный сценарий";
    const data = await api(`/api/runs/${runId}/sensitivity`, {method: "POST", body: {scenario_id: scenarioId, demand_changes: [-20, -10, 0, 10, 20]}});
    if (state.run?.id !== runId || Number($("sensitivity-scenario").value) !== scenarioId || state.sensitivityVersion !== version) return;
    const points = data.points || [];
    state.sensitivityPoints = points;
    $("sensitivity-result-caption").textContent = `Результат для сценария «${scenarioName}». Изменение спроса задано относительно базового прогноза; остальные условия сценария сохранены.`;
    $("sensitivity-result-caption").classList.remove("hidden");
    $("sensitivity-output").className = "sensitivity-output-grid";
    $("sensitivity-output").innerHTML = '<div id="sensitivity-chart" class="chart" role="img" aria-label="Финансовый результат при изменении спроса"></div><div id="sensitivity-table" class="table-scroll"></div>';
    barChart($("sensitivity-chart"), points.map(item => ({label: `${item.demand_change_pct > 0 ? "+" : ""}${item.demand_change_pct}%`, value: Number(item.profit)})), currency());
    $("sensitivity-table").innerHTML = table(["Спрос", `Результат, ${currency()}`, "Дефицит, ед.", "Сервис"], points.map(item => [`${item.demand_change_pct > 0 ? "+" : ""}${number(item.demand_change_pct, 0)}%`, number(item.profit, 2), number(item.lost_sales_units, 1), percent(item.service_level)]));
  }, "Пересчитываем…");
});
$("refresh-history").addEventListener("click", event => busy(event.currentTarget, loadHistory, "Обновляем…"));
window.addEventListener("beforeunload", event => { if (state.dirty) { event.preventDefault(); event.returnValue = ""; } });
let resizeTimer;
window.addEventListener("resize", () => {
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(() => {
    if (!state.run) return;
    if (!$("view-forecast").classList.contains("hidden")) renderForecast();
    if (!$("view-scenarios").classList.contains("hidden")) {
      renderComparison(); renderDaily();
      if (state.sensitivityPoints && $("sensitivity-chart")) barChart($("sensitivity-chart"), state.sensitivityPoints.map(item => ({label: `${item.demand_change_pct > 0 ? "+" : ""}${item.demand_change_pct}%`, value: Number(item.profit)})), currency());
    }
  }, 150);
});

async function start() {
  try {
    await loadDatasets();
    await loadHistory();
    $("connection-state").textContent = "Сервер доступен";
    document.querySelectorAll(".needs-run").forEach(button => button.disabled = true);
  } catch (error) {
    $("connection-state").textContent = "Нет соединения";
    reportError(error);
  }
}
start();
