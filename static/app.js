// HEOR Signal — small interactions, no framework.
(() => {
  "use strict";

  const api = {
    save: (id) => fetch(`/api/save/${encodeURIComponent(id)}`, { method: "POST" }),
    update: () => fetch("/api/update", { method: "POST" }),
    status: () => fetch("/api/status", { headers: { Accept: "application/json" } }),
  };

  // --- Save / unsave ------------------------------------------------------
  function bindSave(button) {
    button.addEventListener("click", async () => {
      if (button.dataset.busy === "1") return;
      button.dataset.busy = "1";
      const previous = button.getAttribute("aria-pressed") === "true";
      const label = button.querySelector(".save-label");
      try {
        const response = await api.save(button.dataset.save);
        if (!response.ok) throw new Error("save failed");
        const data = await response.json();
        const saved = Boolean(data.saved);
        button.classList.toggle("is-saved", saved);
        button.setAttribute("aria-pressed", saved ? "true" : "false");
        button.setAttribute("aria-label", saved ? "Remove from saved" : "Save story");
        if (label) label.textContent = saved ? "Saved" : "Save";
      } catch (error) {
        if (label) label.textContent = "Read-only";
        button.setAttribute("aria-pressed", previous ? "true" : "false");
        window.setTimeout(() => {
          if (label) label.textContent = previous ? "Saved" : "Save";
        }, 1800);
      } finally {
        button.dataset.busy = "0";
      }
    });
  }

  document.querySelectorAll("[data-save]").forEach(bindSave);

  // --- Update / refresh ---------------------------------------------------
  const bar = document.getElementById("update-bar");
  const barText = document.getElementById("update-text");
  const refreshButtons = [
    ...document.querySelectorAll("#refresh, [data-refresh]"),
  ];

  let polling = false;
  let reloading = false;

  function showBar(message, isError = false) {
    if (!bar) return;
    bar.hidden = false;
    bar.classList.toggle("is-error", isError);
    if (barText) barText.textContent = message;
  }

  function hideBar() {
    if (bar) bar.hidden = true;
  }

  function setLoading(loading) {
    refreshButtons.forEach((button) => {
      button.disabled = loading;
      button.classList.toggle("is-loading", loading);
      if (button.id === "refresh") {
        const span = button.querySelector("span");
        if (span) span.textContent = loading ? "Updating" : "Refresh";
      }
    });
  }

  function pollStatus() {
    if (polling) return;
    polling = true;
    const tick = async () => {
      try {
        const response = await api.status();
        const state = await response.json();
        if (state.running) {
          showBar(state.message || "Fetching sources…");
          setLoading(true);
          window.setTimeout(tick, 2500);
          return;
        }
        polling = false;
        setLoading(false);
        if (reloading) return;
        if (state.ok === false) {
          showBar(state.message || "Update failed", true);
          window.setTimeout(hideBar, 12000);
          return;
        }
        showBar(state.message || "Edition updated — reloading…");
        reloading = true;
        window.setTimeout(() => window.location.reload(), 700);
      } catch (error) {
        polling = false;
        setLoading(false);
        showBar("Could not reach the server", true);
        window.setTimeout(hideBar, 8000);
      }
    };
    tick();
  }

  async function startUpdate() {
    if (polling) return;
    setLoading(true);
    showBar("Starting update…");
    try {
      const response = await api.update();
      let data = {};
      try { data = await response.json(); } catch (e) {}
      if (!response.ok) {
        setLoading(false);
        showBar(data.message || "Could not start the update", true);
        window.setTimeout(hideBar, 14000);
        return;
      }
      pollStatus();
    } catch (error) {
      setLoading(false);
      showBar("Could not start the update", true);
      window.setTimeout(hideBar, 8000);
    }
  }

  refreshButtons.forEach((button) => {
    button.addEventListener("click", startUpdate);
  });

  // If the page is opened while an ingest is already running, join it.
  api
    .status()
    .then((response) => response.json())
    .then((state) => {
      if (state.running) {
        setLoading(true);
        showBar(state.message || "Fetching sources…");
        pollStatus();
      }
    })
    .catch(() => {});

  // --- Keyboard: "/" focuses search --------------------------------------
  document.addEventListener("keydown", (event) => {
    if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
    const target = event.target;
    const typing =
      target instanceof HTMLElement &&
      (target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.isContentEditable);
    if (typing) return;
    const input = document.getElementById("q");
    if (input) {
      event.preventDefault();
      input.focus();
      input.select();
    }
  });
})();
