const DISMISS_KEY = "cs2-sniper-dismissed";

let dismissedDeals = loadDismissed();

function loadDismissed() {
  try {
    const raw = localStorage.getItem(DISMISS_KEY);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch {
    return new Set();
  }
}

function saveDismissed() {
  localStorage.setItem(DISMISS_KEY, JSON.stringify([...dismissedDeals]));
}

function dismissDeal(listingId) {
  if (!listingId) return;
  dismissedDeals.add(listingId);
  saveDismissed();
  if (window.__lastDeals) renderDeals(window.__lastDeals);
}

function restoreDismissedDeals() {
  dismissedDeals = new Set();
  localStorage.removeItem(DISMISS_KEY);
  if (window.__lastDeals) renderDeals(window.__lastDeals);
}

let currentFilter = "all";

const els = {
  status: document.getElementById("scannerStatus"),
  meter: document.getElementById("statsMeter"),
  flipBanner: document.getElementById("flipBanner"),
  flipSellVenue: document.getElementById("flipSellVenue"),
  dealsList: document.getElementById("dealsList"),
  hotList: document.getElementById("hotList"),
  settingsForm: document.getElementById("settingsForm"),
  scanNow: document.getElementById("scanNow"),
  toggleSettings: document.getElementById("toggleSettings"),
  controlsPanel: document.getElementById("controlsPanel"),
  restoreDismissed: document.getElementById("restoreDismissed"),
};

function capVenue(v) {
  if (!v) return "CSFloat";
  const s = String(v);
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function buySourceLabel(deal) {
  const src = deal.source || "csfloat";
  const extra = deal.extra || {};
  if (extra.buy_source) return capVenue(extra.buy_source);
  if (src.startsWith("arb:")) return capVenue(src.split(":")[1]);
  return capVenue(src);
}

function money(v) {
  return `$${Number(v).toFixed(2)}`;
}

function priceSourcesHtml(deal) {
  const prices = (deal.extra && deal.extra.prices) || {};
  const keys = ["csfloat", "skinport", "steam", "csgomarket", "waxpeer", "skincash", "lootfarm", "buff"];
  const bits = keys
    .filter((k) => prices[k] != null)
    .map((k) => `${k} ${money(prices[k])}`);
  if (!bits.length) return "";
  return `<span class="sources">${bits.join(" · ")}</span><br/>`;
}

function confidenceHtml(deal) {
  const extra = deal.extra || {};
  const bits = [];

  const agree = extra.sources_agree;
  if (agree != null) {
    const cls = agree >= 3 ? "conf-good" : agree >= 2 ? "conf-ok" : "conf-weak";
    bits.push(`<span class="conf ${cls}">${agree} markets agree</span>`);
  }
  if (extra.instant_bid_usd != null) {
    bits.push(`<span class="conf conf-good">instant bid ${money(extra.instant_bid_usd)}</span>`);
  }
  if (extra.has_stickers) {
    bits.push(`<span class="conf conf-weak">has stickers</span>`);
  }
  if (!bits.length) return "";
  return `<div class="conf-row">${bits.join(" ")}</div>`;
}

function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderDeals(deals) {
  const visible = deals.filter((d) => !dismissedDeals.has(d.listing_id));
  const filtered = visible.filter((d) => {
    if (currentFilter === "all") return true;
    if (currentFilter === "big") return d.is_big_hit;
    if (currentFilter === "actionable") return d.urgency === "actionable";
    if (currentFilter === "watching") return d.urgency === "watching";
    return d.category === currentFilter;
  });

  if (!filtered.length) {
    const hidden = dismissedDeals.size;
    els.dealsList.innerHTML = `<div class="empty">${
      hidden
        ? `No deals in this view — ${hidden} hidden. Use Controls → Restore hidden deals.`
        : "No verified snipes right now. Every candidate failed a safety check — that is the filter working, not a fault."
    }${rejectSummaryHtml()}</div>`;
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
      const buyUrl = d.buy_url || "#";
      const sellVenue = capVenue((d.extra && d.extra.sell_venue) || "csfloat");
      const sellDeposit = (d.extra && d.extra.sell_deposit_url) || "https://csfloat.com/profile";
      const sellPriceUrl = (d.extra && d.extra.sell_url) || sellDeposit;
      const buySource = buySourceLabel(d);
      const extraBuyLinks = Object.entries((d.extra && d.extra.buy_links) || {})
        .filter(([src, url]) => url && url !== buyUrl)
        .slice(0, 4)
        .map(([src, url]) => `<a class="src-link" href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(src)}</a>`)
        .join(" ");

      const buyPrice = Number(d.buy_price_usd);
      const sellPrice = Number(d.sell_target_usd || d.market_price_usd);
      const profit = Number(d.net_profit_usd);

      return `
      <article class="deal ${d.is_big_hit ? "big-hit" : ""} ${d.urgency === "watching" ? "watching" : "actionable"}" data-id="${escapeHtml(d.listing_id)}">
        <button type="button" class="dismiss-btn" data-dismiss="${escapeHtml(d.listing_id)}" title="Hide this deal">×</button>
        <img src="${d.icon_url || ""}" alt="" onerror="this.style.opacity=0.15" />
        <div class="deal-body">
          <div class="deal-head-row">
            <div class="deal-name">${escapeHtml(d.name)}</div>
          </div>
          <div class="flags">${flags}</div>

          <div class="price-diff-bar">
            <div class="price-diff-col">
              <span class="price-diff-label buy-tag">You buy</span>
              <span class="price-diff-value buy-val">${money(buyPrice)}</span>
              <span class="price-diff-venue">${escapeHtml(buySource)}</span>
            </div>
            <div class="price-diff-mid">
              <span class="price-diff-arrow">→</span>
              <span class="price-diff-profit">+${money(profit)}</span>
            </div>
            <div class="price-diff-col">
              <span class="price-diff-label sell-tag">You sell</span>
              <span class="price-diff-value sell-val">~${money(sellPrice)}</span>
              <span class="price-diff-venue">${escapeHtml(sellVenue)}</span>
            </div>
          </div>

          <div class="action-row">
            <a class="action-btn buy-action${dim}" href="${escapeHtml(buyUrl)}" target="_blank" rel="noopener">
              BUY ${money(buyPrice)} · ${escapeHtml(buySource)}
            </a>
            <a class="action-btn sell-action${dim}" href="${escapeHtml(sellDeposit)}" target="_blank" rel="noopener">
              SELL ~${money(sellPrice)} · ${escapeHtml(sellVenue)}
            </a>
            <a class="action-btn check-action" href="${escapeHtml(sellPriceUrl)}" target="_blank" rel="noopener">
              See ${escapeHtml(sellVenue)} price
            </a>
          </div>

          <div class="deal-meta">
            ${priceSourcesHtml(d)}
            ${confidenceHtml(d)}
            ${escapeHtml(d.urgency_reason || "")} · ${escapeHtml(d.sell_speed || "")}
            ${extraBuyLinks ? `<div class="src-links">${extraBuyLinks}</div>` : ""}
          </div>
        </div>
        <div class="deal-side">
          <div class="profit">+${money(profit)}</div>
          <div class="time-left">${escapeHtml(d.time_left_label || "Buy now")}</div>
          <div class="est-label">after fees</div>
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
    .map((h) => {
      const buy = h.buy_url || (h.buy_links && h.buy_links.csfloat) || "#";
      const sellVenue = capVenue(h.sell_venue || "csfloat");
      const sellList = h.sell_deposit_url || "https://csfloat.com/profile";
      const sellCheck = h.sell_url || sellList;
      const ask = Number(h.ask_usd);
      return `
      <div class="liquid-row">
        <div>
          <strong>${escapeHtml(h.name)}</strong>
          <span class="sub">${escapeHtml(h.category)} · sell ~${money(ask)} on ${escapeHtml(sellVenue)}</span>
        </div>
        <div class="liquid-actions">
          <a class="action-btn buy-action" href="${escapeHtml(buy)}" target="_blank" rel="noopener">Buy</a>
          <a class="action-btn sell-action" href="${escapeHtml(sellList)}" target="_blank" rel="noopener">Sell ${escapeHtml(sellVenue)}</a>
          <a class="action-btn check-action" href="${escapeHtml(sellCheck)}" target="_blank" rel="noopener">Price</a>
        </div>
      </div>`;
    })
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

  const sellVenue = capVenue((status.settings && status.settings.sell_venue) || "csfloat");
  if (els.flipSellVenue) els.flipSellVenue.textContent = sellVenue;

  const last = status.last_scan_at
    ? new Date(status.last_scan_at).toLocaleTimeString()
    : "—";

  els.meter.innerHTML = `
    <div class="meter-cell"><span class="meter-label">Scans</span><span class="meter-value">${status.scans_completed ?? 0}</span></div>
    <div class="meter-cell"><span class="meter-label">Checked</span><span class="meter-value">${status.listings_checked ?? 0}</span></div>
    <div class="meter-cell"><span class="meter-label">Alerts</span><span class="meter-value">${status.alerts_sent ?? 0}</span></div>
    <div class="meter-cell"><span class="meter-label">Deals</span><span class="meter-value">${status.deal_count ?? 0}</span></div>
    <div class="meter-cell"><span class="meter-label">Sell on</span><span class="meter-value sell-venue">${escapeHtml(sellVenue)}</span></div>
    ${err ? `<div class="error-line">${escapeHtml(err)}</div>` : ""}
    <div class="meter-cell" style="display:none" data-last="${escapeHtml(last)}"></div>
  `;

  window.__lastRejects = status.reject_reasons || null;
}

// Shown when nothing passes the filters, so a quiet dashboard reads as
// "checked and rejected" rather than "broken".
function rejectSummaryHtml() {
  const rejects = window.__lastRejects;
  if (!rejects || !Object.keys(rejects).length) return "";
  const rows = Object.entries(rejects)
    .map(
      ([reason, count]) =>
        `<li><strong>${Number(count).toLocaleString()}</strong> ${escapeHtml(reason)}</li>`
    )
    .join("");
  return `<div class="reject-summary">
      <div class="reject-title">Rejected this scan — nothing safe enough yet</div>
      <ul>${rows}</ul>
    </div>`;
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

if (els.restoreDismissed) {
  els.restoreDismissed.addEventListener("click", restoreDismissedDeals);
}

els.dealsList.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-dismiss]");
  if (!btn) return;
  e.preventDefault();
  dismissDeal(btn.getAttribute("data-dismiss"));
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
loadMarketPulse().catch(() => {});
connectEvents();

async function loadMarketPulse() {
  const tipsEl = document.getElementById("pulseTips");
  const newsEl = document.getElementById("pulseNews");
  const talkEl = document.getElementById("pulseTalk");
  const linksEl = document.getElementById("pulseLinks");
  if (!tipsEl || !newsEl || !talkEl) return;

  const res = await fetch("/api/market-pulse");
  if (!res.ok) throw new Error("pulse failed");
  const data = await res.json();

  if (linksEl) {
    linksEl.innerHTML = (data.links || [])
      .map(
        (l) =>
          `<a href="${escapeHtml(l.url)}" target="_blank" rel="noopener">${escapeHtml(l.label)}</a>`
      )
      .join("");
  }

  tipsEl.innerHTML = pulseItemsHtml(data.tips || [], "tips");
  newsEl.innerHTML = pulseItemsHtml(data.news || [], "news");
  talkEl.innerHTML = pulseItemsHtml(data.talk || [], "talk");
}

function pulseItemsHtml(items, emptyKind) {
  if (!items.length) {
    const msg =
      emptyKind === "news"
        ? "Steam news unavailable right now — tips still apply."
        : emptyKind === "talk"
          ? "Trader feed quiet or blocked — check the links above."
          : "No tips loaded.";
    return `<div class="pulse-empty">${msg}</div>`;
  }
  return items
    .map((item) => {
      const kind = escapeHtml(item.kind || "tip");
      const title = escapeHtml(item.title || "");
      const summary = escapeHtml(item.summary || "");
      const source = escapeHtml(item.source || "");
      const url = item.url ? escapeHtml(item.url) : "";
      const titleHtml = url
        ? `<a class="pulse-title" href="${url}" target="_blank" rel="noopener">${title}</a>`
        : `<p class="pulse-title">${title}</p>`;
      return `<article class="pulse-item kind-${kind}">
        <span class="pulse-source">${source}</span>
        ${titleHtml}
        ${summary ? `<p class="pulse-summary">${summary}</p>` : ""}
      </article>`;
    })
    .join("");
}
