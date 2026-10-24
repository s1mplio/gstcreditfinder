"use strict";

// All text from uploaded files is inserted with textContent, never as
// HTML, so a crafted supplier name cannot inject markup or scripts.

const $ = (id) => document.getElementById(id);

const rupees = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
});

const wholeRupees = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

const BADGE_TONE = {
  matched: "ok",
  probable_match: "warn",
  duplicate_bill: "warn",
  only_in_2b: "info",
};

const REMINDER_LABELS = {
  pending: "Not sent yet",
  sent: "Message sent",
  replied: "Supplier replied",
  fixed: "Supplier says fixed",
};

let me = null; // { user, businesses } when logged in
let business = null; // the business being viewed
let current = null; // the results on screen
let activeStatus = null;

// ---------------------------------------------------------
// Screens + server calls
// ---------------------------------------------------------

const SCREENS = ["welcome", "auth", "home", "business", "loading", "results"];

function show(section, { scroll = true } = {}) {
  for (const id of SCREENS) {
    $(id).hidden = id !== section;
  }

  $("nav-public").hidden = Boolean(me);
  $("nav-app").hidden = !me;

  if (scroll) window.scrollTo({ top: 0 });
}

function setError(id, message) {
  $(id).textContent = message || "";
  $(id).hidden = !message;
}

async function api(path, options = {}) {
  if (options.json !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(options.json);
  }

  let response;

  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("Couldn't reach the server. Check your connection and try again.");
  }

  const data = await response.json().catch(() => ({}));

  if (response.status === 401 && me && !path.startsWith("/api/login")) {
    signedOut();
  }

  if (!response.ok) {
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "Something went wrong. Please check and try again."
    );
  }

  return data;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function initials(name) {
  const words = String(name || "?").trim().split(/\s+/);
  return (words[0][0] + (words.length > 1 ? words[words.length - 1][0] : "")).toUpperCase();
}

function formatDate(iso) {
  if (!iso) return "";
  const [year, month, day] = iso.split("-");
  return `${day}-${month}-${year}`;
}

function formatTime(seconds) {
  return new Date(seconds * 1000).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

const isCA = () => me && me.user.role === "ca";

function statTile({ label, value, note, tone, iconName }) {
  const tile = element("div", `stat ${tone || ""}`);
  const heading = element("p", "stat-label");
  if (iconName) heading.append(icon(iconName));
  heading.append(label);
  tile.append(heading, element("p", "stat-value", value));
  if (note) tile.append(element("p", "stat-note", note));
  return tile;
}

function banner(className, iconName, title, items) {
  const box = element("div", `banner ${className}`);
  const body = element("div");
  body.append(element("strong", "", title));

  if (items && items.length) {
    const list = element("ul");
    for (const item of items) list.append(element("li", "", item));
    body.append(list);
  }

  box.append(icon(iconName), body);
  return box;
}

// ---------------------------------------------------------
// Landing + login + sign-up
// ---------------------------------------------------------

function openAuth(tab) {
  selectTab(tab);
  show("auth");
  (tab === "login" ? $("login-login") : $("signup-name")).focus();
}

$("nav-login").addEventListener("click", () => openAuth("login"));

for (const id of ["nav-signup", "hero-signup", "cta-signup"]) {
  $(id).addEventListener("click", () => openAuth("signup"));
}

$("auth-back").addEventListener("click", () => show("welcome"));

// Section links on the landing page work from any logged-out screen.
for (const link of document.querySelectorAll(".nav-public a[href^='#']")) {
  link.addEventListener("click", (event) => {
    if (!$("welcome").hidden) return;
    event.preventDefault();
    show("welcome", { scroll: false });
    document.querySelector(link.getAttribute("href")).scrollIntoView();
  });
}

function selectTab(name) {
  $("tab-signup").classList.toggle("active", name === "signup");
  $("tab-login").classList.toggle("active", name === "login");
  $("signup-form").hidden = name !== "signup";
  $("login-form").hidden = name !== "login";
  $("reset-form").hidden = true;
  setError("auth-error");
}

$("tab-signup").addEventListener("click", () => selectTab("signup"));
$("tab-login").addEventListener("click", () => selectTab("login"));

function syncRole() {
  const shop = document.querySelector("input[name=role]:checked").value === "shop";
  $("shop-fields").hidden = !shop;
  $("signup-business").required = shop;
}

for (const radio of document.querySelectorAll("input[name=role]")) {
  radio.addEventListener("change", syncRole);
}

async function submitAuth(form, path, body) {
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  setError("auth-error");

  try {
    signedIn(await api(path, { method: "POST", json: body }));
    form.reset();
    syncRole();
  } catch (error) {
    setError("auth-error", error.message);
  } finally {
    button.disabled = false;
  }
}

$("signup-form").addEventListener("submit", (event) => {
  event.preventDefault();

  submitAuth($("signup-form"), "/api/signup", {
    name: $("signup-name").value,
    login: $("signup-login").value,
    password: $("signup-password").value,
    role: document.querySelector("input[name=role]:checked").value,
    business_name: $("signup-business").value,
    business_gstin: $("signup-gstin").value,
  });
});

$("login-form").addEventListener("submit", (event) => {
  event.preventDefault();

  submitAuth($("login-form"), "/api/login", {
    login: $("login-login").value,
    password: $("login-password").value,
  });
});

// Forgot password: send a code, then set a new password with it.

let resetCodeSent = false;

function openReset() {
  resetCodeSent = false;
  $("reset-form").reset();
  $("reset-login").value = $("login-login").value;
  $("reset-login").readOnly = false;
  $("reset-step2").hidden = true;
  $("reset-code").required = false;
  $("reset-password").required = false;
  $("reset-submit").textContent = "Send code";
  $("reset-intro").textContent = "Enter your mobile number or email. We'll send a 6-digit code.";
  $("login-form").hidden = true;
  $("reset-form").hidden = false;
  setError("auth-error");
  $("reset-login").focus();
}

$("forgot-open").addEventListener("click", openReset);
$("reset-cancel").addEventListener("click", () => selectTab("login"));

$("reset-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("reset-submit");
  button.disabled = true;
  setError("auth-error");

  try {
    if (!resetCodeSent) {
      await api("/api/password/forgot", { method: "POST", json: { login: $("reset-login").value } });
      resetCodeSent = true;
      $("reset-login").readOnly = true;
      $("reset-step2").hidden = false;
      $("reset-code").required = true;
      $("reset-password").required = true;
      $("reset-submit").textContent = "Set new password";
      $("reset-intro").textContent =
        "If an account exists for this number or email, a code is on its way. It expires in 10 minutes.";
      $("reset-code").focus();
    } else {
      signedIn(
        await api("/api/password/reset", {
          method: "POST",
          json: {
            login: $("reset-login").value,
            code: $("reset-code").value,
            password: $("reset-password").value,
          },
        })
      );
    }
  } catch (error) {
    setError("auth-error", error.message);
  } finally {
    button.disabled = false;
  }
});

function signedIn(data) {
  me = data;
  $("notify-toggle").checked = me.user.notify;
  $("user-avatar").textContent = initials(me.user.name);
  $("user-name").textContent = me.user.name;
  $("user-role").textContent = isCA() ? "CA / accountant" : "Shopkeeper";

  // A shopkeeper with one shop goes straight to it.
  if (!isCA() && me.businesses.length === 1) {
    openBusiness(me.businesses[0].id);
  } else {
    renderHome();
  }
}

function signedOut() {
  me = null;
  business = null;
  show("welcome");
}

$("logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  signedOut();
});

$("notify-toggle").addEventListener("change", async () => {
  try {
    me = await api("/api/me/settings", { method: "PUT", json: { notify: $("notify-toggle").checked } });
  } catch (error) {
    $("notify-toggle").checked = !$("notify-toggle").checked;
    alert(error.message);
  }
});

$("delete-account").addEventListener("click", async () => {
  const ok = confirm(
    "Delete your account, all businesses and all saved checks? This cannot be undone."
  );
  if (!ok) return;

  try {
    await api("/api/me", { method: "DELETE" });
    signedOut();
  } catch (error) {
    alert(error.message);
  }
});

// ---------------------------------------------------------
// Dashboard: businesses / clients
// ---------------------------------------------------------

async function refreshMe() {
  me = await api("/api/me");
}

function renderHomeStats() {
  const checked = me.businesses.filter((b) => b.last_check_id);
  const atRisk = checked.reduce((sum, b) => sum + b.total_at_risk, 0);
  const recovered = checked.reduce((sum, b) => sum + (b.recovered || 0), 0);
  const attention = me.businesses.filter((b) => !b.last_check_id || b.total_at_risk > 0).length;
  const word = isCA() ? "Clients" : "Businesses";

  $("home-stats").replaceChildren(
    statTile({
      label: "Credit at risk",
      value: wholeRupees.format(atRisk),
      note: "Across latest checks",
      tone: "risk",
      iconName: "alert",
    }),
    statTile({
      label: "Recovered",
      value: wholeRupees.format(recovered),
      note: "Since the check before",
      tone: "ok",
      iconName: "trend",
    }),
    statTile({
      label: word,
      value: String(me.businesses.length),
      note: `${checked.length} checked`,
      tone: "teal",
      iconName: isCA() ? "briefcase" : "store",
    }),
    statTile({
      label: "Need attention",
      value: String(attention),
      note: "At risk or not checked",
      tone: "warn",
      iconName: "clock",
    })
  );
}

function renderHome() {
  const word = isCA() ? "client" : "business";
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";

  $("home-kicker").textContent = `${greeting}, ${me.user.name.split(" ")[0]}`;
  $("home-title").textContent = isCA() ? "Your clients" : "Your businesses";
  $("add-business-label").textContent = `Add ${word}`;
  $("add-business-title").textContent = `Add a ${word}`;
  $("business-col-name").textContent = isCA() ? "Client" : "Business";
  $("account-login").textContent = `Logged in as ${me.user.login}`;

  renderHomeStats();

  const list = $("business-list");
  list.replaceChildren();

  if (!me.businesses.length) {
    const empty = element("div", "empty-state");
    const badge = element("span", "feature-icon");
    badge.append(icon(isCA() ? "briefcase" : "store"));
    empty.append(
      badge,
      element("b", "", isCA() ? "No clients yet" : "No business yet"),
      element("span", "", isCA()
        ? "Add your first client to run their monthly GSTR-2B check."
        : "Add your business to run your first check.")
    );
    list.append(empty);
    $("add-business-form").hidden = false;
  }

  for (const item of me.businesses) {
    const row = element("button", "table-row business-grid");
    row.type = "button";

    const name = element("div", "cell-main");
    name.append(element("b", "", item.name), element("span", "", item.gstin || "GSTIN not set"));

    const last = element("div", "cell-main");
    if (item.last_check_id) {
      last.append(
        element("span", "cell-muted", item.period_text.replace("GSTR-2B ", "")),
        element("span", "", formatTime(item.last_checked))
      );
    } else {
      last.append(element("span", "", "Not checked yet"));
    }

    const amount = element(
      "span",
      `cell-amount ${item.total_at_risk ? "risk" : ""}`,
      item.last_check_id ? rupees.format(item.total_at_risk) : "—"
    );

    const status = !item.last_check_id
      ? element("span", "status idle", "Pending")
      : item.total_at_risk
        ? element("span", "status risk", "Action needed")
        : element("span", "status ok", "All clear");

    const chevron = icon("chevron");
    chevron.classList.add("row-chevron");

    row.append(name, last, amount, element("span"), chevron);
    row.children[3].append(status);
    row.addEventListener("click", () => openBusiness(item.id));
    list.append(row);
  }

  show("home");
}

$("add-business-toggle").addEventListener("click", () => {
  $("add-business-form").hidden = !$("add-business-form").hidden;
  if (!$("add-business-form").hidden) $("new-business-name").focus();
});

$("add-business-cancel").addEventListener("click", () => {
  $("add-business-form").hidden = true;
  setError("add-business-error");
});

$("add-business-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setError("add-business-error");

  try {
    const created = await api("/api/businesses", {
      method: "POST",
      json: { name: $("new-business-name").value, gstin: $("new-business-gstin").value },
    });
    $("add-business-form").reset();
    $("add-business-form").hidden = true;
    await refreshMe();
    openBusiness(created.id);
  } catch (error) {
    setError("add-business-error", error.message);
  }
});

async function goHome() {
  try {
    await refreshMe();
  } catch {
    return;
  }

  // A shopkeeper with one shop has no list to go back to.
  if (!isCA() && me.businesses.length === 1) {
    openBusiness(me.businesses[0].id);
  } else {
    renderHome();
  }
}

$("back-home").addEventListener("click", goHome);
$("nav-dashboard").addEventListener("click", goHome);

// ---------------------------------------------------------
// One business: upload + history
// ---------------------------------------------------------

async function openBusiness(id) {
  let data;

  try {
    data = await api(`/api/businesses/${id}/checks`);
  } catch (error) {
    alert(error.message);
    return;
  }

  business = data.business;

  $("business-name").textContent = business.name;
  $("business-gstin").textContent = business.gstin ? `GSTIN ${business.gstin}` : "";
  $("back-home").hidden = !isCA() && me.businesses.length === 1;
  $("back-home-label").textContent = isCA() ? "All clients" : "All businesses";
  $("upload-form").reset();
  resetDropzones();
  setError("form-error");

  renderHistory(data.checks);
  closeBillForm();
  show("business");
  loadBills();
}

function renderHistory(checks) {
  const list = $("history");
  list.replaceChildren();

  if (!checks.length) {
    const empty = element("div", "empty-state");
    empty.append(
      element("b", "", "No checks yet"),
      element("span", "", "Upload this month's files above to run the first check.")
    );
    list.append(empty);
    return;
  }

  for (const check of checks) {
    const row = element("div", "table-row history-grid");

    const period = element("div", "cell-main");
    period.append(element("b", "", check.period_text.replace("GSTR-2B ", "GSTR-2B · ")));

    const open = element("button", "button small secondary", "Open");
    open.type = "button";
    open.addEventListener("click", () => openCheck(check.id));

    const download = element("a", "button small secondary");
    download.append(icon("download"), "Excel");
    download.href = `/api/checks/${check.id}/report`;
    download.setAttribute("download", "");

    const remove = element("button", "icon-button");
    remove.type = "button";
    remove.title = "Delete this check";
    remove.setAttribute("aria-label", "Delete this check");
    remove.append(icon("trash"));
    remove.addEventListener("click", async () => {
      if (!confirm("Delete this saved check?")) return;
      try {
        await api(`/api/checks/${check.id}`, { method: "DELETE" });
        openBusiness(business.id);
      } catch (error) {
        alert(error.message);
      }
    });

    const actions = element("div", "cell-actions");
    actions.append(open, download, remove);

    row.append(
      period,
      element("span", "cell-muted", formatTime(check.created)),
      element("span", `cell-amount ${check.total_at_risk ? "risk" : "ok"}`, rupees.format(check.total_at_risk)),
      element("span", `cell-amount ${check.recovered ? "ok" : ""}`, check.recovered ? rupees.format(check.recovered) : "—"),
      actions
    );
    list.append(row);
  }
}

$("delete-business").addEventListener("click", async () => {
  if (!confirm(`Remove ${business.name} and all its saved checks? This cannot be undone.`)) return;

  try {
    await api(`/api/businesses/${business.id}`, { method: "DELETE" });
    business = null;
    await refreshMe();
    renderHome();
  } catch (error) {
    alert(error.message);
  }
});

// Drop zones: the file input covers the zone, so dropping works natively.

function syncDropzone(zone) {
  const input = zone.querySelector("input");
  const hint = zone.querySelector(".drop-hint");
  const files = [...input.files];

  zone.classList.toggle("filled", files.length > 0);
  hint.textContent = files.length
    ? files.length === 1 ? `✓ ${files[0].name}` : `✓ ${files.length} files: ${files.map((f) => f.name).join(", ")}`
    : hint.dataset.empty;
}

function resetDropzones() {
  for (const zone of document.querySelectorAll(".dropzone")) syncDropzone(zone);
}

for (const zone of document.querySelectorAll(".dropzone")) {
  const input = zone.querySelector("input");
  input.addEventListener("change", () => syncDropzone(zone));
  input.addEventListener("dragenter", () => zone.classList.add("dragging"));
  for (const type of ["dragleave", "drop"]) {
    input.addEventListener(type, () => zone.classList.remove("dragging"));
  }
}

$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();

  const months = [...document.querySelectorAll("#saved-bills-list input:checked")].map((box) => box.value);

  if (!$("register").files.length && !months.length) {
    setError("form-error", "Upload a purchase register, or tick a month of saved bills.");
    return;
  }

  if (!$("gstr2b").files.length) {
    setError("form-error", "Add at least one GSTR-2B file.");
    return;
  }

  const form = new FormData();
  if ($("register").files.length) form.append("register", $("register").files[0]);

  for (const file of $("gstr2b").files) {
    form.append("gstr2b", file);
  }

  form.append("business_id", business.id);
  form.append("bill_months", months.join(","));

  setError("form-error");
  show("loading");

  try {
    render(await api("/api/check", { method: "POST", body: form }));
  } catch (error) {
    if (!me) return;
    show("business");
    setError("form-error", error.message);
  }
});

// ---------------------------------------------------------
// Purchase bills entered in the app
// ---------------------------------------------------------

const GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";

// Same rules as gstin.py: format, then the mod-36 check character.
function gstinProblem(gstin) {
  if (gstin.length !== 15) return `GSTIN has 15 characters (this has ${gstin.length})`;
  if (!/^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]{3}$/.test(gstin)) return "This doesn't look like a GSTIN";

  let total = 0;
  for (let i = 0; i < 14; i++) {
    const product = GSTIN_CHARS.indexOf(gstin[i]) * (i % 2 ? 2 : 1);
    total += Math.floor(product / 36) + (product % 36);
  }

  if (gstin[14] !== GSTIN_CHARS[(36 - (total % 36)) % 36]) {
    return "Check digit doesn't match: a character is probably mistyped";
  }

  return "";
}

let billData = null;
let billConfirm = false;
let totalTouched = false;

const amount = (id) => parseAmount($(id).value) || 0;
const fixed = (value) => (Math.round(value * 100) / 100).toFixed(2);

function monthName(month) {
  const [year, number] = month.split("-");
  return periodName(`${number}${year}`);
}

async function loadBills(month = "") {
  const forBusiness = business.id;
  let data;

  try {
    data = await api(`/api/businesses/${forBusiness}/bills${month ? `?month=${month}` : ""}`);
  } catch {
    return;
  }

  if (!business || business.id !== forBusiness) return;
  billData = data;
  renderBills();
  renderSavedBillChoices();
}

function renderBills() {
  const select = $("bills-month");
  select.replaceChildren();

  const months = billData.months.map((m) => m.month);
  if (!months.includes(billData.month)) months.push(billData.month);
  months.sort().reverse();

  for (const month of months) {
    const option = element("option", "", monthName(month));
    option.value = month;
    select.append(option);
  }
  select.value = billData.month;

  const gst = billData.bills.reduce((sum, bill) => sum + bill.gst, 0);
  $("bills-summary").textContent = billData.bills.length
    ? `${billData.bills.length} bill(s) in ${monthName(billData.month)} · GST ${rupees.format(gst)}`
    : "Add each supplier bill as it arrives. No Excel needed.";

  const datalist = $("bill-suppliers");
  datalist.replaceChildren();
  for (const supplier of billData.suppliers) {
    const option = element("option", "", supplier.supplier_name);
    option.value = supplier.supplier_gstin;
    datalist.append(option);
  }

  const list = $("bills-list");
  list.replaceChildren();

  if (!billData.bills.length) {
    const empty = element("div", "empty-state compact");
    empty.append(
      element("b", "", `No bills saved for ${monthName(billData.month)}`),
      element("span", "", "Tap “Add bill” and type in the details from the supplier's bill.")
    );
    list.append(empty);
    return;
  }

  for (const bill of billData.bills) {
    const row = element("div", "bill-row");

    const who = element("div", "cell-main");
    who.append(
      element("b", "", bill.supplier_name || bill.supplier_gstin),
      element("span", "", `${bill.invoice_number} · ${formatDate(bill.invoice_date)}`)
    );

    const totals = element("div", "bill-money");
    totals.append(
      element("b", "", rupees.format(bill.invoice_value)),
      element("span", "", `GST ${rupees.format(bill.gst)}`)
    );

    const remove = element("button", "icon-button");
    remove.type = "button";
    remove.title = "Delete this bill";
    remove.setAttribute("aria-label", `Delete bill ${bill.invoice_number}`);
    remove.append(icon("trash"));
    remove.addEventListener("click", async () => {
      if (!confirm(`Delete bill ${bill.invoice_number}?`)) return;
      try {
        await api(`/api/businesses/${business.id}/bills/${bill.id}`, { method: "DELETE" });
        loadBills(billData.month);
      } catch (error) {
        alert(error.message);
      }
    });

    row.append(who, totals, remove);
    list.append(row);
  }
}

function renderSavedBillChoices() {
  const list = $("saved-bills-list");
  list.replaceChildren();
  $("saved-bills").hidden = billData.months.length === 0;

  billData.months.forEach((month, index) => {
    const label = element("label", "saved-bill-choice");
    const box = element("input");
    box.type = "checkbox";
    box.value = month.month;
    box.checked = index === 0; // the newest month
    label.append(box, element("span", "", `${monthName(month.month)} · ${month.count} bill(s)`));
    list.append(label);
  });
}

$("bills-month").addEventListener("change", () => loadBills($("bills-month").value));

function setPlace(place) {
  $("bill-place").value = place;
  for (const field of document.querySelectorAll(".tax-field")) {
    field.hidden = field.dataset.place !== place;
    if (field.hidden) field.querySelector("input").value = "";
  }
}

function recalculate() {
  const taxable = amount("bill-taxable");
  const rate = $("bill-rate").value;

  if (rate !== "") {
    const tax = (taxable * Number(rate)) / 100;
    if ($("bill-place").value === "inter") {
      $("bill-igst").value = taxable ? fixed(tax) : "";
    } else {
      $("bill-cgst").value = taxable ? fixed(tax / 2) : "";
      $("bill-sgst").value = taxable ? fixed(tax / 2) : "";
    }
  }

  if (!totalTouched) {
    const total = taxable + amount("bill-igst") + amount("bill-cgst") + amount("bill-sgst") + amount("bill-cess");
    $("bill-total").value = taxable ? fixed(total) : "";
  }

  resetConfirm();
}

function resetConfirm() {
  billConfirm = false;
  $("bill-save").textContent = "Save bill";
  $("bill-warnings").hidden = true;
}

function checkGstin() {
  const gstin = $("bill-gstin").value.replace(/\s+/g, "").toUpperCase();
  const hint = $("bill-gstin-hint");

  if (gstin.length < 15) {
    hint.textContent = "";
    hint.className = "field-hint";
    return;
  }

  const problem = gstinProblem(gstin);
  hint.textContent = problem || "✓ Valid GSTIN";
  hint.className = `field-hint ${problem ? "bad" : "good"}`;

  if (!problem) {
    const known = billData && billData.suppliers.find((s) => s.supplier_gstin === gstin);
    if (known && !$("bill-name").value) $("bill-name").value = known.supplier_name;

    if (business.gstin) {
      setPlace(gstin.slice(0, 2) === business.gstin.slice(0, 2) ? "intra" : "inter");
      recalculate();
    }
  }
}

function openBillForm() {
  $("bill-form").reset();
  totalTouched = false;
  setPlace("intra");
  $("bill-date").value = new Date().toLocaleDateString("en-CA"); // local YYYY-MM-DD
  $("bill-date").max = $("bill-date").value;
  $("bill-gstin-hint").textContent = "";
  $("bill-saved").textContent = "";
  setError("bill-error");
  resetConfirm();
  $("bill-form").hidden = false;
  $("bill-add-open").hidden = true;
  $("bill-gstin").focus();
}

function closeBillForm() {
  $("bill-form").hidden = true;
  $("bill-add-open").hidden = false;
}

$("bill-add-open").addEventListener("click", openBillForm);
$("bill-cancel").addEventListener("click", closeBillForm);
$("bill-gstin").addEventListener("input", () => {
  checkGstin();
  resetConfirm();
});

for (const id of ["bill-taxable", "bill-cess"]) $(id).addEventListener("input", recalculate);
$("bill-rate").addEventListener("change", recalculate);
$("bill-place").addEventListener("change", () => {
  setPlace($("bill-place").value);
  recalculate();
});

// Typing a tax amount by hand switches the rate to "Other / mixed".
for (const id of ["bill-igst", "bill-cgst", "bill-sgst"]) {
  $(id).addEventListener("input", () => {
    $("bill-rate").value = "";
    recalculate();
  });
}

$("bill-total").addEventListener("input", () => {
  totalTouched = $("bill-total").value.trim() !== "";
  resetConfirm();
});

for (const id of ["bill-name", "bill-number", "bill-date"]) $(id).addEventListener("input", resetConfirm);

$("bill-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setError("bill-error");

  const body = {
    supplier_gstin: $("bill-gstin").value,
    supplier_name: $("bill-name").value,
    invoice_number: $("bill-number").value,
    invoice_date: $("bill-date").value,
    taxable_value: $("bill-taxable").value,
    igst: $("bill-igst").value || "0",
    cgst: $("bill-cgst").value || "0",
    sgst: $("bill-sgst").value || "0",
    cess: $("bill-cess").value || "0",
    invoice_value: $("bill-total").value,
    confirm: billConfirm,
  };

  const button = $("bill-save");
  button.disabled = true;

  try {
    const response = await fetch(`/api/businesses/${business.id}/bills`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));

    if (response.status === 409) {
      // Looks wrong: show why, and let the user save anyway.
      $("bill-warnings").replaceWith(
        Object.assign(banner("notice bill-warnings", "alert", "Please check this bill", data.warnings), {
          id: "bill-warnings",
        })
      );
      billConfirm = true;
      button.textContent = "Save anyway";
      return;
    }

    if (!response.ok) {
      if (response.status === 401) signedOut();
      setError("bill-error", typeof data.detail === "string" ? data.detail : "Couldn't save the bill.");
      return;
    }

    // Ready for the next bill from the same supplier.
    const saved = data.bill;
    for (const id of ["bill-number", "bill-taxable", "bill-igst", "bill-cgst", "bill-sgst", "bill-cess", "bill-total"]) {
      $(id).value = "";
    }
    totalTouched = false;
    resetConfirm();
    $("bill-saved").textContent = `✓ Saved ${saved.invoice_number}`;
    $("bill-number").focus();
    loadBills(saved.invoice_date.slice(0, 7));
  } catch {
    setError("bill-error", "Couldn't reach the server. Check your connection and try again.");
  } finally {
    button.disabled = false;
  }
});

async function openCheck(id) {
  try {
    render(await api(`/api/checks/${id}`));
  } catch (error) {
    alert(error.message);
  }
}

// ---------------------------------------------------------
// Demo
// ---------------------------------------------------------

async function runDemo() {
  show("loading");

  try {
    render(await api("/api/demo"));
  } catch (error) {
    show(me ? "home" : "welcome");
    alert(error.message);
  }
}

for (const id of ["demo-button", "demo-top"]) {
  $(id).addEventListener("click", runDemo);
}

$("new-check").addEventListener("click", () => {
  if (current && current.check_id && business) {
    openBusiness(business.id);
  } else if (me) {
    goHome();
  } else {
    show("welcome");
  }
});

$("show-all").addEventListener("change", () => renderList());

// ---------------------------------------------------------
// Rendering results
// ---------------------------------------------------------

function toneOf(item) {
  if (item.at_risk) return "risk";
  return BADGE_TONE[item.status] || "warn";
}

function render(data) {
  current = data;
  activeStatus = null;
  $("show-all").checked = false;

  $("result-period").textContent = data.period_text;
  $("result-title").textContent = data.buyer_name;
  $("result-meta").textContent = [
    data.buyer_gstin && `GSTIN ${data.buyer_gstin}`,
    data.created ? `Checked ${formatTime(data.created)}` : "Sample data",
  ]
    .filter(Boolean)
    .join(" · ");

  $("hero-stat").classList.toggle("clean", data.total_at_risk === 0);
  $("total-at-risk").textContent = rupees.format(data.total_at_risk);
  $("hero-note").textContent = data.total_at_risk
    ? "Credit you can't claim until these bills are fixed"
    : "Every bill is in GSTR-2B. Nothing to chase.";

  const bills = data.counts.reduce((sum, c) => sum + c.count, 0);
  const problems = data.counts.filter((c) => c.needs_action).reduce((sum, c) => sum + c.count, 0);

  $("result-stats").replaceChildren(
    statTile({
      label: "Safe to claim",
      value: wholeRupees.format(data.claimable),
      note: "Confirmed in GSTR-2B",
      tone: "ok",
      iconName: "shield",
    }),
    statTile({
      label: "Bills to fix",
      value: String(problems),
      note: `of ${bills} records checked`,
      tone: "warn",
      iconName: "file",
    }),
    statTile({
      label: "Suppliers to chase",
      value: String(data.reminders.length),
      note: data.urgent.length ? `${data.urgent.length} near the deadline` : "None near the deadline",
      tone: "risk",
      iconName: "chat",
    })
  );

  $("download").href = data.check_id
    ? `/api/checks/${data.check_id}/report`
    : `/api/report/${data.report_id}`;

  renderRecovered(data);
  renderUrgent(data);
  renderNotices(data);
  renderBreakdown(data);
  renderCounts(data);
  renderReminders(data);
  renderCompare(data);
  renderList();

  show("results");
}

// ---------------------------------------------------------
// GSTR-3B vs GSTR-2B
// ---------------------------------------------------------

const HEADS = [
  ["igst", "IGST"],
  ["cgst", "CGST"],
  ["sgst", "SGST/UTGST"],
  ["cess", "Cess"],
];

function periodName(period) {
  return new Date(Number(period.slice(2)), Number(period.slice(0, 2)) - 1, 1).toLocaleDateString(
    "en-IN",
    { month: "long", year: "numeric" }
  );
}

function renderCompare(data) {
  const periods = data.itc_2b || []; // checks saved before this feature have none
  $("compare-section").hidden = periods.length === 0;
  $("compare-result").replaceChildren();
  $("compare-form").reset();

  const select = $("compare-period");
  select.replaceChildren();

  for (const row of periods) {
    const option = element("option", "", periodName(row.period));
    option.value = row.period;
    select.append(option);
  }
}

function parseAmount(text) {
  const value = Number(String(text).replace(/[₹,\s]/g, "") || 0);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

$("compare-form").addEventListener("submit", (event) => {
  event.preventDefault();

  const available = current.itc_2b.find((row) => row.period === $("compare-period").value);
  const result = $("compare-result");
  result.replaceChildren();

  const rows = [];

  for (const [key, label] of HEADS) {
    const claimed = parseAmount($(`compare-${key}`).value);

    if (claimed === null) {
      result.append(element("p", "error", `${label}: enter an amount in rupees, like 12500.50`));
      return;
    }

    rows.push({ label, inTwoB: available[key], claimed, difference: claimed - available[key] });
  }

  const excess = rows.reduce((sum, r) => sum + Math.max(r.difference, 0), 0);
  const unclaimed = rows.reduce((sum, r) => sum + Math.max(-r.difference, 0), 0);

  const table = element("div", "compare-table");
  const head = element("div", "compare-row compare-head");
  head.append(
    element("span", "", "Tax"),
    element("span", "num", "In GSTR-2B"),
    element("span", "num", "Claimed in 3B"),
    element("span", "num", "Difference")
  );
  table.append(head);

  for (const r of rows) {
    const row = element("div", "compare-row");
    const tone = r.difference > 0.5 ? "risk" : r.difference < -0.5 ? "ok" : "";
    row.append(
      element("span", "", r.label),
      element("span", "num", rupees.format(r.inTwoB)),
      element("span", "num", rupees.format(r.claimed)),
      element("span", `num cell-amount ${tone}`, `${r.difference > 0 ? "+" : ""}${rupees.format(r.difference)}`)
    );
    table.append(row);
  }

  result.append(table);

  if (excess > 0.5) {
    result.append(
      banner(
        "urgent",
        "alert",
        `${rupees.format(excess)} more claimed in GSTR-3B than GSTR-2B allows for ${periodName(available.period)}`,
        [
          "Check whether this is credit from credit notes, imports or ISD (not counted here yet).",
          "If not, reverse the excess in your next GSTR-3B or be ready to explain it: " +
            "the department issues DRC-01C notices for large differences.",
        ]
      )
    );
  } else if (unclaimed > 0.5) {
    result.append(
      banner(
        "recovered",
        "trend",
        `${rupees.format(unclaimed)} available in GSTR-2B but not claimed`,
        ["You can still claim it in a later GSTR-3B, before the Section 16(4) time limit."]
      )
    );
  } else {
    result.append(banner("recovered", "check", "GSTR-3B matches GSTR-2B for this period.", []));
  }
});

function renderRecovered(data) {
  const box = $("recovered");
  const recovered = data.recovered;

  if (!recovered || !recovered.amount) {
    box.hidden = true;
    return;
  }

  box.replaceWith(
    Object.assign(
      banner(
        "recovered",
        "trend",
        `${rupees.format(recovered.amount)} recovered since your last check`,
        recovered.items.map(
          (item) =>
            `${item.supplier_name || item.supplier_gstin}, invoice ${item.bill_number}: ` +
            `${rupees.format(item.amount)} now in GSTR-2B`
        )
      ),
      { id: "recovered" }
    )
  );
}

function renderUrgent(data) {
  const box = $("urgent");

  if (!data.urgent.length) {
    box.hidden = true;
    return;
  }

  box.replaceWith(
    Object.assign(
      banner(
        "urgent",
        "alert",
        `${data.urgent.length} bill(s) close to the claim deadline`,
        data.urgent.map(
          (item) =>
            `${item.supplier_name || item.supplier_gstin}, invoice ${item.bill_number}: ` +
            `${rupees.format(item.at_risk)}, deadline ${formatDate(item.deadline)} ` +
            `(${item.days_left} days left)`
        )
      ),
      { id: "urgent" }
    )
  );
}

function renderNotices(data) {
  const container = $("notices");
  container.replaceChildren();

  const groups = [
    ["Rows skipped in your purchase register", data.register_errors],
    ["Bills to double-check (amounts don't add up)", data.register_warnings],
    ["Not checked yet", data.skipped],
  ];

  for (const [title, items] of groups) {
    if (items.length) container.append(banner("notice", "alert", title, items));
  }
}

function filterBy(status) {
  activeStatus = activeStatus === status ? null : status;
  if (status === "matched") $("show-all").checked = true;
  renderList();
  $("list-title").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderBreakdown(data) {
  const container = $("breakdown");
  container.replaceChildren();

  const rows = data.counts.filter((c) => c.at_risk).sort((a, b) => b.at_risk - a.at_risk);

  if (!rows.length) {
    container.append(element("p", "breakdown-empty", "No credit at risk. Every bill matched."));
    return;
  }

  const largest = rows[0].at_risk;

  for (const item of rows) {
    const row = element("button", "bar-row");
    row.type = "button";
    row.dataset.status = item.status;
    row.title = `${item.label}: ${rupees.format(item.at_risk)} across ${item.count} bill(s). Click to see them.`;

    const track = element("span", "bar-track");
    const fill = element("span", "bar-fill");
    fill.style.width = `${(item.at_risk / largest) * 100}%`;
    track.append(fill);

    row.append(element("span", "bar-label", item.label), track, element("span", "bar-value", rupees.format(item.at_risk)));
    row.addEventListener("click", () => filterBy(item.status));
    container.append(row);
  }
}

function renderCounts(data) {
  const container = $("chips");
  container.replaceChildren();

  for (const item of data.counts) {
    const row = element("button", "count-row");
    row.type = "button";
    row.dataset.status = item.status;
    row.append(element("span", `dot ${toneOf(item)}`), element("span", "count-label", item.label), element("b", "", String(item.count)));
    row.addEventListener("click", () => filterBy(item.status));
    container.append(row);
  }
}

function reminderStatusPicker(reminder) {
  const saved = (current.reminder_status || {})[reminder.supplier_gstin];

  const wrapper = element("label", "reminder-status");
  wrapper.append(element("span", "muted", "Status"));

  const select = element("select");
  for (const [value, text] of Object.entries(REMINDER_LABELS)) {
    const option = element("option", "", text);
    option.value = value;
    select.append(option);
  }
  select.value = saved ? saved.status : "pending";

  const when = element("span", "muted reminder-updated", saved ? `since ${formatTime(saved.updated)}` : "");

  select.addEventListener("change", async () => {
    try {
      current.reminder_status = await api(
        `/api/businesses/${current.business_id}/reminders/${encodeURIComponent(reminder.supplier_gstin)}`,
        { method: "PUT", json: { status: select.value } }
      );
      when.textContent = "Saved ✓";
    } catch (error) {
      when.textContent = error.message;
    }
  });

  wrapper.append(select, when);
  return wrapper;
}

function renderReminders(data) {
  const container = $("reminders");
  container.replaceChildren();

  $("reminders-section").hidden = data.reminders.length === 0;

  for (const reminder of data.reminders) {
    const card = element("div", "card reminder");

    const who = element("div", "reminder-who");
    const text = element("div");
    text.style.minWidth = "0";
    text.append(
      element("p", "reminder-name", reminder.supplier_name),
      element("p", "reminder-detail", `${reminder.invoices} invoice(s) · ${reminder.supplier_gstin}`)
    );
    who.append(element("span", "avatar", initials(reminder.supplier_name)), text);

    const top = element("div", "reminder-top");
    top.append(who, element("span", "reminder-amount", rupees.format(reminder.at_risk)));

    const actions = element("div", "reminder-actions");

    const whatsapp = element("a", "button small whatsapp");
    whatsapp.append(icon("chat"), "Send on WhatsApp");
    whatsapp.href = `https://wa.me/?text=${encodeURIComponent(reminder.message)}`;
    whatsapp.target = "_blank";
    whatsapp.rel = "noopener";

    const copy = element("button", "button small secondary", "Copy message");
    copy.type = "button";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(reminder.message);
        copy.textContent = "Copied ✓";
      } catch {
        copy.textContent = "Copy failed";
      }
      setTimeout(() => (copy.textContent = "Copy message"), 2000);
    });

    actions.append(whatsapp, copy);
    card.append(top, actions);

    // Saved checks can track each supplier; the demo cannot.
    if (data.check_id) card.append(reminderStatusPicker(reminder));

    container.append(card);
  }
}

function renderList() {
  if (!current) return;

  const showAll = $("show-all").checked;

  for (const row of document.querySelectorAll(".count-row, .bar-row")) {
    row.classList.toggle("active", row.dataset.status === activeStatus);
  }

  const results = current.results.filter((result) => {
    if (activeStatus) return result.status === activeStatus;
    return showAll || result.needs_action;
  });

  $("list-title").textContent = activeStatus
    ? current.counts.find((c) => c.status === activeStatus).label
    : showAll
      ? "All bills"
      : "Problems to fix";

  const list = $("results-list");
  list.replaceChildren();

  if (!results.length) {
    list.append(element("div", "card empty", "Nothing here. Every bill matched GSTR-2B. 🎉"));
    return;
  }

  const template = $("result-template");

  for (const result of results) {
    const node = template.content.firstElementChild.cloneNode(true);
    node.classList.toggle("has-risk", Boolean(result.at_risk));

    const badge = node.querySelector(".badge");
    badge.textContent = result.label;
    badge.classList.add(toneOf(result));

    node.querySelector(".result-supplier").textContent =
      result.supplier_name || result.supplier_gstin;

    node.querySelector(".result-amount").textContent = result.at_risk ? rupees.format(result.at_risk) : "";
    node.querySelector(".result-amount-label").textContent = result.at_risk ? "at risk" : "";

    const number = result.bill_number || result.gstr2b_number;
    const gst = result.bill_gst ?? result.gstr2b_gst;

    node.querySelector(".result-invoice").textContent =
      `Invoice ${number} · ${formatDate(result.invoice_date)}` +
      (gst !== null ? ` · GST ${rupees.format(gst)}` : "") +
      ` · ${result.supplier_gstin}`;

    node.querySelector(".result-action").textContent =
      result.needs_action ? `→ ${result.action}` : "";

    const notes = node.querySelector(".result-notes");

    for (const note of result.notes) {
      notes.append(element("li", "", note));
    }

    if (result.deadline && result.at_risk) {
      notes.append(
        element("li", "", `Claim deadline ${formatDate(result.deadline)} (${result.days_left} days left)`)
      );
    }

    list.append(node);
  }
}

// ---------------------------------------------------------
// Start
// ---------------------------------------------------------

syncRole();

fetch("/api/me")
  .then((response) => (response.ok ? response.json() : null))
  .then((data) => (data ? signedIn(data) : show("welcome")))
  .catch(() => show("welcome"));
