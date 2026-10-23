"use strict";

// All text from uploaded files is inserted with textContent, never as
// HTML, so a crafted supplier name cannot inject markup or scripts.

const $ = (id) => document.getElementById(id);

const rupees = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
});

const BADGE_TONE = {
  matched: "ok",
  probable_match: "warn",
  duplicate_bill: "warn",
  only_in_2b: "info",
};

let current = null;
let activeStatus = null;

// ---------------------------------------------------------
// Screens
// ---------------------------------------------------------

function show(section) {
  for (const id of ["start", "loading", "results"]) {
    $(id).hidden = id !== section;
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function showError(message) {
  show("start");
  $("form-error").textContent = message;
  $("form-error").hidden = false;
}

async function runCheck(request) {
  $("form-error").hidden = true;
  show("loading");

  try {
    const response = await request();
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      showError(data.detail || "Something went wrong. Please check the files and try again.");
      return;
    }

    render(data);
  } catch {
    showError("Couldn't reach the server. Check your connection and try again.");
  }
}

// ---------------------------------------------------------
// Upload + demo
// ---------------------------------------------------------

$("upload-form").addEventListener("submit", (event) => {
  event.preventDefault();

  const form = new FormData();
  form.append("register", $("register").files[0]);

  for (const file of $("gstr2b").files) {
    form.append("gstr2b", file);
  }

  form.append("buyer_name", $("buyer_name").value.trim());

  runCheck(() => fetch("/api/check", { method: "POST", body: form }));
});

for (const id of ["demo-button", "demo-top"]) {
  $(id).addEventListener("click", () => runCheck(() => fetch("/api/demo")));
}

$("new-check").addEventListener("click", () => show("start"));

$("show-all").addEventListener("change", () => renderList());

// ---------------------------------------------------------
// Rendering
// ---------------------------------------------------------

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatDate(iso) {
  if (!iso) return "";
  const [year, month, day] = iso.split("-");
  return `${day}-${month}-${year}`;
}

function render(data) {
  current = data;
  activeStatus = null;
  $("show-all").checked = false;

  $("result-meta").textContent =
    [data.buyer_name, data.buyer_gstin && `GSTIN ${data.buyer_gstin}`, data.period_text]
      .filter(Boolean)
      .join(" · ");

  const headline = document.querySelector(".headline");
  headline.classList.toggle("clean", data.total_at_risk === 0);

  $("total-at-risk").textContent = rupees.format(data.total_at_risk);
  $("claimable").textContent = `${rupees.format(data.claimable)} confirmed in GSTR-2B and safe to claim`;
  $("download").href = `/api/report/${data.report_id}`;

  renderUrgent(data);
  renderNotices(data);
  renderChips(data);
  renderReminders(data);
  renderList();

  show("results");
}

function renderUrgent(data) {
  const box = $("urgent");
  box.replaceChildren();

  if (!data.urgent.length) {
    box.hidden = true;
    return;
  }

  box.append(element("strong", "", `⚠ ${data.urgent.length} item(s) close to the claim deadline`));

  const list = element("ul");

  for (const item of data.urgent) {
    list.append(
      element(
        "li",
        "",
        `${item.supplier_name || item.supplier_gstin}, invoice ${item.bill_number}: ` +
          `${rupees.format(item.at_risk)}, deadline ${formatDate(item.deadline)} ` +
          `(${item.days_left} days left)`
      )
    );
  }

  box.append(list);
  box.hidden = false;
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
    if (!items.length) continue;

    const box = element("div", "alert notice");
    box.append(element("strong", "", title));

    const list = element("ul");
    for (const item of items) list.append(element("li", "", item));

    box.append(list);
    container.append(box);
  }
}

function renderChips(data) {
  const chips = $("chips");
  chips.replaceChildren();

  for (const item of data.counts) {
    const chip = element("button", "chip");
    chip.type = "button";
    chip.append(element("span", "", item.label), element("strong", "", String(item.count)));

    if (item.at_risk) {
      chip.append(element("span", "chip-risk", rupees.format(item.at_risk)));
    }

    chip.addEventListener("click", () => {
      activeStatus = activeStatus === item.status ? null : item.status;

      if (item.status === "matched") $("show-all").checked = true;

      renderList();
    });

    chips.append(chip);
  }
}

function renderReminders(data) {
  const container = $("reminders");
  container.replaceChildren();

  $("reminders-section").hidden = data.reminders.length === 0;

  for (const reminder of data.reminders) {
    const card = element("div", "card reminder");

    const top = element("div", "reminder-top");
    top.append(
      element("p", "reminder-name", reminder.supplier_name),
      element("span", "reminder-amount", rupees.format(reminder.at_risk))
    );

    const detail = element(
      "p",
      "muted",
      `${reminder.invoices} invoice(s) · GSTIN ${reminder.supplier_gstin}`
    );
    detail.style.margin = "2px 0 0";

    const actions = element("div", "reminder-actions");

    const whatsapp = element("a", "button small whatsapp", "Send on WhatsApp");
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
    card.append(top, detail, actions);
    container.append(card);
  }
}

function renderList() {
  if (!current) return;

  const showAll = $("show-all").checked;

  for (const chip of $("chips").children) {
    const label = chip.firstChild.textContent;
    const item = current.counts.find((c) => c.label === label);
    chip.classList.toggle("active", Boolean(item && item.status === activeStatus));
  }

  const results = current.results.filter((result) => {
    if (activeStatus) return result.status === activeStatus;
    return showAll || result.needs_action;
  });

  $("list-title").textContent = activeStatus
    ? current.counts.find((c) => c.status === activeStatus).label
    : showAll
      ? "All invoices"
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

    const badge = node.querySelector(".badge");
    badge.textContent = result.label;
    badge.classList.add(result.at_risk ? "risk" : BADGE_TONE[result.status] || "warn");

    node.querySelector(".result-amount").textContent = result.at_risk
      ? `${rupees.format(result.at_risk)} at risk`
      : "";

    node.querySelector(".result-supplier").textContent =
      result.supplier_name || result.supplier_gstin;

    const number = result.bill_number || result.gstr2b_number;
    const gst = result.bill_gst ?? result.gstr2b_gst;

    node.querySelector(".result-invoice").textContent =
      `Invoice ${number} · ${formatDate(result.invoice_date)}` +
      (gst !== null ? ` · GST ${rupees.format(gst)}` : "") +
      ` · GSTIN ${result.supplier_gstin}`;

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
