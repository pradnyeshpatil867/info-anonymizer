// info-anonymizer – the browser part. Plain JavaScript, no framework.
//
// One simple flow:  Upload  ->  Analysing (loading screen)  ->  Check and correct  ->  Done
//
// Privacy rules for this file:
// - Personal data is only kept in variables in this page. It is never written to
//   localStorage, cookies or the address bar (URL), because those survive the page.
// - Values are always inserted as text (textContent / input.value), never as HTML.
//   So a value like "<script>" on a document can never run as code.

"use strict";

const FIELD_LABELS = {
  surname: "Surname", given_names: "Given names", birth_name: "Birth name (Geburtsname)",
  date_of_birth: "Date of birth", place_of_birth: "Place of birth", nationality: "Nationality",
  gender: "Sex", address: "Address", phone: "Phone", email: "E-mail", iban: "IBAN",
  steuer_id: "Tax ID (Steuer-ID)", employer: "Employer", height: "Height", eye_colour: "Eye colour",
  id_card_number: "ID card number", passport_number: "Passport number",
  residence_permit_number: "Residence permit number", driving_licence_number: "Driving licence number",
  issue_date: "Date of issue", expiry_date: "Date of expiry", issuing_authority: "Issuing authority",
  permit_type: "Type of permit", remarks: "Remarks",
};
const STATUS_TEXT = { ok: "OK", warn: "Check", fail: "Error" };
const ALLOWED = /\.(jpe?g|png|pdf)$/i;
const MAX_FILES = 4;

// ---------- small helpers ----------
const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

const sizeText = (b) => (b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.ceil(b / 1024) + " KB");
const badge = (status, text) => el("span", { class: "badge " + status, text: text || STATUS_TEXT[status] || status });

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let body = null;
  try { body = await response.json(); } catch { /* not JSON */ }
  if (!response.ok) {
    const detail = body && body.detail;
    const error = new Error(typeof detail === "string" ? detail : `The server answered with error ${response.status}.`);
    error.status = response.status;  // 409 = refused duplicate (not a failure)
    throw error;
  }
  return body;
}

// ---------- show exactly one step ----------
const STEPS = ["upload", "analysing", "review", "done", "search"];

function showStep(name) {
  for (const s of STEPS) $("step-" + s).hidden = s !== name;
  const tab = name === "search" ? "search" : "upload";
  for (const a of document.querySelectorAll(".topbar nav a")) a.classList.toggle("active", a.dataset.tab === tab);
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", () => {
  if (location.hash === "#search") showStep("search");
  else if (location.hash === "#upload") showStep(analysis ? "review" : "upload");
});

// =====================================================================
// Step 1: UPLOAD
// =====================================================================
let pickedFiles = [];  // File objects chosen with either button
const ticked = new Set();  // fileKey of every ticked file (survives redrawing the list)

const fileKey = (f) => `${f.webkitRelativePath || f.name}|${f.size}|${f.lastModified}`;
const displayName = (f) => f.webkitRelativePath || f.name;

function addFiles(fileList) {
  const picked = [...fileList];
  const allowed = picked.filter((f) => ALLOWED.test(f.name));
  const known = new Set(pickedFiles.map(fileKey));
  const fresh = allowed.filter((f) => !known.has(fileKey(f)));
  pickedFiles = pickedFiles.concat(fresh).sort((a, b) => displayName(a).localeCompare(displayName(b)));
  // New files are ticked, as long as the document stays within the file limit.
  for (const f of fresh) if (ticked.size < MAX_FILES) ticked.add(fileKey(f));
  const ignored = picked.length - allowed.length;
  $("pick-info").textContent = `${pickedFiles.length} file(s) in the list.` +
    (ignored ? ` ${ignored} file(s) are not JPG/PNG/PDF and were ignored.` : "") +
    (pickedFiles.length > 1 ? " Untick files that do not belong to this document." : "");
  renderFileList();
}

function renderFileList() {
  const list = $("file-list");
  list.replaceChildren(...pickedFiles.map((f, i) => {
    const box = el("input", { type: "checkbox", "data-index": i });
    box.checked = ticked.has(fileKey(f));
    box.addEventListener("change", () => {
      if (box.checked) ticked.add(fileKey(f)); else ticked.delete(fileKey(f));
      updateAnalyseButton();
    });
    return el("li", {}, el("label", {}, box, displayName(f), el("span", { class: "size", text: sizeText(f.size) })));
  }));
  $("clear-files").hidden = pickedFiles.length === 0;
  updateAnalyseButton();
}

const tickedFiles = () => [...document.querySelectorAll("#file-list input:checked")].map((b) => pickedFiles[Number(b.dataset.index)]);

function updateAnalyseButton() {
  const n = tickedFiles().length;
  $("analyse").disabled = n === 0 || n > MAX_FILES;
  $("analyse").textContent = n > MAX_FILES ? `Too many files (max ${MAX_FILES})` : n > 1 ? `Analyse ${n} files as one document` : "Analyse";
}

for (const id of ["files-input", "folder-input"]) {
  $(id).addEventListener("change", (event) => {
    addFiles(event.target.files);
    event.target.value = "";  // so choosing the same file again still works
  });
}

$("clear-files").addEventListener("click", resetUpload);

function resetUpload() {
  pickedFiles = [];
  ticked.clear();
  $("pick-info").textContent = "No files chosen.";
  renderFileList();
}

// =====================================================================
// Step 2: ANALYSING – the loading screen stays until the server answers
// =====================================================================
let analysis = null;  // the proposal from the server

$("analyse").addEventListener("click", async () => {
  const files = tickedFiles();
  $("analysing-name").textContent = files.map((f) => f.name).join(" + ");
  showStep("analysing");
  const started = Date.now();
  const timer = setInterval(() => { $("analysing-timer").textContent = `${Math.round((Date.now() - started) / 1000)} s`; }, 1000);
  $("analysing-timer").textContent = "0 s";

  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  try {
    analysis = await api("/api/analyse", { method: "POST", body: form });
    showReview();
  } catch (error) {
    showStep("upload");
    $("pick-info").textContent = error.status === 409 ? error.message : "The analysis failed: " + error.message;
  } finally {
    clearInterval(timer);  // the loading screen ends in BOTH cases: result or error
  }
});

// =====================================================================
// Step 3: CHECK AND CORRECT
// =====================================================================
function showReview() {
  const a = analysis;
  $("overall").replaceWith(Object.assign(badge(a.status, `${STATUS_TEXT[a.status]} · ${a.doc_type.doc_type}`), { id: "overall", className: "badge big " + a.status }));

  $("pages").replaceChildren(...Array.from({ length: a.pages }, (_, i) => {
    const img = el("img", { src: `/api/analyses/${a.analysis_id}/pages/${i + 1}`, alt: `Page ${i + 1}` });
    img.addEventListener("click", () => img.classList.toggle("zoomed"));
    return img;
  }));

  // Rejected: only the reason and "Start again". No fields, no person choice, no Save.
  $("rejected").textContent = a.rejected || "";
  $("rejected-box").hidden = !a.rejected;
  $("review-body").hidden = Boolean(a.rejected);
  $("doc-checks").replaceChildren(...a.document_checks.filter((c) => c.status !== "ok")
    .map((c) => el("li", { class: c.status, text: c.message })));

  $("field-rows").replaceChildren();
  for (const f of a.fields) addFieldRow(f.name, f.value, f.status, f.messages);
  refreshAddFieldMenu();
  const canSave = renderPersonChoice(a.match, a.doc_type.doc_type === "FOREIGN_PASSPORT");
  $("save").disabled = Boolean(a.rejected) || !canSave;
  $("save-error").hidden = true;
  showStep("review");
}

function addFieldRow(name, value, status, messages) {
  const input = el("input", { type: "text", "data-field": name, "aria-label": FIELD_LABELS[name] || name });
  input.value = value || "";
  const state = el("td", { class: "state" }, badge(status));
  // "Anonymize" box. Group A (name, birth date, numbers, address, ...): always ticked and locked.
  // Group B (height, dates, authority, ...): clear text in the JSON unless ticked (then a fake value).
  const locked = analysis.always_anonymized.includes(name);
  const box = el("input", { type: "checkbox", class: "anon", "data-field": name,
                            "aria-label": `Anonymize ${FIELD_LABELS[name] || name}` });
  box.checked = locked;
  box.disabled = locked;
  const anon = el("td", { class: "anon-cell", title: locked ? "Always anonymized (personal data)" : "Tick to anonymize this value" },
                  el("label", {}, box, el("span", { class: "small", text: "Anonymize" })));
  const notes = el("ul", { class: "msgs" }, ...(status === "ok" ? [] : messages).map((m) => el("li", { text: m })));
  const row = el("tr", {}, el("td", { class: "label", text: FIELD_LABELS[name] || name }), el("td", {}, input, notes), anon, state);
  // After the user edits a value, the old check result no longer applies.
  input.addEventListener("input", () => {
    row.classList.add("edited");
    state.replaceChildren(el("span", { class: "badge edited-badge", text: "Edited" }));
  });
  $("field-rows").append(row);
}

const shownFields = () => [...document.querySelectorAll('#field-rows input[type="text"]')].map((i) => i.dataset.field);

function refreshAddFieldMenu() {
  const missing = Object.keys(FIELD_LABELS).filter((n) => !shownFields().includes(n));
  $("add-field").replaceChildren(el("option", { value: "", text: "— choose —" }),
    ...missing.map((n) => el("option", { value: n, text: FIELD_LABELS[n] })));
}

$("add-field").addEventListener("change", (event) => {
  if (!event.target.value) return;
  addFieldRow(event.target.value, "", "warn", ["Added by you."]);
  refreshAddFieldMenu();
  $("field-rows").lastElementChild.querySelector('input[type="text"]').focus();
});

// Returns false when the document cannot be saved for anybody.
function renderPersonChoice(match, foreignPassport) {
  const box = $("person-choice");
  box.replaceChildren(el("legend", { class: "small", text: "Save this document for" }));
  $("match-reason").textContent = match ? match.reason : "";
  const options = (match ? match.candidates : []).map((c) => ({ value: c.person_id, text: `${c.pseudonym} (${c.person_id}) – existing person` }));
  // A foreign passport can only be added to a person who already exists.
  if (!foreignPassport) options.push({ value: "", text: "A new person" });
  if (options.length === 0) {
    box.append(el("p", { class: "small", text: "No saved person matches this passport. A foreign passport can only be " +
      "added to a person who already exists: save the German document (for example the residence permit) first, " +
      "then analyse the passport again." }));
    return false;
  }
  // Pre-select only when the app is sure. For "confirm" and "conflict" the user must choose.
  const preselect = !match || ["new", "incomplete"].includes(match.kind) ? "" : match.kind === "existing" ? match.person_id : null;
  for (const o of options) {
    const radio = el("input", { type: "radio", name: "person", value: o.value });
    radio.checked = o.value === preselect;
    box.append(el("label", {}, radio, o.text));
  }
  return true;
}

$("cancel").addEventListener("click", () => { analysis = null; showStep("upload"); });

$("save").addEventListener("click", async () => {
  const chosen = document.querySelector('input[name="person"]:checked');
  if (!chosen) return showSaveError("Please choose the person this document belongs to.");
  const values = {};
  for (const input of document.querySelectorAll('#field-rows input[type="text"]')) {
    if (input.value.trim()) values[input.dataset.field] = input.value.trim();
  }
  // Only the group B boxes the user ticked (group A is always anonymized by the server).
  const anonymize = [...document.querySelectorAll("#field-rows input.anon:checked:not(:disabled)")].map((b) => b.dataset.field);
  $("save").disabled = true;
  try {
    const result = await api(`/api/analyses/${analysis.analysis_id}/approve`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values, anonymize, person_id: chosen.value || null }),
    });
    await showDone(result);
  } catch (error) {
    showSaveError(error.message);
  } finally {
    $("save").disabled = false;
  }
});

function showSaveError(text) { $("save-error").textContent = text; $("save-error").hidden = false; }

// =====================================================================
// Step 4: DONE – confirmation and the JSON file
// =====================================================================
async function showDone(result) {
  const person = await api(`/api/persons/${result.person_id}`);
  $("done-box").textContent = `✓ Document uploaded and completed. Saved ${result.created ? "as a new person" : "for the existing person"} ` +
    `${person.pseudonym} (${result.person_id}).`;
  $("done-warnings").replaceChildren(...result.warnings.map((w) => el("li", { class: "warn", text: w })));
  $("done-json").textContent = JSON.stringify(person.data, null, 2);
  analysis = null;
  showStep("done");
}

$("again").addEventListener("click", () => { resetUpload(); showStep("upload"); location.hash = "#upload"; });
$("rejected-again").addEventListener("click", () => { analysis = null; resetUpload(); showStep("upload"); location.hash = "#upload"; });

// =====================================================================
// SEARCH
// =====================================================================
const PLACEHOLDERS = {
  name: "Name, e.g. Erika Mustermann", pseudonym: "Fake name, e.g. John Miller",
  person_id: "Person ID, e.g. P_7F3K9Q", document_number: "Document number, e.g. T22000129",
};

$("search-kind").addEventListener("change", () => {
  const kind = $("search-kind").value;
  $("search-text").placeholder = PLACEHOLDERS[kind];
  $("search-dob").hidden = kind !== "name";
});

$("search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("search-error").hidden = true;
  const kind = $("search-kind").value;
  const body = { [kind]: $("search-text").value.trim() };
  if (kind === "name") body.date_of_birth = $("search-dob").value.trim();
  const results = $("search-results");
  results.replaceChildren();
  try {
    const persons = await api("/api/persons/search", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (persons.length === 0) results.append(el("p", { class: "muted", text: "Nobody found." }));
    for (const p of persons) {
      results.append(el("div", { class: "card result" },
        el("h2", { text: `${p.pseudonym} · ${p.person_id}` }),
        el("p", { class: "small muted", text: p.match_reason }),
        el("pre", { class: "json", text: JSON.stringify(p.data, null, 2) })));
    }
  } catch (error) {
    $("search-error").textContent = error.message;
    $("search-error").hidden = false;
  }
});

showStep(location.hash === "#search" ? "search" : "upload");
