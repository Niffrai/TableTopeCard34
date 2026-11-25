// --- ГЛОБАЛЬНАЯ СИСТЕМА УВЕДОМЛЕНИЙ (TOAST NOTIFICATIONS) ---
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function showToast(message, type = "info", title = "", duration = 5000) {
  const container = document.getElementById("toast-container");
  if (!container) {
    console.warn(`[Toast:${type}] ${title ? title + ': ' : ''}${message}`);
    return;
  }

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;

  let iconSvg = "";
  if (type === "error") {
    iconSvg = `<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>`;
    if (!title) title = "Ошибка";
  } else if (type === "success") {
    iconSvg = `<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>`;
    if (!title) title = "Успешно";
  } else if (type === "warning") {
    iconSvg = `<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>`;
    if (!title) title = "Внимание";
  } else {
    iconSvg = `<svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>`;
    if (!title) title = "Информация";
  }

  toast.innerHTML = `
    ${iconSvg}
    <div class="toast-body">
      <div class="toast-title">${escapeHtml(title)}</div>
      <div class="toast-message">${escapeHtml(message)}</div>
    </div>
    <button type="button" class="toast-close" title="Закрыть">&times;</button>
  `;

  function closeToast() {
    if (toast.classList.contains("toast-hiding")) return;
    toast.classList.add("toast-hiding");
    setTimeout(() => {
      if (toast.parentElement) toast.parentElement.removeChild(toast);
    }, 250);
  }

  const closeBtn = toast.querySelector(".toast-close");
  if (closeBtn) closeBtn.addEventListener("click", closeToast);

  container.appendChild(toast);

  if (duration > 0) {
    setTimeout(closeToast, duration);
  }
}

// --- СКАЧИВАНИЕ ZIP-АРХИВОВ С ИНДИКАТОРОМ ПРОГРЕССА ---
function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return "0 Б";
  const units = ["Б", "КБ", "МБ", "ГБ"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return (bytes / Math.pow(1024, i)).toFixed(1) + " " + units[i];
}

function extractFilenameFromHeader(disposition, fallback = "deck.zip") {
  if (!disposition) return fallback;
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match && utf8Match[1]) {
    try {
      return decodeURIComponent(utf8Match[1].trim());
    } catch (_) {}
  }
  const quotedMatch = disposition.match(/filename="([^"]+)"/i);
  if (quotedMatch && quotedMatch[1]) {
    return quotedMatch[1].trim();
  }
  const plainMatch = disposition.match(/filename=([^;]+)/i);
  if (plainMatch && plainMatch[1]) {
    return plainMatch[1].trim();
  }
  return fallback;
}

async function downloadZipWithProgress(url, {
  method = "GET",
  body = null,
  headers = {},
  defaultFilename = "deck.zip",
  title = "Сохранение ZIP-архива"
} = {}) {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = "toast toast-progress toast-info";

  const abortController = new AbortController();

  toast.innerHTML = `
    <div class="toast-icon-wrapper">
      <svg class="toast-icon toast-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 12a9 9 0 1 1-6.219-8.56"></path>
      </svg>
    </div>
    <div class="toast-body">
      <div class="toast-title">${escapeHtml(title)}</div>
      <div class="toast-message">Сервер сжимает карты и текстуры...</div>
      <div class="toast-progress-track">
        <div class="toast-progress-fill indeterminate"></div>
      </div>
      <div class="toast-progress-meta">
        <span class="toast-progress-status">Подготовка архива...</span>
        <span class="toast-progress-numbers">0%</span>
      </div>
    </div>
    <button type="button" class="toast-close" title="Отмена">&times;</button>
  `;

  let isClosed = false;
  function closeToast() {
    if (isClosed) return;
    isClosed = true;
    toast.classList.add("toast-hiding");
    setTimeout(() => {
      if (toast.parentElement) toast.parentElement.removeChild(toast);
    }, 250);
  }

  const closeBtn = toast.querySelector(".toast-close");
  closeBtn.addEventListener("click", () => {
    abortController.abort();
    closeToast();
  });

  container.appendChild(toast);

  const messageEl = toast.querySelector(".toast-message");
  const progressFill = toast.querySelector(".toast-progress-fill");
  const progressStatus = toast.querySelector(".toast-progress-status");
  const progressNumbers = toast.querySelector(".toast-progress-numbers");
  const iconWrapper = toast.querySelector(".toast-icon-wrapper");
  const titleEl = toast.querySelector(".toast-title");

  try {
    const res = await fetch(url, {
      method,
      headers,
      body,
      signal: abortController.signal
    });

    if (!res.ok) {
      let errMsg = "Ошибка при загрузке архива";
      try {
        const errJson = await res.json();
        if (errJson.error) errMsg = errJson.error;
      } catch (_) {
        errMsg = `Сбой сервера (${res.status} ${res.statusText})`;
      }
      throw new Error(errMsg);
    }

    const disposition = res.headers.get("Content-Disposition");
    const filename = extractFilenameFromHeader(disposition, defaultFilename);
    const contentLength = parseInt(res.headers.get("Content-Length") || "0", 10);

    const cleanName = filename.replace(/\.zip$/i, "");
    titleEl.textContent = `📦 Экспорт «${cleanName}»`;
    progressFill.classList.remove("indeterminate");
    progressFill.style.width = "0%";
    messageEl.textContent = `Скачивание: ${filename}`;
    progressStatus.textContent = contentLength > 0 ? `0 из ${formatBytes(contentLength)}` : "Скачивание...";

    const reader = res.body.getReader();
    const chunks = [];
    let received = 0;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.length;

      if (contentLength > 0) {
        const pct = Math.min(100, Math.round((received / contentLength) * 100));
        progressFill.style.width = `${pct}%`;
        progressNumbers.textContent = `${pct}%`;
        progressStatus.textContent = `${formatBytes(received)} / ${formatBytes(contentLength)}`;
      } else {
        progressFill.style.width = "100%";
        progressNumbers.textContent = "";
        progressStatus.textContent = formatBytes(received);
      }
    }

    const blob = new Blob(chunks, { type: "application/zip" });
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      document.body.removeChild(a);
      URL.revokeObjectURL(blobUrl);
    }, 500);

    toast.className = "toast toast-progress toast-success";
    iconWrapper.innerHTML = `
      <svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
        <polyline points="22 4 12 14.01 9 11.01"></polyline>
      </svg>
    `;
    titleEl.textContent = "Архив успешно сохранён!";
    messageEl.textContent = `Файл «${filename}» (${formatBytes(received)}) скачан на компьютер.`;
    progressFill.style.width = "100%";
    progressStatus.textContent = "100% Завершено";
    progressNumbers.textContent = "";

    setTimeout(closeToast, 4500);
  } catch (err) {
    if (err.name === "AbortError") {
      messageEl.textContent = "Загрузка отменена пользователем.";
      setTimeout(closeToast, 1500);
      return;
    }
    toast.className = "toast toast-progress toast-error";
    iconWrapper.innerHTML = `
      <svg class="toast-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"></circle>
        <line x1="15" y1="9" x2="9" y2="15"></line>
        <line x1="9" y1="9" x2="15" y2="15"></line>
      </svg>
    `;
    titleEl.textContent = "Ошибка экспорта ZIP";
    messageEl.textContent = err.message || "Не удалось сохранить архив";
    progressFill.style.width = "100%";
    progressStatus.textContent = "Сбой";
    progressNumbers.textContent = "";
  }
}

// --- ГЛОБАЛЬНЫЕ ОБРАБОТЧИКИ СОБЫТИЙ НА ОШИБКИ (ERROR EVENT HANDLERS) ---
window.addEventListener("error", (event) => {
  console.error("Глобальный перехват ошибки (Window Error):", event.error || event.message);
  const msg = event.error ? (event.error.message || String(event.error)) : (event.message || "Непредвиденная ошибка скрипта");
  showToast(msg, "error", "Ошибка JavaScript");

  const logs = document.getElementById("logs-container");
  if (logs) {
    const timestamp = new Date().toLocaleTimeString();
    const div = document.createElement("div");
    div.className = "log-line log-line-error";
    div.style.color = "#ef4444";
    div.textContent = `[${timestamp}] Ошибка интерфейса: ${msg}`;
    logs.appendChild(div);
    logs.scrollTop = logs.scrollHeight;
  }
});

window.addEventListener("unhandledrejection", (event) => {
  console.error("Глобальный перехват Promise rejection:", event.reason);
  let msg = "Необработанная асинхронная ошибка";
  if (event.reason) {
    msg = event.reason.message || String(event.reason);
  }
  showToast(msg, "error", "Асинхронная ошибка");

  const logs = document.getElementById("logs-container");
  if (logs) {
    const timestamp = new Date().toLocaleTimeString();
    const div = document.createElement("div");
    div.className = "log-line log-line-error";
    div.style.color = "#ef4444";
    div.textContent = `[${timestamp}] Ошибка промиса: ${msg}`;
    logs.appendChild(div);
    logs.scrollTop = logs.scrollHeight;
  }
});

document.addEventListener("DOMContentLoaded", () => {
  // Элементы формы
  const form = document.getElementById("gen-form");
  const booruSourceSelect = document.getElementById("booru_source");
  const sourceStatusBadge = document.getElementById("source-status-badge");
  const rule34AuthBox = document.getElementById("rule34-auth-box");
  const rule34UserIdInput = document.getElementById("rule34_user_id");
  const rule34ApiKeyInput = document.getElementById("rule34_api_key");
  const charTextarea = document.getElementById("characters");
  const charCalcBadge = document.getElementById("char-calc-badge");
  const posArtistsInput = document.getElementById("positive_artists");
  const negArtistsInput = document.getElementById("negative_artists");
  const artistCalcBadge = document.getElementById("artist-calc-badge");
  const posTagsInput = document.getElementById("positive_tags");
  const negTagsInput = document.getElementById("negative_tags");
  const totalCardsRange = document.getElementById("total_cards_range");
  const totalCardsNum = document.getElementById("total_cards_num");
  const cardResolutionSelect = document.getElementById("card_resolution");
  const threadsCountSelect = document.getElementById("threads_count");
  const yearFromInput = document.getElementById("year_from");
  const yearToInput = document.getElementById("year_to");
  const btnResetYears = document.getElementById("btn-reset-years");
  const deckTitleInput = document.getElementById("deck_title");

  // Кнопки
  const btnGenerate = document.getElementById("btn-generate");
  const btnStop = document.getElementById("btn-stop");
  const btnOpenFolder = document.getElementById("btn-open-folder");
  const btnOpenResFolder = document.getElementById("btn-open-res-folder");
  const btnOpenPhotos = document.getElementById("btn-open-photos");
  const btnSaveGame = document.getElementById("btn-save-game");
  const btnSaveObject = document.getElementById("btn-save-object");
  const btnClearLogs = document.getElementById("btn-clear-logs");
  const btnClearHistory = document.getElementById("btn-clear-history");
  const historyCount = document.getElementById("history-count");
  const btnExportZip = document.getElementById("btn-export-zip");
  const btnDeleteCurrentDeck = document.getElementById("btn-delete-current-deck");
  const btnImportZip = document.getElementById("btn-import-zip");
  const friendZipInput = document.getElementById("friend-zip-input");
  const btnShowGuide = document.getElementById("btn-show-guide");
  const guideModal = document.getElementById("guide-modal");
  const guideModalClose = document.getElementById("guide-modal-close");
  const guideModalBackdrop = document.getElementById("guide-modal-backdrop");

  // Элементы мониторинга
  const progressBar = document.getElementById("progress-bar");
  const percentText = document.getElementById("percent-text");
  const statusText = document.getElementById("status-text");
  const logsContainer = document.getElementById("logs-container");
  const errorBox = document.getElementById("error-box");
  const errorMessageEl = document.getElementById("error-message");
  const btnDismissError = document.getElementById("btn-dismiss-error");

  let lastReportedError = null;

  // Элементы результатов
  const resultBox = document.getElementById("result-box");
  const resultSummary = document.getElementById("result-summary");
  const sheetsList = document.getElementById("sheets-list");
  const deckSheetsCalc = document.getElementById("deck-sheets-calc");

  let pollInterval = null;
  let lastLogCount = 0;
  let isGenerating = false;

  // Защита от случайного закрытия вкладки во время генерации
  window.addEventListener("beforeunload", (e) => {
    if (isGenerating) {
      e.preventDefault();
      e.returnValue = "";
      return "";
    }
  });

  // --- 1. Синхронизация слайдера и числового поля (до 1024 карт) ---
  const MAX_PER_SHEET = 70;

  function updateSheetsCalc(total) {
    if (!deckSheetsCalc) return;
    const numSheets = Math.ceil(total / MAX_PER_SHEET);
    const fullSheets = Math.floor(total / MAX_PER_SHEET);
    const remainder = total % MAX_PER_SHEET;

    if (numSheets <= 1) {
      deckSheetsCalc.textContent = `${total} карт на 1 листе`;
    } else if (remainder === 0) {
      deckSheetsCalc.textContent = `${numSheets} листов (${fullSheets}×70)`;
    } else {
      deckSheetsCalc.textContent = `${numSheets} листов (${fullSheets}×70 + ${remainder})`;
    }

    document.querySelectorAll(".preset-chip").forEach(chip => {
      const val = parseInt(chip.dataset.val, 10);
      chip.classList.toggle("active", val === total);
    });
  }

  function syncCardsCount(val) {
    const num = Math.max(1, Math.min(1024, parseInt(val, 10) || 1));
    totalCardsRange.value = num;
    totalCardsNum.value = num;
    updateSheetsCalc(num);
    updateCharacterAllocation();
    updateArtistAllocation();
  }

  totalCardsRange.addEventListener("input", () => syncCardsCount(totalCardsRange.value));
  totalCardsNum.addEventListener("input", () => syncCardsCount(totalCardsNum.value));

  document.querySelectorAll(".preset-chip").forEach(chip => {
    chip.addEventListener("click", () => {
      const val = parseInt(chip.dataset.val, 10);
      if (val) syncCardsCount(val);
    });
  });

  // --- 2. Динамический расчет распределения по персонажам ---
  function updateCharacterAllocation() {
    const raw = charTextarea.value;
    const chars = raw.replace(/\n/g, ",").split(",").map(c => c.trim()).filter(c => c.length > 0);
    const total = parseInt(totalCardsNum.value, 10) || 456;

    if (chars.length === 0) {
      charCalcBadge.textContent = "Случайный пул";
      charCalcBadge.className = "badge-subtle";
    } else {
      const perChar = Math.floor(total / chars.length);
      const rem = total % chars.length;
      let hint = `${chars.length} перс.: по ~${perChar} карт`;
      if (rem > 0) hint += ` (+${rem})`;
      charCalcBadge.textContent = hint;
      charCalcBadge.className = "badge-subtle highlight-val";
    }
  }

  charTextarea.addEventListener("input", updateCharacterAllocation);

  // --- 2.1. Динамический расчет распределения по художникам ---
  function updateArtistAllocation() {
    if (!artistCalcBadge || !posArtistsInput) return;
    const raw = posArtistsInput.value;
    const artists = raw.split(/[\s,]+/).map(a => a.trim().replace(/^@/, "")).filter(Boolean);
    const total = parseInt(totalCardsNum.value, 10) || 456;

    if (artists.length === 0) {
      artistCalcBadge.textContent = "Любые авторы";
      artistCalcBadge.className = "badge-subtle";
    } else {
      const perArtist = Math.floor(total / artists.length);
      const rem = total % artists.length;
      let hint = `${artists.length} авт.: по ~${perArtist} карт`;
      if (rem > 0) hint += ` (+${rem})`;
      artistCalcBadge.textContent = hint;
      artistCalcBadge.className = "badge-subtle highlight-val";
    }
  }

  if (posArtistsInput) {
    posArtistsInput.addEventListener("input", updateArtistAllocation);
  }

  // Инициализация расчета листов при загрузке
  syncCardsCount(totalCardsNum.value || 456);

  // --- 3. Интерактивные всплывающие подсказки (кнопки "i") ---
  const floatingTooltip = document.createElement("div");
  floatingTooltip.className = "app-floating-tooltip";
  document.body.appendChild(floatingTooltip);

  let activeTooltipBtn = null;

  function showTooltip(btn) {
    const text = btn.getAttribute("data-tooltip");
    if (!text) return;
    floatingTooltip.textContent = text;
    floatingTooltip.classList.add("visible");

    const btnRect = btn.getBoundingClientRect();
    const tooltipWidth = floatingTooltip.offsetWidth;
    const tooltipHeight = floatingTooltip.offsetHeight;

    let left = btnRect.left + (btnRect.width / 2) - (tooltipWidth / 2);
    const padding = 12;
    if (left < padding) left = padding;
    if (left + tooltipWidth > window.innerWidth - padding) {
      left = window.innerWidth - padding - tooltipWidth;
    }

    let top = btnRect.top + window.scrollY - tooltipHeight - 8;
    let arrowAtTop = false;

    if (btnRect.top - tooltipHeight - 8 < 10) {
      top = btnRect.bottom + window.scrollY + 8;
      arrowAtTop = true;
    }

    floatingTooltip.style.top = `${top}px`;
    floatingTooltip.style.left = `${left}px`;
    floatingTooltip.classList.toggle("arrow-top", arrowAtTop);

    const arrowX = btnRect.left + (btnRect.width / 2) - left;
    floatingTooltip.style.setProperty("--arrow-left", `${Math.max(12, Math.min(tooltipWidth - 12, arrowX))}px`);
  }

  function hideTooltip() {
    floatingTooltip.classList.remove("visible");
    if (activeTooltipBtn) {
      activeTooltipBtn.classList.remove("active");
      activeTooltipBtn = null;
    }
  }

  document.querySelectorAll(".info-btn").forEach(btn => {
    btn.addEventListener("mouseenter", () => {
      if (!activeTooltipBtn) showTooltip(btn);
    });

    btn.addEventListener("mouseleave", () => {
      if (activeTooltipBtn !== btn) hideTooltip();
    });

    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (activeTooltipBtn === btn) {
        hideTooltip();
      } else {
        if (activeTooltipBtn) activeTooltipBtn.classList.remove("active");
        activeTooltipBtn = btn;
        btn.classList.add("active");
        showTooltip(btn);
      }
    });
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".info-btn") && !e.target.closest(".app-floating-tooltip")) {
      hideTooltip();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideTooltip();
  });

  // --- 3. Авторизация Rule34 и выбор источника ---
  if (rule34UserIdInput && rule34ApiKeyInput) {
    rule34UserIdInput.value = localStorage.getItem("tts_rule34_user_id") || "";
    rule34ApiKeyInput.value = localStorage.getItem("tts_rule34_api_key") || "";

    rule34UserIdInput.addEventListener("input", () => {
      localStorage.setItem("tts_rule34_user_id", rule34UserIdInput.value.trim());
    });
    rule34ApiKeyInput.addEventListener("input", () => {
      localStorage.setItem("tts_rule34_api_key", rule34ApiKeyInput.value.trim());
    });
  }

  if (booruSourceSelect) {
    booruSourceSelect.addEventListener("change", () => {
      if (booruSourceSelect.value === "rule34") {
        rule34AuthBox.classList.remove("hidden");
        sourceStatusBadge.textContent = "Нужен API ключ";
        sourceStatusBadge.className = "badge-subtle highlight-val";
      } else {
        rule34AuthBox.classList.add("hidden");
        sourceStatusBadge.textContent = "Без регистрации";
        sourceStatusBadge.className = "badge-subtle";
      }
    });
  }

  // --- 4. Быстрые чипы (добавление тегов) ---
  document.querySelectorAll(".chip").forEach(chip => {
    chip.addEventListener("click", () => {
      const targetId = chip.getAttribute("data-target");
      const val = chip.getAttribute("data-val");
      const input = document.getElementById(targetId);
      if (!input) return;

      const current = input.value.split(/[\s,]+/).map(t => t.trim()).filter(Boolean);
      if (!current.includes(val) && !current.includes("-" + val)) {
        input.value = current.length > 0 ? `${input.value.trim()}, ${val}` : val;
        input.dispatchEvent(new Event("input"));
        input.dispatchEvent(new Event("change"));
      }
    });
  });

  // Сброс годов
  btnResetYears.addEventListener("click", () => {
    yearFromInput.value = "";
    yearToInput.value = "";
  });

  // Очистка логов
  btnClearLogs.addEventListener("click", () => {
    logsContainer.innerHTML = "";
    lastLogCount = 0;
  });

  // --- 4.1. Управление переключателями защиты (Шок-контент и Странные фетиши) ---
  const shieldShockBtn = document.getElementById("shield-shock-btn");
  const shieldFetishBtn = document.getElementById("shield-fetish-btn");

  function setupShieldToggle(btn, storageKey, blockedText, allowedText) {
    if (!btn) return;
    const badge = btn.querySelector(".shield-badge");

    const saved = localStorage.getItem(storageKey);
    let isActive = saved !== null ? saved === "true" : true;

    function render() {
      btn.classList.toggle("active", isActive);
      btn.dataset.active = isActive ? "true" : "false";
      if (badge) badge.textContent = isActive ? blockedText : allowedText;
    }

    render();

    btn.addEventListener("click", () => {
      isActive = !isActive;
      localStorage.setItem(storageKey, String(isActive));
      render();
    });
  }

  setupShieldToggle(shieldShockBtn, "tts_shield_shock", "ЗАБЛОКИРОВАН", "РАЗРЕШЁН");
  setupShieldToggle(shieldFetishBtn, "tts_shield_fetish", "ЗАБЛОКИРОВАНЫ", "РАЗРЕШЕНЫ");

  // --- 4.1.1. Второстепенный фильтр: Отсекать арты с пустым центром ---
  const filterEmptyCenterBtn = document.getElementById("filter-empty-center-btn");
  if (filterEmptyCenterBtn) {
    const badge = filterEmptyCenterBtn.querySelector(".secondary-filter-badge");
    const saved = localStorage.getItem("tts_filter_empty_center");
    let isActive = saved !== null ? saved === "true" : true;

    function renderEmptyCenter() {
      filterEmptyCenterBtn.classList.toggle("active", isActive);
      filterEmptyCenterBtn.dataset.active = isActive ? "true" : "false";
      if (badge) badge.textContent = isActive ? "ВКЛ" : "ВЫКЛ";
    }

    renderEmptyCenter();

    filterEmptyCenterBtn.addEventListener("click", () => {
      isActive = !isActive;
      localStorage.setItem("tts_filter_empty_center", String(isActive));
      renderEmptyCenter();
    });
  }

  // --- 4.2. Переключатель режима откровенности контента ---
  const contentModeBadge = document.getElementById("content-mode-badge");
  const modeButtons = document.querySelectorAll(".mode-btn");

  function setContentMode(mode, updateInputs = true) {
    modeButtons.forEach(btn => {
      btn.classList.toggle("active", btn.dataset.mode === mode);
    });

    if (contentModeBadge) {
      if (mode === "explicit") {
        contentModeBadge.textContent = "🔥 Полный 18+";
        contentModeBadge.className = "badge-subtle badge-mode-explicit";
        if (updateInputs) {
          posTagsInput.value = "highres, solo, nude";
          negTagsInput.value = "comic, bad_anatomy, lowres, 3d, text";
        }
      } else if (mode === "light") {
        contentModeBadge.textContent = "🌸 Горячий SFW (Без наготы)";
        contentModeBadge.className = "badge-subtle badge-mode-light";
        if (updateInputs) {
          posTagsInput.value = "highres, solo, -rating:explicit";
          negTagsInput.value = "comic, bad_anatomy, lowres, 3d, text, nude, sex, nipples, penetration, topless, bottomless";
        }
      } else {
        contentModeBadge.textContent = "✨ Сбалансированный";
        contentModeBadge.className = "badge-subtle badge-mode-balanced";
        if (updateInputs) {
          posTagsInput.value = "highres, solo";
          negTagsInput.value = "comic, bad_anatomy, lowres, 3d, text";
        }
      }
    }
    localStorage.setItem("tts_content_mode", mode);
  }

  modeButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      const mode = btn.dataset.mode;
      if (mode) setContentMode(mode, true);
    });
  });

  const savedMode = localStorage.getItem("tts_content_mode");
  if (savedMode) {
    setContentMode(savedMode, false);
  }

  // --- 4.3. Режим масштабирования артов (во весь размер / целиком) ---
  const fitModeBadge = document.getElementById("fit-mode-badge");
  const fitButtons = document.querySelectorAll(".fit-btn");

  function setFitMode(mode) {
    fitButtons.forEach(btn => {
      btn.classList.toggle("active", btn.dataset.fit === mode);
    });
    if (fitModeBadge) {
      if (mode === "contain") {
        fitModeBadge.textContent = "Целиком без обрезки";
      } else {
        fitModeBadge.textContent = "Во весь размер";
      }
    }
    localStorage.setItem("tts_fit_mode", mode);
  }

  fitButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      const mode = btn.dataset.fit;
      if (mode) setFitMode(mode);
    });
  });

  const savedFitMode = localStorage.getItem("tts_fit_mode");
  if (savedFitMode) {
    setFitMode(savedFitMode);
  }

  // Восстановление художников из localStorage
  const savedPosArtists = localStorage.getItem("tts_positive_artists");
  if (savedPosArtists && posArtistsInput) {
    posArtistsInput.value = savedPosArtists;
    updateArtistAllocation();
  }
  const savedNegArtists = localStorage.getItem("tts_negative_artists");
  if (savedNegArtists && negArtistsInput) {
    negArtistsInput.value = savedNegArtists;
  }

  if (posArtistsInput) {
    posArtistsInput.addEventListener("change", () => {
      localStorage.setItem("tts_positive_artists", posArtistsInput.value);
    });
  }
  if (negArtistsInput) {
    negArtistsInput.addEventListener("change", () => {
      localStorage.setItem("tts_negative_artists", negArtistsInput.value);
    });
  }

  // --- 5. Запуск генерации ---
  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    const [cardW, cardH] = cardResolutionSelect.value.split("x").map(Number);
    const activeModeBtn = document.querySelector(".mode-btn.active");
    const activeFitBtn = document.querySelector(".fit-btn.active");
    const payload = {
      source: booruSourceSelect ? booruSourceSelect.value : "xbooru",
      rule34_user_id: rule34UserIdInput ? rule34UserIdInput.value.trim() : "",
      rule34_api_key: rule34ApiKeyInput ? rule34ApiKeyInput.value.trim() : "",
      positive_tags: posTagsInput.value,
      negative_tags: negTagsInput.value,
      characters: charTextarea.value,
      positive_artists: posArtistsInput ? posArtistsInput.value.trim() : "",
      negative_artists: negArtistsInput ? negArtistsInput.value.trim() : "",
      total_cards: parseInt(totalCardsNum.value, 10) || 456,
      year_from: yearFromInput.value ? parseInt(yearFromInput.value, 10) : null,
      year_to: yearToInput.value ? parseInt(yearToInput.value, 10) : null,
      card_width: cardW,
      card_height: cardH,
      threads: threadsCountSelect ? parseInt(threadsCountSelect.value, 10) : 8,
      deck_title: deckTitleInput.value.trim() || "CUSTOM DECK",
      filter_shock_content: shieldShockBtn ? shieldShockBtn.classList.contains("active") : true,
      filter_weird_fetishes: shieldFetishBtn ? shieldFetishBtn.classList.contains("active") : true,
      filter_empty_center: filterEmptyCenterBtn ? filterEmptyCenterBtn.classList.contains("active") : true,
      content_mode: activeModeBtn ? activeModeBtn.dataset.mode : "explicit",
      fit_mode: activeFitBtn ? activeFitBtn.dataset.fit : "cover"
    };

    setRunningState(true);
    resultBox.classList.add("hidden");
    if (errorBox) errorBox.classList.add("hidden");
    progressBar.classList.remove("error");
    lastReportedError = null;

    // Полный сброс консоли и индикаторов для нового запуска
    lastLogCount = 0;
    logsContainer.innerHTML = "";
    progressBar.style.width = "0%";
    percentText.textContent = "0%";
    statusText.textContent = "Инициализация...";

    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!data.success) {
        if (data.error && data.error.includes("уже идёт")) {
          setRunningState(true);
          statusText.textContent = "Подключение к уже запущенной генерации...";
          startPolling();
          return;
        }
        showToast(data.error || "Не удалось запустить", "error", "Ошибка запуска");
        setRunningState(false);
        return;
      }
      startPolling();
    } catch (err) {
      showToast(err.message, "error", "Сетевая ошибка");
      setRunningState(false);
    }
  });

  // Остановка
  btnStop.addEventListener("click", async () => {
    try {
      await fetch("/api/stop", { method: "POST" });
      statusText.textContent = "Запрошена остановка...";
      showToast("Запрошена остановка генерации", "warning", "Остановка");
    } catch (err) {
      console.error(err);
      showToast(err.message, "error", "Ошибка при остановке");
    }
  });

  // Закрытие блока ошибки генерации
  if (btnDismissError) {
    btnDismissError.addEventListener("click", () => {
      if (errorBox) errorBox.classList.add("hidden");
    });
  }

  // Удаление файлов текущей колоды (очистить результат)
  if (btnDeleteCurrentDeck) {
    btnDeleteCurrentDeck.addEventListener("click", async () => {
      if (!confirm("Вы уверены, что хотите удалить файлы сгенерированной колоды (листы и исходные фото) и очистить результат?")) {
        return;
      }
      btnDeleteCurrentDeck.disabled = true;
      btnDeleteCurrentDeck.textContent = "Удаление...";
      try {
        const res = await fetch("/api/delete-current-deck", { method: "POST" });
        const data = await res.json();
        if (data.success) {
          resultBox.classList.add("hidden");
          if (errorBox) errorBox.classList.add("hidden");
          sheetsList.innerHTML = "";
          progressBar.style.width = "0%";
          progressBar.classList.remove("error");
          percentText.textContent = "0%";
          statusText.textContent = "Готов к работе";
          logsContainer.innerHTML = '<div class="log-line text-muted">[00:00:00] Готов к работе.</div>';
          lastLogCount = 0;
          lastReportedError = null;
          updateHistoryStats();
          showToast("Файлы текущей колоды успешно удалены", "success");
        } else {
          showToast(data.error || "Неизвестная ошибка", "error", "Ошибка удаления");
        }
      } catch (e) {
        showToast(e.message, "error", "Сетевая ошибка при удалении");
      } finally {
        btnDeleteCurrentDeck.disabled = false;
        btnDeleteCurrentDeck.textContent = "🗑️ Удалить файлы этой колоды (очистить)";
      }
    });
  }

  // --- УПРАВЛЕНИЕ КНОПКАМИ ОТКРЫТИЯ ПАПОК ---
  async function handleOpenFolderAction(btn, endpoint) {
    if (!btn) return;
    const origHtml = btn.innerHTML;
    try {
      btn.disabled = true;
      btn.innerHTML = `<span style="opacity:0.75">Открытие...</span>`;
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      });
      const data = await res.json();
      if (data.success) {
        btn.innerHTML = `<span>Папка открыта</span>`;
        if (data.path && navigator.clipboard) {
          try {
            await navigator.clipboard.writeText(data.path);
          } catch (_) {}
        }
        setTimeout(() => {
          btn.innerHTML = origHtml;
          btn.disabled = false;
        }, 1600);
      } else {
        showToast((data.error || "Неизвестная ошибка") + (data.path ? `\nПуть: ${data.path}` : ""), "error", "Не удалось открыть папку");
        btn.innerHTML = origHtml;
        btn.disabled = false;
      }
    } catch (err) {
      showToast("Ошибка запроса при открытии папки: " + err.message, "error");
      btn.innerHTML = origHtml;
      btn.disabled = false;
    }
  }

  if (btnOpenFolder) {
    btnOpenFolder.addEventListener("click", () => {
      handleOpenFolderAction(btnOpenFolder, "/api/open-project-folder");
    });
  }

  if (btnOpenResFolder) {
    btnOpenResFolder.addEventListener("click", () => {
      handleOpenFolderAction(btnOpenResFolder, "/api/open-res-folder");
    });
  }

  if (btnOpenPhotos) {
    btnOpenPhotos.addEventListener("click", () => {
      handleOpenFolderAction(btnOpenPhotos, "/api/open-photos-folder");
    });
  }

  // --- СОХРАНИТЬ КАК ИГРУ (Save & Load) ---
  btnSaveGame.addEventListener("click", async () => {
    const deckName = deckTitleInput.value.trim() || "Custom_Deck";
    try {
      btnSaveGame.disabled = true;
      btnSaveGame.textContent = "Сохранение игры...";
      const res = await fetch("/api/save-as-game", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ deck_name: deckName })
      });
      const data = await res.json();
      if (data.success) {
        showToast(`Сохранено как ИГРА (Слот #${data.save_num}): "${data.save_name}".\nВ TTS: Games → Save & Load.`, "success", "Игра сохранена!", 8000);
        loadInstalledDecks();
      } else {
        showToast(data.error || "Не удалось сохранить игру", "error", "Ошибка сохранения");
      }
    } catch (err) {
      showToast(err.message, "error", "Сетевая ошибка");
    } finally {
      btnSaveGame.disabled = false;
      btnSaveGame.textContent = "Сохранить как ИГРУ (Games → Save & Load)";
    }
  });

  // --- СОХРАНИТЬ КАК ОБЪЕКТ (Saved Objects) ---
  btnSaveObject.addEventListener("click", async () => {
    const deckName = deckTitleInput.value.trim() || "Custom_Deck";
    try {
      btnSaveObject.disabled = true;
      btnSaveObject.textContent = "Сохранение объекта...";
      const res = await fetch("/api/save-as-object", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ deck_name: deckName })
      });
      const data = await res.json();
      if (data.success) {
        showToast(`Объект сохранён в Saved Objects под именем "${data.object_name}".\nВ TTS: Objects → Saved Objects.`, "success", "Объект сохранён!", 8000);
      } else {
        showToast(data.error || "Не удалось сохранить объект", "error", "Ошибка сохранения");
      }
    } catch (err) {
      showToast(err.message, "error", "Сетевая ошибка");
    } finally {
      btnSaveObject.disabled = false;
      btnSaveObject.textContent = "Сохранить в Saved Objects";
      loadInstalledDecks();
    }
  });

  // --- НАВИГАЦИОННЫЕ ВКЛАДКИ ---
  const tabButtons = document.querySelectorAll(".tab-btn");
  const tabViews = document.querySelectorAll(".tab-view");
  const installedDecksBadge = document.getElementById("installed-decks-badge");

  tabButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      const targetId = btn.getAttribute("data-tab");
      tabButtons.forEach(b => b.classList.remove("active"));
      tabViews.forEach(v => v.classList.add("hidden"));
      btn.classList.add("active");
      const targetView = document.getElementById(targetId);
      if (targetView) targetView.classList.remove("hidden");
      if (targetId === "tab-installed") {
        loadInstalledDecks();
      }
    });
  });

  const btnGotoGen = document.getElementById("btn-goto-generator");
  if (btnGotoGen) {
    btnGotoGen.addEventListener("click", () => {
      const genTab = document.querySelector('[data-tab="tab-generator"]');
      if (genTab) genTab.click();
    });
  }

  // --- УПРАВЛЕНИЕ ВШИТЫМИ КОЛОДАМИ В TABLETOP SIMULATOR ---
  const installedGrid = document.getElementById("installed-decks-grid");
  const installedEmpty = document.getElementById("installed-empty");
  const btnRefreshDecks = document.getElementById("btn-refresh-decks");
  const btnOpenTtsDir = document.getElementById("btn-open-tts-dir");
  const btnCleanupTtsCache = document.getElementById("btn-cleanup-tts-cache");
  const btnDeleteAllDecks = document.getElementById("btn-delete-all-decks");
  const searchInput = document.getElementById("installed-search");
  const typeFilterSelect = document.getElementById("installed-type-filter");

  // Элементы панели массовых действий с выбранными колодами
  const selectionBar = document.getElementById("installed-selection-bar");
  const selectAllCheckbox = document.getElementById("installed-select-all");
  const selectAllText = document.getElementById("installed-select-all-text");
  const selectedCountBadge = document.getElementById("installed-selected-count");
  const btnExportSelected = document.getElementById("btn-export-selected-decks");
  const btnDeleteSelected = document.getElementById("btn-delete-selected-decks");

  let allInstalledDecks = [];
  const selectedDeckIds = new Set();

  function getFilteredDecks() {
    const query = (searchInput ? searchInput.value : "").trim().toLowerCase();
    const typeFilter = typeFilterSelect ? typeFilterSelect.value : "all";

    return allInstalledDecks.filter(d => {
      const matchName = d.name.toLowerCase().includes(query) || d.filename.toLowerCase().includes(query);
      const matchType = (typeFilter === "all") || (d.type === typeFilter);
      return matchName && matchType;
    });
  }

  function updateSelectionBar() {
    if (!selectionBar) return;
    const count = selectedDeckIds.size;
    if (selectedCountBadge) {
      selectedCountBadge.textContent = `Выбрано: ${count}`;
    }

    if (count > 0) {
      selectionBar.classList.remove("hidden");
    } else {
      selectionBar.classList.add("hidden");
    }

    const visibleDecks = getFilteredDecks();
    if (visibleDecks.length > 0 && visibleDecks.every(d => selectedDeckIds.has(d.id))) {
      if (selectAllCheckbox) selectAllCheckbox.checked = true;
      if (selectAllText) selectAllText.textContent = "Снять выбор";
    } else {
      if (selectAllCheckbox) selectAllCheckbox.checked = false;
      if (selectAllText) selectAllText.textContent = "Выбрать все";
    }
  }

  async function loadInstalledDecks() {
    try {
      if (installedGrid) {
        installedGrid.innerHTML = '<div style="color: var(--text-muted); padding: 30px; grid-column: 1/-1; text-align: center;">Загрузка списка колод...</div>';
      }
      const res = await fetch("/api/tts-decks");
      const data = await res.json();
      allInstalledDecks = data.decks || [];
      if (installedDecksBadge) {
        installedDecksBadge.textContent = allInstalledDecks.length;
      }
      // Очищаем несуществующие выбранные ID
      const existingIds = new Set(allInstalledDecks.map(d => d.id));
      for (const id of Array.from(selectedDeckIds)) {
        if (!existingIds.has(id)) selectedDeckIds.delete(id);
      }
      renderInstalledDecks();
    } catch (e) {
      if (installedGrid) {
        installedGrid.innerHTML = `<div style="color: #ef4444; padding: 30px; grid-column: 1/-1; text-align: center;">Ошибка загрузки колод: ${e.message}</div>`;
      }
    }
  }

  function renderInstalledDecks() {
    if (!installedGrid) return;
    const filtered = getFilteredDecks();

    if (filtered.length === 0) {
      installedGrid.innerHTML = "";
      if (installedEmpty) installedEmpty.classList.remove("hidden");
      updateSelectionBar();
      return;
    }

    if (installedEmpty) installedEmpty.classList.add("hidden");
    installedGrid.innerHTML = "";

    filtered.forEach(deck => {
      const card = document.createElement("div");
      const isSelected = selectedDeckIds.has(deck.id);
      card.className = "deck-card" + (isSelected ? " is-selected" : "");

      const thumbHtml = deck.thumbnail_url
        ? `<img src="${deck.thumbnail_url}" class="deck-card-img" alt="${deck.name}">`
        : `<div class="deck-card-placeholder">DECK</div>`;

      const typeBadgeClass = deck.type === "game" ? "game" : "object";
      const typeLabel = deck.type === "game" ? "Игра (Save & Load)" : "Saved Object";

      let metaPills = `
        <span class="deck-meta-pill">Карт: <strong>${deck.card_count || '—'}</strong></span>
      `;
      if (deck.sheets_count) {
        metaPills += `<span class="deck-meta-pill">Листов: ${deck.sheets_count}</span>`;
      }
      if (deck.has_tier_table) {
        metaPills += `<span class="deck-meta-pill" style="color: #f59e0b; border-color: rgba(245, 158, 11, 0.3);">Стол Tier List</span>`;
      }
      if (deck.has_sky) {
        metaPills += `<span class="deck-meta-pill" style="color: #a855f7; border-color: rgba(168, 85, 247, 0.3);">Тёмный фон</span>`;
      }
      metaPills += `<span class="deck-meta-pill">${deck.date}</span>`;

      card.innerHTML = `
        <div class="deck-card-top">
          <label class="deck-select-wrap" title="Выбрать колоду">
            <input type="checkbox" class="deck-select-checkbox" data-id="${deck.id}" ${isSelected ? "checked" : ""}>
          </label>
          ${thumbHtml}
          <div class="deck-type-tag ${typeBadgeClass}">${typeLabel}</div>
        </div>
        <div class="deck-card-body">
          <div class="deck-card-title" title="${deck.name}">${deck.name}</div>
          <div class="deck-meta-list">
            ${metaPills}
          </div>
          <div class="deck-card-footer">
            <span class="deck-file-text" title="${deck.filename}">${deck.filename}</span>
            <div class="deck-card-actions">
              <button class="deck-export-btn" data-type="${deck.type}" data-filename="${deck.filename}" data-name="${deck.name}" title="Экспортировать эту колоду для друга в .zip">
                📦 Экспорт
              </button>
              <button class="deck-delete-btn" data-type="${deck.type}" data-filename="${deck.filename}" data-name="${deck.name}" title="Удалить колоду из Tabletop Simulator">
                Удалить
              </button>
            </div>
          </div>
        </div>
      `;

      // Чекбокс выбора
      const chk = card.querySelector(".deck-select-checkbox");
      chk.addEventListener("change", (e) => {
        e.stopPropagation();
        if (chk.checked) {
          selectedDeckIds.add(deck.id);
          card.classList.add("is-selected");
        } else {
          selectedDeckIds.delete(deck.id);
          card.classList.remove("is-selected");
        }
        updateSelectionBar();
      });

      // Кнопка экспорта отдельной колоды в .zip для друга
      const expBtn = card.querySelector(".deck-export-btn");
      expBtn.addEventListener("click", () => {
        downloadZipWithProgress(`/api/export-tts-deck?type=${encodeURIComponent(deck.type)}&filename=${encodeURIComponent(deck.filename)}`, {
          title: `📦 Экспорт «${deck.name}»`,
          defaultFilename: `${deck.name}.zip`
        });
      });

      // Кнопка удаления отдельной колоды
      const delBtn = card.querySelector(".deck-delete-btn");
      delBtn.addEventListener("click", async () => {
        if (!confirm(`Удалить колоду "${deck.name}" (${deck.filename}) из Tabletop Simulator?`)) {
          return;
        }
        delBtn.disabled = true;
        delBtn.textContent = "Удаление...";
        try {
          const res = await fetch("/api/delete-tts-deck", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ type: deck.type, filename: deck.filename })
          });
          const resp = await res.json();
          if (resp.success) {
            selectedDeckIds.delete(deck.id);
            showToast(`Колода "${deck.name}" удалена из TTS`, "info", "Удалено");
            loadInstalledDecks();
          } else {
            showToast("Ошибка удаления: " + (resp.message || resp.error || "Неизвестная ошибка"), "error", "Ошибка удаления");
            delBtn.disabled = false;
            delBtn.textContent = "Удалить";
          }
        } catch (err) {
          showToast("Ошибка сети: " + err.message, "error", "Сбой сети");
          delBtn.disabled = false;
          delBtn.textContent = "Удалить";
        }
      });

      installedGrid.appendChild(card);
    });

    updateSelectionBar();
  }

  // Чекбокс «Выбрать все / Снять выбор»
  if (selectAllCheckbox) {
    selectAllCheckbox.addEventListener("change", () => {
      const visible = getFilteredDecks();
      if (selectAllCheckbox.checked) {
        visible.forEach(d => selectedDeckIds.add(d.id));
      } else {
        visible.forEach(d => selectedDeckIds.delete(d.id));
      }
      renderInstalledDecks();
    });
  }

  // Кнопка «Удалить выбранные»
  if (btnDeleteSelected) {
    btnDeleteSelected.addEventListener("click", async () => {
      if (selectedDeckIds.size === 0) return;
      const count = selectedDeckIds.size;
      if (!confirm(`Вы действительно хотите удалить выбранные колоды (${count} шт.) из Tabletop Simulator?`)) {
        return;
      }

      btnDeleteSelected.disabled = true;
      btnDeleteSelected.textContent = "Удаление...";

      const decksToDelete = allInstalledDecks
        .filter(d => selectedDeckIds.has(d.id))
        .map(d => ({ type: d.type, filename: d.filename }));

      try {
        const res = await fetch("/api/delete-selected-tts-decks", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decks: decksToDelete })
        });
        const data = await res.json();
        if (data.success) {
          selectedDeckIds.clear();
          showToast(`Успешно удалено колод: ${data.deleted}`, "success");
          loadInstalledDecks();
        } else {
          showToast("Ошибка удаления: " + (data.error || "Неизвестная ошибка"), "error", "Ошибка");
        }
      } catch (err) {
        showToast("Ошибка сети: " + err.message, "error", "Сбой сети");
      } finally {
        btnDeleteSelected.disabled = false;
        btnDeleteSelected.textContent = "🗑️ Удалить выбранные";
      }
    });
  }

  // Кнопка «Экспорт выбранных колод» для друга
  if (btnExportSelected) {
    btnExportSelected.addEventListener("click", async () => {
      if (selectedDeckIds.size === 0) return;
      const decksToExport = allInstalledDecks
        .filter(d => selectedDeckIds.has(d.id))
        .map(d => ({ type: d.type, filename: d.filename, name: d.name }));

      if (decksToExport.length === 1) {
        const d = decksToExport[0];
        downloadZipWithProgress(`/api/export-tts-deck?type=${encodeURIComponent(d.type)}&filename=${encodeURIComponent(d.filename)}`, {
          title: `📦 Экспорт «${d.name || d.filename}»`,
          defaultFilename: `${d.name || "deck"}.zip`
        });
        return;
      }

      btnExportSelected.disabled = true;
      btnExportSelected.textContent = "Сборка архива...";

      try {
        await downloadZipWithProgress("/api/export-selected-tts-decks", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decks: decksToExport.map(d => ({ type: d.type, filename: d.filename })) }),
          title: `📦 Экспорт выбранных колод (${decksToExport.length} шт.)`,
          defaultFilename: "TTS_Decks_Collection.zip"
        });
      } finally {
        btnExportSelected.disabled = false;
        btnExportSelected.textContent = "📦 Экспорт выбранных";
      }
    });
  }

  if (searchInput) searchInput.addEventListener("input", renderInstalledDecks);
  if (typeFilterSelect) typeFilterSelect.addEventListener("change", renderInstalledDecks);
  if (btnRefreshDecks) btnRefreshDecks.addEventListener("click", loadInstalledDecks);

  if (btnOpenTtsDir) {
    btnOpenTtsDir.addEventListener("click", () => {
      handleOpenFolderAction(btnOpenTtsDir, "/api/open-tts-folder");
    });
  }

  if (btnCleanupTtsCache) {
    btnCleanupTtsCache.addEventListener("click", async () => {
      if (!confirm("Очистить неиспользуемый кэш колод (Mods/Images) и тяжёлые временные текстуры TTS (Images Raw)?\n\nЭто освободит место на диске и ускорит запуск Tabletop Simulator. Ваши колоды и сохранения останутся нетронутыми.")) {
        return;
      }
      btnCleanupTtsCache.disabled = true;
      btnCleanupTtsCache.textContent = "Очистка кэша...";
      try {
        const res = await fetch("/api/cleanup-tts-cache", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ clear_raw: true })
        });
        const data = await res.json();
        if (data.success) {
          showToast(`Кэш очищен! Удалено файлов: ${data.removed_files}, освобождено: ${data.freed_mb} МБ`, "success", "Кэш TTS");
          loadInstalledDecks();
        } else {
          showToast("Ошибка очистки кэша: " + (data.error || "Неизвестная ошибка"), "error", "Ошибка кэша");
        }
      } catch (e) {
        showToast("Ошибка сети: " + e.message, "error", "Сбой сети");
      } finally {
        btnCleanupTtsCache.disabled = false;
        btnCleanupTtsCache.textContent = "🧹 Очистить кэш TTS";
      }
    });
  }

  if (btnDeleteAllDecks) {
    btnDeleteAllDecks.addEventListener("click", async () => {
      if (!confirm("Вы действительно хотите удалить все созданные генератором колоды из Tabletop Simulator? (Ваши личные сохранения затронуты не будут).")) {
        return;
      }
      btnDeleteAllDecks.disabled = true;
      btnDeleteAllDecks.textContent = "Очистка...";
      try {
        const res = await fetch("/api/delete-all-tts-decks", { method: "POST" });
        const data = await res.json();
        if (data.success) {
          selectedDeckIds.clear();
          const cacheInfo = data.freed_mb ? ` (освобождено ${data.freed_mb} МБ кэша)` : "";
          showToast(`Очистка завершена! Удалено игр: ${data.deleted_games}, объектов: ${data.deleted_objects}${cacheInfo}`, "success", "Очистка TTS");
          loadInstalledDecks();
        } else {
          showToast("Ошибка: " + data.error, "error", "Ошибка очистки");
        }
      } catch (e) {
        showToast("Ошибка сети: " + e.message, "error", "Сбой сети");
      } finally {
        btnDeleteAllDecks.disabled = false;
        btnDeleteAllDecks.textContent = "Очистить все колоды из TTS";
      }
    });
  }

  // --- ОЧИСТКА ИСТОРИИ ПОВТОРОВ ---
  async function updateHistoryStats() {
    try {
      const res = await fetch("/api/history-stats");
      const data = await res.json();
      const countEl = document.getElementById("history-count");
      if (countEl) {
        countEl.textContent = data.total_cards_tracked || 0;
      }
    } catch (e) {
      console.warn(e);
    }
  }

  if (btnClearHistory) {
    btnClearHistory.addEventListener("click", async () => {
      const countEl = document.getElementById("history-count");
      const currentCount = countEl ? countEl.textContent.trim() : "0";
      if (!confirm(`Очистить базу history.json (${currentCount} запомненных карт)?\n\nПосле очистки ранее скачанные арты снова смогут попадаться в новых генерациях.`)) {
        return;
      }
      try {
        btnClearHistory.disabled = true;
        const res = await fetch("/api/clear-history", { method: "POST" });
        const data = await res.json();
        if (data.success) {
          await updateHistoryStats();
          showToast("История уникальных карт успешно очищена.", "success");
        } else {
          showToast("Не удалось очистить историю: " + (data.error || "Неизвестная ошибка"), "error", "Ошибка");
        }
      } catch (e) {
        showToast("Ошибка: " + e.message, "error", "Сбой сети");
      } finally {
        btnClearHistory.disabled = false;
      }
    });
  }

  // --- 6. Polling статуса ---
  function startPolling() {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(fetchStatus, 700);
  }

  async function fetchStatus() {
    try {
      const res = await fetch("/api/status");
      const state = await res.json();

      progressBar.style.width = `${state.percent}%`;
      percentText.textContent = `${state.percent}%`;
      statusText.textContent = state.status_message || "...";

      // Логи
      if (state.logs) {
        if (state.logs.length < lastLogCount) {
          logsContainer.innerHTML = "";
          lastLogCount = 0;
        }
        if (state.logs.length > lastLogCount) {
          const newLogs = state.logs.slice(lastLogCount);
          newLogs.forEach(line => {
            const div = document.createElement("div");
            div.className = "log-line";
            div.textContent = line;
            logsContainer.appendChild(div);
          });
          lastLogCount = state.logs.length;
          logsContainer.scrollTop = logsContainer.scrollHeight;
        }
      }

      // Завершение
      if (!state.is_running) {
        clearInterval(pollInterval);
        setRunningState(false);

        if (state.result && state.result.success) {
          if (errorBox) errorBox.classList.add("hidden");
          progressBar.classList.remove("error");
          lastReportedError = null;
          showResults(state.result);
        } else {
          resultBox.classList.add("hidden");
          sheetsList.innerHTML = "";
          if (state.error) {
            statusText.textContent = "Ошибка: " + state.error;
            progressBar.classList.add("error");
            if (errorBox && errorMessageEl) {
              errorMessageEl.textContent = state.error;
              errorBox.classList.remove("hidden");
            }
            if (lastReportedError !== state.error) {
              lastReportedError = state.error;
              showToast(state.error, "error", "Ошибка генерации", 8000);
            }
          }
        }
      }
    } catch (err) {
      console.warn("Ошибка опроса статуса:", err);
    }
  }

  function setRunningState(running) {
    isGenerating = running;
    if (running) {
      btnGenerate.classList.add("hidden");
      btnStop.classList.remove("hidden");
    } else {
      btnGenerate.classList.remove("hidden");
      btnStop.classList.add("hidden");
    }
  }

  function showResults(result, shouldScroll = true) {
    if (result && result.deck_title && deckTitleInput) {
      deckTitleInput.value = result.deck_title;
    }
    let summary = `Успешно создано ${result.total_cards} уникальных карт. Собрано листов: ${result.sheets.length}.`;
    if (result.duplicates_filtered) {
      summary += ` Отсеяно повторов: ${result.duplicates_filtered}.`;
    }
    if (result.empty_center_filtered) {
      summary += ` Отсеяно пустых/смещённых: ${result.empty_center_filtered}.`;
    }
    resultSummary.textContent = summary;
    sheetsList.innerHTML = "";

    result.sheets.forEach((sheet, idx) => {
      const item = document.createElement("div");
      item.style.cssText = "background: var(--bg-input); border: 1px solid var(--border-subtle); padding: 8px 12px; border-radius: 6px; display: flex; justify-content: space-between; align-items: center; font-size: 12px;";
      item.innerHTML = `
        <span><strong>Лист #${idx + 1}</strong> (${sheet.filename})</span>
        <span style="color: var(--text-muted);">Сетка: ${sheet.width}x${sheet.height} | Карт: <strong>${sheet.card_count}</strong></span>
      `;
      sheetsList.appendChild(item);
    });

    resultBox.classList.remove("hidden");
    if (shouldScroll) {
      resultBox.scrollIntoView({ behavior: "smooth" });
    }
    updateHistoryStats();
  }

  // Восстановление состояния при перезагрузке страницы или открытии после закрытия вкладки
  async function checkInitialStatus() {
    try {
      const res = await fetch("/api/status");
      const state = await res.json();

      if (state.is_running) {
        // Подключаемся к запущенной в фоне генерации
        setRunningState(true);
        resultBox.classList.add("hidden");
        progressBar.style.width = `${state.percent}%`;
        percentText.textContent = `${state.percent}%`;
        statusText.textContent = state.status_message || "Генерация продолжается в фоне...";

        if (state.logs && state.logs.length > 0) {
          logsContainer.innerHTML = "";
          state.logs.forEach(line => {
            const div = document.createElement("div");
            div.className = "log-line";
            div.textContent = line;
            logsContainer.appendChild(div);
          });
          lastLogCount = state.logs.length;
          logsContainer.scrollTop = logsContainer.scrollHeight;
        }

        startPolling();
      } else if (state.result && state.result.success) {
        // Восстанавливаем результат готовой генерации
        if (state.result.deck_title && deckTitleInput) {
          deckTitleInput.value = state.result.deck_title;
        }
        progressBar.style.width = "100%";
        percentText.textContent = "100%";
        statusText.textContent = state.status_message || "Генерация завершена";
        if (state.logs && state.logs.length > 0) {
          logsContainer.innerHTML = "";
          state.logs.forEach(line => {
            const div = document.createElement("div");
            div.className = "log-line";
            div.textContent = line;
            logsContainer.appendChild(div);
          });
          lastLogCount = state.logs.length;
          logsContainer.scrollTop = logsContainer.scrollHeight;
        }
        showResults(state.result, false);
      } else {
        resultBox.classList.add("hidden");
        sheetsList.innerHTML = "";
        if (state.error) {
          statusText.textContent = "Ошибка: " + state.error;
          progressBar.classList.add("error");
          if (errorBox && errorMessageEl) {
            errorMessageEl.textContent = state.error;
            errorBox.classList.remove("hidden");
          }
        }
      }
    } catch (err) {
      console.warn("Не удалось проверить статус при загрузке страницы:", err);
    }
  }

  // --- ЭКСПОРТ ДЛЯ ДРУГА (.ZIP) ---
  if (btnExportZip) {
    btnExportZip.addEventListener("click", () => {
      const deckTitle = (deckTitleInput && deckTitleInput.value.trim()) || "Custom_Deck";
      downloadZipWithProgress("/api/export-zip", {
        title: `📦 Экспорт колоды «${deckTitle}»`,
        defaultFilename: `${deckTitle}.zip`
      });
    });
  }

  // --- ИМПОРТ КОЛОДЫ ОТ ДРУГА (.ZIP) ---
  if (btnImportZip && friendZipInput) {
    btnImportZip.addEventListener("click", () => {
      friendZipInput.value = "";
      friendZipInput.click();
    });

    friendZipInput.addEventListener("change", async () => {
      const file = friendZipInput.files[0];
      if (!file) return;

      if (!file.name.toLowerCase().endsWith(".zip")) {
        showToast("Пожалуйста, выберите файл с расширением .zip", "warning", "Неверный формат");
        return;
      }

      btnImportZip.disabled = true;
      btnImportZip.textContent = "Импорт...";

      const reader = new FileReader();
      reader.onload = async () => {
        try {
          const base64Data = reader.result.split(",")[1];
          const res = await fetch("/api/import-zip", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              filename: file.name,
              data: base64Data
            })
          });
          const data = await res.json();
          if (data.success) {
            if (data.is_collection) {
              showToast(`Архив-коллекция успешно импортирована! Добавлено колод: ${data.count}. Все колоды сохранены в Tabletop Simulator.`, "success", "Коллекция импортирована", 8000);
            } else {
              showToast(`Колода от друга успешно импортирована! Сохранение #${data.save_num} ("${data.save_file}"). Загрузите через Games -> Save & Load.`, "success", "Колода импортирована", 9000);
            }
            loadInstalledDecks();
          } else {
            showToast("Ошибка импорта архива: " + (data.error || "Неизвестная ошибка"), "error", "Ошибка импорта");
          }
        } catch (err) {
          showToast("Сетевая ошибка при импорте: " + err.message, "error", "Сбой сети");
        } finally {
          btnImportZip.disabled = false;
          btnImportZip.textContent = "Импорт архива";
          friendZipInput.value = "";
        }
      };

      reader.onerror = () => {
        showToast("Не удалось прочитать выбранный файл.", "error", "Ошибка чтения файла");
        btnImportZip.disabled = false;
        btnImportZip.textContent = "Импорт архива";
      };

      reader.readAsDataURL(file);
    });
  }

  // --- МОДАЛЬНОЕ ОКНО С ПОДСКАЗКОЙ ---
  if (btnShowGuide && guideModal) {
    btnShowGuide.addEventListener("click", () => {
      guideModal.classList.remove("hidden");
    });

    if (guideModalClose) {
      guideModalClose.addEventListener("click", () => {
        guideModal.classList.add("hidden");
      });
    }

    if (guideModalBackdrop) {
      guideModalBackdrop.addEventListener("click", () => {
        guideModal.classList.add("hidden");
      });
    }

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !guideModal.classList.contains("hidden")) {
        guideModal.classList.add("hidden");
      }
    });
  }

  // --- ПЕРЕКЛЮЧАТЕЛЬ ТЕМЫ (Resend Minimalist <-> Frutiger Aero) ---
  const btnThemeToggle = document.getElementById("btn-theme-toggle");
  const themeBtnLabel = document.getElementById("theme-btn-label");

  function applyTheme(theme) {
    if (theme === "frutiger-aero") {
      document.body.classList.add("theme-frutiger-aero");
      if (themeBtnLabel) themeBtnLabel.textContent = "Минимализм";
    } else {
      document.body.classList.remove("theme-frutiger-aero");
      if (themeBtnLabel) themeBtnLabel.textContent = "Frutiger Aero";
    }
  }

  const savedTheme = localStorage.getItem("tts_theme") || "minimal";
  applyTheme(savedTheme);

  if (btnThemeToggle) {
    btnThemeToggle.addEventListener("click", () => {
      const isAero = document.body.classList.contains("theme-frutiger-aero");
      const nextTheme = isAero ? "minimal" : "frutiger-aero";
      localStorage.setItem("tts_theme", nextTheme);
      applyTheme(nextTheme);
    });
  }

  // Первоначальный расчёт и загрузка статистики
  updateCharacterAllocation();
  updateHistoryStats();
  loadInstalledDecks();
  checkInitialStatus();
});
