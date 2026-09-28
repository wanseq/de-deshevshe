const ALL = (typeof PRODUCTS !== "undefined") ? PRODUCTS : [];
const PAGE_SIZE = 48;
const STORES = [{key: "atb", name: "АТБ"}, {key: "silpo", name: "Сільпо"}, {key: "fozzy", name: "Фоззі"}];

const search = document.querySelector("#search");
const productsEl = document.querySelector("#products");
const count = document.querySelector("#count");
const hint = document.querySelector("#hint");
const clear = document.querySelector("#clear");
const sort = document.querySelector("#sort");
const categorySelect = document.querySelector("#category");
const loadMoreBtn = document.querySelector("#loadMore");
const heroSub = document.querySelector("#heroSub");
const footerNote = document.querySelector("#footerNote");

let visibleCount = PAGE_SIZE;

const money = v => (v === null || v === undefined)
  ? null
  : v.toLocaleString("uk-UA", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " грн";

function comparablePrices(p) {
  const vals = STORES.map(({key}) => {
    if (p[`${key}_out_of_stock`]) return null;
    if (p.compare_prices) return p.compare_prices[key] ?? null;
    if (p.comparison_mode === "base" && p[`${key}_per_base`] != null) return p[`${key}_per_base`];
    if (p.comparison_mode === "pack") return p[`${key}_raw`] ?? null;
    return p.demo ? (p[key] ?? null) : null;
  });
  return vals.filter(v => v != null).length >= 2 ? vals : [null, null, null];
}

const escapeHtml = v => String(v ?? "").replace(/[&<>"']/g, c =>
  ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[c]);

function buildCategories() {
  const cats = Array.from(new Set(ALL.map(p => p.category).filter(Boolean))).sort((a, b) => a.localeCompare(b, "uk"));
  categorySelect.innerHTML = '<option value="">Усі категорії</option>' +
    cats.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
}

function setupHeader() {
  const hasDemo = ALL.some(p => p.demo);
  const hasReal = ALL.some(p => !p.demo);
  if (hasDemo && !hasReal) {
    hint.textContent = " • демонстраційна база, ціни приблизні";
    heroSub.textContent = "Поки що працюємо тільки для Горішніх Плавнів. Дані нижче — демонстраційні, реальні ціни підключаються через products.js.";
  } else if (hasReal && hasDemo) {
    hint.textContent = " • частина цін демонстраційна";
  } else if (hasReal) {
    hint.textContent = " • у наявності щонайменше у двох магазинах";
    heroSub.textContent = "Порівняння цін АТБ, Сільпо та онлайн-каталогу Фоззі. Ціни й наявність залежать від адреси магазину.";
    footerNote.textContent = `Товарів у базі: ${ALL.length} • Дані з products.js`;
  }
}

function filterSortList() {
  const q = search.value.trim().toLowerCase();
  const cat = categorySelect.value;

  let list = ALL.filter(p => {
    const matchesQ = !q || `${p.name} ${p.tags || ""} ${p.unit || ""}`.toLowerCase().includes(q);
    const matchesCat = !cat || p.category === cat;
    const available = STORES.filter(({key}) => p[key] != null && !p[`${key}_out_of_stock`]).length >= 2;
    return matchesQ && matchesCat && available;
  });

  const minPrice = p => {
    const vals = comparablePrices(p).filter(v => v !== null && v !== undefined);
    return vals.length ? Math.min(...vals) : Infinity;
  };

  if (sort.value === "cheap") {
    list = list.slice().sort((a, b) => minPrice(a) - minPrice(b));
  } else if (sort.value === "name") {
    list = list.slice().sort((a, b) => a.name.localeCompare(b.name, "uk"));
  } else if (sort.value === "diff") {
    const diff = p => {
      const vals = comparablePrices(p).filter(v => v != null);
      return vals.length >= 2 ? Math.max(...vals) - Math.min(...vals) : -1;
    };
    list = list.slice().sort((a, b) => diff(b) - diff(a));
  }

  return list;
}

function priceRow(store, value, raw, perBase, baseUnit, priceLabel, isCheap, outOfStock) {
  if (outOfStock) {
    return `<div class="price">
      <span class="store">${escapeHtml(store)}</span>
      <span class="value" style="color:#98a2b3;font-weight:600;font-size:14px">немає в наявності</span>
    </div>`;
  }
  if (value === null || value === undefined) {
    return `<div class="price">
      <span class="store">${escapeHtml(store)}</span>
      <span class="value" style="color:#98a2b3;font-weight:600;font-size:14px">немає товару</span>
    </div>`;
  }
  const hasBase = perBase != null && baseUnit;
  const mainPrice = hasBase ? `${money(perBase)}/${baseUnit}` : money(value);
  const sourcePrice = raw ?? value;
  return `<div class="price ${isCheap ? 'cheap' : ''}">
    <span class="store">${escapeHtml(store)} ${isCheap ? '<span class="badge">ДЕШЕВШЕ</span>' : ''}</span>
    <span class="price-values"><span class="value">${mainPrice}</span>
      ${hasBase ? `<span class="price-detail">${money(sourcePrice)} ${escapeHtml(priceLabel)}</span>` : (priceLabel ? `<span class="price-detail">${escapeHtml(priceLabel)}</span>` : '')}
    </span>
  </div>`;
}

function render() {
  const list = filterSortList();
  const q = search.value.trim();

  count.textContent = q || categorySelect.value ? `Знайдено: ${list.length}` : `Товари: ${list.length}`;

  if (!list.length) {
    productsEl.innerHTML = '<div class="empty">Нічого не знайдено.<br>Спробуйте іншу назву або категорію.</div>';
    loadMoreBtn.hidden = true;
    return;
  }

  const shown = list.slice(0, visibleCount);

  productsEl.innerHTML = shown.map(p => {
    const comparable = comparablePrices(p);
    const values = comparable.filter(v => v != null);
    const cheapest = values.length >= 2 ? Math.min(...values) : null;
    const uniqueCheapest = cheapest != null && values.filter(v => v === cheapest).length === 1;

    const photo = p.image
      ? `<div class="photo"><img src="${escapeHtml(p.image)}" alt="${escapeHtml(p.name)}" loading="lazy" onerror="this.closest('.photo').classList.add('no-image');this.remove()"></div>`
      : `<div class="photo no-image"><div class="icon">🛍️</div><div>Фото буде додано</div></div>`;

    return `<article class="card">
      ${photo}
      <div class="content">
        ${p.category ? `<div class="category-tag">${escapeHtml(p.category)}</div>` : ""}
        <div class="name">${escapeHtml(p.name)}</div>
        <div class="unit">${escapeHtml(p.unit || "")}</div>
        <div class="prices">
          ${STORES.map(({key, name}, i) => priceRow(name, p[key], p[`${key}_raw`], p[`${key}_per_base`], p[`${key}_base_unit`], p[`${key}_price_label`], uniqueCheapest && comparable[i] === cheapest, p[`${key}_out_of_stock`])).join("")}
        </div>
        <div class="updated">${p.demo ? "Ціна демонстраційна." : (p.store_updated ? "Дати цін — " + STORES.filter(({key}) => p.store_updated[key]).map(({key, name}) => `${escapeHtml(name)}: ${escapeHtml(p.store_updated[key])}`).join(" · ") : `Оновлено: ${escapeHtml(p.updated || "невідомо")}`)}</div>
        ${p.source ? `<div class="source">${escapeHtml(p.source)}</div>` : ""}
        ${p.demo ? `<div class="demo-flag">ДЕМО-ДАНІ</div>` : ""}
      </div>
    </article>`;
  }).join("");

  loadMoreBtn.hidden = visibleCount >= list.length;
}

search.addEventListener("input", () => { visibleCount = PAGE_SIZE; render(); });
sort.addEventListener("change", () => { visibleCount = PAGE_SIZE; render(); });
categorySelect.addEventListener("change", () => { visibleCount = PAGE_SIZE; render(); });
clear.addEventListener("click", () => { search.value = ""; visibleCount = PAGE_SIZE; search.focus(); render(); });
loadMoreBtn.addEventListener("click", () => { visibleCount += PAGE_SIZE; render(); });
document.querySelectorAll(".chips button").forEach(b => b.addEventListener("click", () => {
  search.value = b.dataset.q; visibleCount = PAGE_SIZE; render(); search.focus();
}));

buildCategories();
setupHeader();
render();
