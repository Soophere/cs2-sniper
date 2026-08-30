let currentFilter = "all";

const els = {
  status: document.getElementById("scannerStatus"),
  meter: document.getElementById("statsMeter"),
  dealsList: document.getElementById("dealsList"),
  hotList: document.getElementById("hotList"),
  settingsForm: document.getElementById("settingsForm"),
  scanNow: document.getElementById("scanNow"),
  toggleSettings: document.getElementById("toggleSettings"),
  controlsPanel: document.getElementById("controlsPanel"),
};

function money(v) {
  return `$${Number(v).toFixed(2)}`;
}

function priceSourcesHtml(deal) {
  const prices = (deal.extra && deal.extra.prices) || {};
  const keys = ["csfloat", "skinport", "steam", "csgomarket", "skincash", "buff"];
  const bits = keys
    .filter((k) => prices[k] != null)
    .map((k) => `${k} ${money(prices[k])}`);
  if (!bits.length) return "";
  return `<span class="sources">${bits.join(" · ")}</span><br/>`;
}

function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderDeals(deals) {
  const filtered = deals.filter((d) => {
    if (currentFilter === "all") return true;
    if (currentFilter === "big") return d.is_big_hit;
    if (currentFilter === "actionable") return d.urgency === "actionable";
    if (currentFilter === "watching") return d.urgency === "watching";
    return d.category === currentFilter;
  });

  if (!filtered.length) {
    els.dealsList.innerHTML = `<div class="empty">No liquid snipes in this view yet. Thin markets (qty &lt; 8) are filtered out.</div>`;
    return;
  }

  els.dealsList.innerHTML = filtered
    .map((d) => {
      const flags = [
        d.is_big_hit ? `<span class="flag big">Big fish</span>` : "",
        d.urgency === "watching"
          ? `<span class="flag watch">Watching</span>`
          : `<span class="flag now">Act now</span>`,
        `<span class="flag cat">${escapeHtml(d.category)}</span>`,
        `<span class="flag cat">${escapeHtml(d.listing_type)}</span>`,
      ].join("");

      const btnLabel = d.listing_type === "auction" ? "Open auction" : "Buy now";
      const dim = d.urgency === "watching" ? " dim" : "";

      return `
      <article class="deal ${d.is_big_hit ? "big-hit" : ""} ${d.urgency === "watching" ? "watching" : "actionable"}">
        <img src="${d.icon_url || ""}" alt="" onerror="this.style.opacity=0.15" />
        <div>
          <div class="deal-name">${escapeHtml(d.name)}</div>
          <div class="flags">${flags}</div>
          <div class="deal-meta">
            ${money(d.buy_price_usd)} buy → ${money(d.market_price_usd)} market · ${d.discount_pct}% under<br/>
            ${priceSourcesHtml(d)}
            ${escapeHtml(d.urgency_reason || "")}<br/>
            ${escapeHtml(d.sell_speed || "")}
          </div>
        </div>
        <div class="deal-side">
          <div class="profit">+${money(d.net_profit_usd)}</div>
          <div class="time-left">${escapeHtml(d.time_left_label || "Buy now")}</div>
          <a class="buy-btn${dim}" href="${d.buy_url}" target="_blank" rel="noopener">${btnLabel}</a>
        </div>
      </article>`;
    })
    .join("");
}

function renderHot(items) {
  if (!items.length) {
    els.hotList.innerHTML = `<div class="empty">Liquid targets appear once price data loads.</div>`;
    return;
  }

  els.hotList.innerHTML = items
    .slice(0, 18)
    .map(
      (h) => `
      <div class="liquid-row">
        <div>
          <strong>${escapeHtml(h.name)}</strong>
          <span class="sub">${escapeHtml(h.category)} · ${escapeHtml(h.sell_speed || "medium")}</span>
        </div>
        <div class="ask">
          ${money(h.ask_usd)}
          <div class="sub">${Number(h.sell_order_count || 0).toLocaleString()} listed</div>
        </div>
      </div>`
    )
    .join("");
}

function renderStatus(status) {
  const apiOk = status.api_key_configured;
  const err = status.last_error;
  const textEl = els.status.querySelector(".status-text");

  let label = "Stopped";
  let cls = "status-block warn";
  if (status.running && apiOk && !err) {
    label = "Scanning";
    cls = "status-block ok";
  } else if (status.running && !apiOk) {
    label = "Need API key";
    cls = "status-block warn";
  } else if (err) {
    label = "Cooldown / error";
    cls = "status-block err";
  }

  els.status.className = cls;
  textEl.textContent = label;
  if (status.light_mode) {
    textEl.textContent = "Light mode";
    els.status.className = "status-block warn";
  }

  const last = status.last_scan_at
    ? new Date(status.last_scan_at).toLocaleTimeString()
    : "—";

  els.meter.innerHTML = `
    <div class="meter-cell"><span class="meter-label">Scans</span><span class="meter-value">${status.scans_completed ?? 0}</span></div>
    <div class="meter-cell"><span class="meter-label">Checked</span><span class="meter-value">${status.listings_checked ?? 0}</span></div>
    <div class="meter-cell"><span class="meter-label">Alerts</span><span class="meter-value">${status.alerts_sent ?? 0}</span></div>
    <div class="meter-cell"><span class="meter-label">Watching</span><span class="meter-value">${status.watching_count ?? 0}</span></div>
    ${err ? `<div class="error-line">${escapeHtml(err)}</div>` : ""}
    <div class="meter-cell" style="display:none" data-last="${escapeHtml(last)}"></div>
  `;
}

async function loadSettings() {
  const res = await fetch("/api/settings");
  const data = await res.json();
  document.getElementById("minProfit").value = data.min_profit_usd;
  document.getElementById("minDiscount").value = data.min_discount_pct;
  document.getElementById("maxBuy").value = data.max_buy_usd;
  document.getElementById("pollInterval").value = data.poll_interval_sec;
}

els.toggleSettings.addEventListener("click", () => {
  els.controlsPanel.hidden = !els.controlsPanel.hidden;
});

els.settingsForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = {
    csfloat_api_key: document.getElementById("csfloatKey").value.trim() || undefined,
    discord_webhook_url: document.getElementById("discordWebhook").value.trim() || undefined,
    min_profit_usd: Number(document.getElementById("minProfit").value),
    min_discount_pct: Number(document.getElementById("minDiscount").value),
    max_buy_usd: Number(document.getElementById("maxBuy").value),
    poll_interval_sec: Number(document.getElementById("pollInterval").value),
  };

  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  els.controlsPanel.hidden = true;
});

els.scanNow.addEventListener("click", async () => {
  els.scanNow.disabled = true;
  els.scanNow.textContent = "…";
  try {
    await fetch("/api/scan", { method: "POST" });
  } finally {
    els.scanNow.disabled = false;
    els.scanNow.textContent = "Scan";
  }
});

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentFilter = btn.dataset.filter;
    if (window.__lastDeals) renderDeals(window.__lastDeals);
  });
});

function connectEvents() {
  const source = new EventSource("/api/events");
  source.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    window.__lastDeals = payload.deals || [];
    renderStatus(payload.status || {});
    renderDeals(window.__lastDeals);
    renderHot(payload.hot_items || []);
  };
  source.onerror = () => {
    const textEl = els.status.querySelector(".status-text");
    els.status.className = "status-block warn";
    textEl.textContent = "Reconnecting";
    source.close();
    setTimeout(connectEvents, 3000);
  };
}

loadSettings().catch(() => {});
connectEvents();
