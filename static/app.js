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
  const historyBadge = document.getElementById("history-badge");
  const btnExportZip = document.getElementById("btn-export-zip");
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

  // Элементы результатов
  const resultBox = document.getElementById("result-box");
  const resultSummary = document.getElementById("result-summary");
  const sheetsList = document.getElementById("sheets-list");

  let pollInterval = null;
  let lastLogCount = 0;

  // --- 1. Синхронизация слайдера и числового поля ---
  function syncCardsCount(val) {
    const num = Math.max(1, parseInt(val, 10) || 1);
    totalCardsRange.value = Math.min(num, 600);
    totalCardsNum.value = num;
    updateCharacterAllocation();
  }

  totalCardsRange.addEventListener("input", () => syncCardsCount(totalCardsRange.value));
  totalCardsNum.addEventListener("input", () => syncCardsCount(totalCardsNum.value));

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

  // --- 5. Запуск генерации ---
  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    const [cardW, cardH] = cardResolutionSelect.value.split("x").map(Number);
    const payload = {
      source: booruSourceSelect ? booruSourceSelect.value : "xbooru",
      rule34_user_id: rule34UserIdInput ? rule34UserIdInput.value.trim() : "",
      rule34_api_key: rule34ApiKeyInput ? rule34ApiKeyInput.value.trim() : "",
      positive_tags: posTagsInput.value,
      negative_tags: negTagsInput.value,
      characters: charTextarea.value,
      total_cards: parseInt(totalCardsNum.value, 10) || 456,
      year_from: yearFromInput.value ? parseInt(yearFromInput.value, 10) : null,
      year_to: yearToInput.value ? parseInt(yearToInput.value, 10) : null,
      card_width: cardW,
      card_height: cardH,
      threads: threadsCountSelect ? parseInt(threadsCountSelect.value, 10) : 8,
      deck_title: deckTitleInput.value.trim() || "CUSTOM DECK"
    };

    setRunningState(true);
    resultBox.classList.add("hidden");

    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!data.success) {
        alert("Ошибка: " + (data.error || "Не удалось запустить"));
        setRunningState(false);
        return;
      }
      startPolling();
    } catch (err) {
      alert("Сетевая ошибка: " + err.message);
      setRunningState(false);
    }
  });

  // Остановка
  btnStop.addEventListener("click", async () => {
    try {
      await fetch("/api/stop", { method: "POST" });
      statusText.textContent = "Запрошена остановка...";
    } catch (err) {
      console.error(err);
    }
  });

  // Папки
  const triggerOpenFolder = async () => {
    try {
      await fetch("/api/open-folder", { method: "POST" });
    } catch (err) {
      alert("Не удалось открыть папку: " + err.message);
    }
  };
  btnOpenFolder.addEventListener("click", triggerOpenFolder);
  btnOpenResFolder.addEventListener("click", triggerOpenFolder);

  if (btnOpenPhotos) {
    btnOpenPhotos.addEventListener("click", async () => {
      try {
        await fetch("/api/open-photos-folder", { method: "POST" });
      } catch (err) {
        alert("Не удалось открыть папку с фото: " + err.message);
      }
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
        alert(`🎉 Успешно!\nСохранено как ИГРА (Слот #${data.save_num}):\n"${data.save_name}"\n\nКак открыть в игре:\n1. Запустите Tabletop Simulator\n2. Нажмите Create -> Singleplayer (или Multiplayer)\n3. Выберите Games -> Save & Load\n4. Загрузите сохранение "${data.save_file}" — стол Tier List и колода уже готовы!`);
        loadInstalledDecks();
      } else {
        alert("Ошибка сохранения игры: " + data.error);
      }
    } catch (err) {
      alert("Ошибка: " + err.message);
    } finally {
      btnSaveGame.disabled = false;
      btnSaveGame.textContent = "🎮 Сохранить как ИГРУ (Games → Save & Load)";
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
        alert(`📦 Успешно!\nОбъект сохранён в Saved Objects под именем:\n"${data.object_name}"\n\nКак использовать:\nВ Tabletop Simulator на любом столе откройте Objects -> Saved Objects и вытащите колоду! (Текстуры привязаны локально, карты больше не будут белыми).`);
      } else {
        alert("Ошибка сохранения объекта: " + data.error);
      }
    } catch (err) {
      alert("Ошибка: " + err.message);
    } finally {
      btnSaveObject.disabled = false;
      btnSaveObject.textContent = "📦 Сохранить в Saved Objects";
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
  const btnDeleteAllDecks = document.getElementById("btn-delete-all-decks");
  const searchInput = document.getElementById("installed-search");
  const typeFilterSelect = document.getElementById("installed-type-filter");

  let allInstalledDecks = [];

  async function loadInstalledDecks() {
    try {
      if (installedGrid) {
        installedGrid.innerHTML = '<div style="color: var(--text-muted); padding: 30px; grid-column: 1/-1; text-align: center;">⏳ Загрузка списка колод...</div>';
      }
      const res = await fetch("/api/tts-decks");
      const data = await res.json();
      allInstalledDecks = data.decks || [];
      if (installedDecksBadge) {
        installedDecksBadge.textContent = allInstalledDecks.length;
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
    const query = (searchInput ? searchInput.value : "").trim().toLowerCase();
    const typeFilter = typeFilterSelect ? typeFilterSelect.value : "all";

    const filtered = allInstalledDecks.filter(d => {
      const matchName = d.name.toLowerCase().includes(query) || d.filename.toLowerCase().includes(query);
      const matchType = (typeFilter === "all") || (d.type === typeFilter);
      return matchName && matchType;
    });

    if (filtered.length === 0) {
      installedGrid.innerHTML = "";
      if (installedEmpty) installedEmpty.classList.remove("hidden");
      return;
    }

    if (installedEmpty) installedEmpty.classList.add("hidden");
    installedGrid.innerHTML = "";

    filtered.forEach(deck => {
      const card = document.createElement("div");
      card.className = "deck-card";

      const thumbHtml = deck.thumbnail_url
        ? `<img src="${deck.thumbnail_url}" class="deck-card-img" alt="${deck.name}">`
        : `<div class="deck-card-placeholder">🎴</div>`;

      const typeBadgeClass = deck.type === "game" ? "game" : "object";
      const typeLabel = deck.type === "game" ? "🎮 Игра (Save & Load)" : "📦 Saved Object";

      let metaPills = `
        <span class="deck-meta-pill">🎴 Карт: <strong>${deck.card_count || '—'}</strong></span>
      `;
      if (deck.sheets_count) {
        metaPills += `<span class="deck-meta-pill">📄 Листов: ${deck.sheets_count}</span>`;
      }
      if (deck.has_tier_table) {
        metaPills += `<span class="deck-meta-pill" style="color: #f59e0b; border-color: rgba(245, 158, 11, 0.3);">🎨 Стол Tier List</span>`;
      }
      if (deck.has_sky) {
        metaPills += `<span class="deck-meta-pill" style="color: #a855f7; border-color: rgba(168, 85, 247, 0.3);">🌌 Тёмный фон</span>`;
      }
      metaPills += `<span class="deck-meta-pill">📅 ${deck.date}</span>`;

      card.innerHTML = `
        <div class="deck-card-top">
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
            <button class="deck-delete-btn" data-type="${deck.type}" data-filename="${deck.filename}" data-name="${deck.name}">
              🗑️ Удалить
            </button>
          </div>
        </div>
      `;

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
            loadInstalledDecks();
          } else {
            alert("Ошибка удаления: " + resp.message);
            delBtn.disabled = false;
            delBtn.textContent = "🗑️ Удалить";
          }
        } catch (err) {
          alert("Ошибка сети: " + err.message);
          delBtn.disabled = false;
          delBtn.textContent = "🗑️ Удалить";
        }
      });

      installedGrid.appendChild(card);
    });
  }

  if (searchInput) searchInput.addEventListener("input", renderInstalledDecks);
  if (typeFilterSelect) typeFilterSelect.addEventListener("change", renderInstalledDecks);
  if (btnRefreshDecks) btnRefreshDecks.addEventListener("click", loadInstalledDecks);

  if (btnOpenTtsDir) {
    btnOpenTtsDir.addEventListener("click", async () => {
      try {
        await fetch("/api/open-tts-folder", { method: "POST" });
      } catch (e) {
        alert("Не удалось открыть папку: " + e.message);
      }
    });
  }

  if (btnDeleteAllDecks) {
    btnDeleteAllDecks.addEventListener("click", async () => {
      if (!confirm("Вы действительно хотите удалить ВСЕ созданные генератором колоды из Tabletop Simulator? (Ваши личные сохранения затронуты не будут).")) {
        return;
      }
      btnDeleteAllDecks.disabled = true;
      btnDeleteAllDecks.textContent = "Очистка...";
      try {
        const res = await fetch("/api/delete-all-tts-decks", { method: "POST" });
        const data = await res.json();
        if (data.success) {
          alert(`🗑️ Очистка завершена!\nУдалено игр: ${data.deleted_games}\nУдалено объектов: ${data.deleted_objects}`);
          loadInstalledDecks();
        } else {
          alert("Ошибка: " + data.error);
        }
      } catch (e) {
        alert("Ошибка сети: " + e.message);
      } finally {
        btnDeleteAllDecks.disabled = false;
        btnDeleteAllDecks.textContent = "🗑️ Очистить все колоды из TTS";
      }
    });
  }

  // --- ОЧИСТКА ИСТОРИИ ПОВТОРОВ ---
  async function updateHistoryStats() {
    try {
      const res = await fetch("/api/history-stats");
      const data = await res.json();
      if (historyBadge) {
        historyBadge.textContent = `🧠 В истории: ${data.total_cards_tracked || 0} карт`;
      }
    } catch (e) {
      console.warn(e);
    }
  }

  if (btnClearHistory) {
    btnClearHistory.addEventListener("click", async () => {
      if (!confirm("Сбросить историю скачанных карточек? После этого ранее скачанные арты смогут снова попадаться в новых генерациях.")) {
        return;
      }
      try {
        await fetch("/api/clear-history", { method: "POST" });
        alert("История успешно очищена! Теперь можно генерировать карты заново с чистого листа.");
        updateHistoryStats();
      } catch (e) {
        alert("Ошибка: " + e.message);
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
      if (state.logs && state.logs.length > lastLogCount) {
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

      // Завершение
      if (!state.is_running) {
        clearInterval(pollInterval);
        setRunningState(false);

        if (state.result && state.result.success) {
          showResults(state.result);
        } else if (state.error) {
          statusText.textContent = "Ошибка: " + state.error;
        }
      }
    } catch (err) {
      console.warn("Ошибка опроса статуса:", err);
    }
  }

  function setRunningState(running) {
    if (running) {
      btnGenerate.classList.add("hidden");
      btnStop.classList.remove("hidden");
    } else {
      btnGenerate.classList.remove("hidden");
      btnStop.classList.add("hidden");
    }
  }

  function showResults(result) {
    let summary = `Успешно создано ${result.total_cards} уникальных карт. Собрано листов: ${result.sheets.length}.`;
    if (result.duplicates_filtered) {
      summary += ` Отсеяно повторов: ${result.duplicates_filtered}.`;
    }
    resultSummary.textContent = summary;
    sheetsList.innerHTML = "";

    result.sheets.forEach((sheet, idx) => {
      const item = document.createElement("div");
      item.style.cssText = "background: var(--bg-input); border: 1px solid var(--border-color); padding: 8px 12px; border-radius: var(--radius-sm); display: flex; justify-content: space-between; align-items: center; font-size: 12px;";
      item.innerHTML = `
        <span>📄 <strong>Лист #${idx + 1}</strong> (${sheet.filename})</span>
        <span style="color: var(--text-muted);">Сетка: ${sheet.width}x${sheet.height} | Карт: <strong>${sheet.card_count}</strong></span>
      `;
      sheetsList.appendChild(item);
    });

    resultBox.classList.remove("hidden");
    resultBox.scrollIntoView({ behavior: "smooth" });
    updateHistoryStats();
  }

  // --- ЭКСПОРТ ДЛЯ ДРУГА (.ZIP) ---
  if (btnExportZip) {
    btnExportZip.addEventListener("click", () => {
      window.location.href = "/api/export-zip";
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
        alert("Пожалуйста, выберите файл с расширением .zip");
        return;
      }

      btnImportZip.disabled = true;
      btnImportZip.textContent = "⏳ Импорт...";

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
            alert(`🎉 Колода от друга успешно импортирована!\n\nСоздано сохранение игры Tabletop Simulator: #${data.save_num}\nФайл: "${data.save_file}" (${data.save_name})\n\n🎮 Как открыть в Tabletop Simulator:\n1. Запустите игру Tabletop Simulator\n2. Нажмите Create -> Singleplayer (или Multiplayer)\n3. Выберите Games -> Save & Load\n4. Загрузите сохранение "${data.save_file}" — стол Tier List и колода друга появятся на столе без белых текстур!`);
            loadInstalledDecks();
          } else {
            alert("Ошибка импорта архива: " + (data.error || "Неизвестная ошибка"));
          }
        } catch (err) {
          alert("Сетевая ошибка при импорте: " + err.message);
        } finally {
          btnImportZip.disabled = false;
          btnImportZip.textContent = "📥 Импортировать от друга";
          friendZipInput.value = "";
        }
      };

      reader.onerror = () => {
        alert("Не удалось прочитать выбранный файл.");
        btnImportZip.disabled = false;
        btnImportZip.textContent = "📥 Импортировать от друга";
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

  // Первоначальный расчёт и загрузка статистики
  updateCharacterAllocation();
  updateHistoryStats();
  loadInstalledDecks();
});
