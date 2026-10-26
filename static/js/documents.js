(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    user: null,
    personal: [],
    company: [],
    all: [],
    policies: [],
    view: "mine",
  };

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);

  function formatDate(value) {
    if (!value) return "Date unavailable";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric", month: "short", day: "numeric",
    }).format(parsed);
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers: { ...(options.headers || {}) },
    });
    let data = {};
    try { data = await response.json(); } catch { /* Empty response. */ }
    if (response.status === 401) {
      window.location.replace("/?next=/documents");
      throw new Error("Sign in to access document management.");
    }
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status}).`);
    return data;
  }

  function isAdmin() { return state.user?.role === "admin"; }
  function indexState(doc) { return Number(doc?.chunks || 0) > 0 ? "indexed" : "attention"; }
  function indexStateLabel(doc) { return indexState(doc) === "indexed" ? "Indexed" : "Needs attention"; }

  function setStatus(id, message, isError = false) {
    const node = $(id);
    if (!node) return;
    node.textContent = message || "";
    node.classList.toggle("is-error", Boolean(isError));
  }

  function updateStats() {
    const documents = isAdmin() ? state.all : state.personal;
    const indexed = documents.filter((doc) => indexState(doc) === "indexed").length;
    const chunks = documents.reduce((total, doc) => total + Math.max(0, Number(doc.chunks || 0)), 0);
    $("dmStatTotal").textContent = String(documents.length);
    $("dmStatIndexed").textContent = String(indexed);
    $("dmStatChunks").textContent = String(chunks);
    $("dmStatAttention").textContent = String(documents.length - indexed);
  }

  function filterDocuments(documents, searchId, statusId, includeUploader = false) {
    const query = $(searchId).value.trim().toLocaleLowerCase();
    const status = $(statusId).value;
    return documents.filter((doc) => {
      const text = [doc.filename, ...(includeUploader ? [doc.uploader_name, doc.uploader_email, doc.kind] : [])]
        .map((item) => String(item || "").toLocaleLowerCase()).join(" ");
      return text.includes(query) && (status === "all" || indexState(doc) === status);
    });
  }

  function renderDocumentRows(containerId, documents, kind, adminView = false) {
    const container = $(containerId);
    if (!documents.length) {
      container.innerHTML = '<div class="documents-empty">No documents match this search or filter.</div>';
      return;
    }
    container.innerHTML = documents.map((doc) => {
      const chunks = Math.max(0, Number(doc.chunks || 0));
      const status = indexState(doc);
      const isAdminRow = adminView;
      const scope = doc.kind === "company" ? "Company" : "Private upload";
      const uploader = doc.uploader_name || "Unknown user";
      const email = doc.uploader_email || "No email available";
      const sizeKb = Math.max(0, Number(doc.size_bytes || 0) / 1024).toFixed(0);
      const deleteAction = isAdminRow ? "admin-delete" : "delete";
      const meta = isAdminRow
        ? `<p class="documents-record-owner">Uploaded by <strong>${esc(uploader)}</strong> <span>· ${esc(email)}</span></p>`
        : "";
      const scopeBadge = isAdminRow ? `<span class="documents-badge">${scope}</span>` : "";
      return `
        <article class="documents-record">
          <div class="documents-record-icon" aria-hidden="true"><svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h8"/></svg></div>
          <div class="documents-record-body">
            <strong class="documents-record-title" title="${esc(doc.filename || "Document")}">${esc(doc.filename || "Document")}</strong>
            <div class="documents-record-badges">${scopeBadge}<span class="documents-state-badge ${status === "indexed" ? "is-indexed" : "needs-attention"}">${indexStateLabel(doc)}</span></div>
            ${meta}
            <p class="documents-record-meta">${sizeKb} KB <span>·</span> ${esc(formatDate(doc.created_at))} <span>·</span> ${chunks} searchable chunk${chunks === 1 ? "" : "s"}</p>
          </div>
          <div class="documents-record-actions">
            ${isAdminRow ? "" : `<button class="documents-action-button" type="button" data-dm-action="reindex" data-kind="${esc(kind)}" data-id="${esc(doc.id)}">Re-index</button>`}
            <button class="documents-action-button danger" type="button" data-dm-action="${deleteAction}" data-kind="${esc(doc.kind || kind)}" data-id="${esc(doc.id)}" data-filename="${esc(doc.filename || "Document")}" data-uploader="${esc(uploader)}">Delete</button>
          </div>
        </article>`;
    }).join("");
  }

  function renderMyDocuments() {
    const docs = filterDocuments(state.personal, "dmMineSearch", "dmMineStatus");
    renderDocumentRows("dmMineList", docs, "personal");
    setStatus("dmMineStatusLine", `${state.personal.length} private document${state.personal.length === 1 ? "" : "s"} · ${state.personal.filter((doc) => indexState(doc) === "indexed").length} indexed`);
  }

  function renderCompanyDocuments() {
    const docs = filterDocuments(state.company, "dmCompanySearch", "dmCompanyStatus");
    renderDocumentRows("dmCompanyList", docs, "company");
    setStatus("dmCompanyStatusLine", `${state.company.length} company document${state.company.length === 1 ? "" : "s"} · ${state.company.filter((doc) => indexState(doc) === "indexed").length} indexed`);
  }

  function renderAllDocuments() {
    const docs = filterDocuments(state.all, "dmAllSearch", "dmAllStatus", true);
    renderDocumentRows("dmAllList", docs, "", true);
    setStatus("dmAllStatusLine", `${state.all.length} documents across all users · ${state.all.filter((doc) => indexState(doc) === "indexed").length} indexed`);
  }

  function renderPolicyWarnings(result) {
    const warnings = Array.isArray(result?.conflicts) ? result.conflicts : [];
    $("dmPolicyWarnings").innerHTML = warnings.length
      ? warnings.map((item) => `<article class="documents-policy-warning"><strong>Potential version conflict · ${esc(item.policy_family)}</strong><p>${esc(item.message || "Multiple versions are marked Active for this policy family.")}</p><p>${(item.documents || []).map((doc) => esc(`${doc.filename}${doc.version_label ? ` · ${doc.version_label}` : ""}`)).join(" · ")}</p></article>`).join("")
      : '<div class="documents-policy-clear"><strong>No lifecycle metadata conflicts detected.</strong><span>This checks document status metadata, not whether policy text is semantically consistent.</span></div>';
  }

  function renderPolicies(result) {
    const conflicts = Array.isArray(result?.conflicts) ? result.conflicts : [];
    renderPolicyWarnings(result);
    const documents = state.policies;
    if (!documents.length) {
      $("dmPolicyList").innerHTML = '<div class="documents-empty">No company documents are available for policy review. Upload a company policy first.</div>';
      return;
    }
    $("dmPolicyList").innerHTML = documents.map((doc) => {
      const savedStatus = ["draft", "active", "superseded"].includes(doc.policy_status) ? doc.policy_status : "draft";
      const needsReview = !["draft", "active", "superseded"].includes(doc.policy_status);
      const hasConflict = Boolean(doc.has_active_conflict || conflicts.some((conflict) => (conflict.documents || []).some((item) => item.id === doc.id)));
      const options = documents.filter((candidate) => candidate.id !== doc.id).map((candidate) => {
        const label = `${candidate.filename}${candidate.version_label ? ` · ${candidate.version_label}` : ""}`;
        return `<option value="${esc(candidate.id)}"${candidate.id === doc.supersedes_document_id ? " selected" : ""}>${esc(label)}</option>`;
      }).join("");
      return `
        <article class="documents-policy-card" data-policy-id="${esc(doc.id)}">
          <div class="documents-policy-card-head">
            <div class="documents-policy-file-icon" aria-hidden="true"><svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg></div>
            <div class="documents-policy-file-title"><div class="documents-record-badges"><span class="documents-state-badge ${needsReview ? "needs-attention" : `policy-${savedStatus}`} ">${needsReview ? "Needs review" : esc(savedStatus[0].toUpperCase() + savedStatus.slice(1))}</span>${hasConflict ? '<span class="documents-state-badge needs-attention">Potential conflict</span>' : ""}</div><strong>${esc(doc.filename || "Document")}</strong><p>${esc(doc.uploader_name || "Unknown uploader")} <span>·</span> ${Number(doc.chunks || 0)} searchable chunks</p></div>
          </div>
          <div class="documents-policy-fields">
            <label>Policy family<input data-policy-family type="text" maxlength="120" value="${esc(doc.policy_family || "")}" placeholder="e.g. Remote Work Policy" autocomplete="off" /></label>
            <label>Version<input data-policy-version type="text" maxlength="40" value="${esc(doc.version_label || "")}" placeholder="e.g. v2.1" autocomplete="off" /></label>
            <label>Status<select data-policy-status>${["draft", "active", "superseded"].map((value) => `<option value="${value}"${value === savedStatus ? " selected" : ""}>${value[0].toUpperCase() + value.slice(1)}</option>`).join("")}</select></label>
            <label>Effective date<input data-policy-date type="date" value="${esc(doc.effective_date || "")}" /></label>
            <label class="documents-policy-supersedes">Supersedes previous version<select data-policy-supersedes><option value="">No previous version selected</option>${options}</select></label>
          </div>
          ${needsReview ? '<p class="documents-policy-help">This legacy document has not been reviewed. Choose Draft, Active, or Superseded before saving its lifecycle metadata.</p>' : ""}
          <div class="documents-policy-card-footer"><p>Saving updates metadata and re-indexes the company document so retrieval can apply its lifecycle status.</p><button class="documents-primary-button" type="button" data-dm-action="save-policy">Save &amp; re-index</button></div>
        </article>`;
    }).join("");
  }

  async function refreshLibraries({quiet = false} = {}) {
    if (!quiet) setStatus("dmGlobalStatus", "Refreshing document libraries…");
    const results = await Promise.allSettled([
      api("/api/documents/personal"),
      isAdmin() ? api("/api/documents/company") : Promise.resolve({documents: []}),
      isAdmin() ? api("/api/admin/documents") : Promise.resolve({documents: []}),
      isAdmin() ? api("/api/admin/policy-documents") : Promise.resolve({documents: [], conflicts: []}),
    ]);
    const [personal, company, all, policies] = results;
    state.personal = personal.status === "fulfilled" ? (personal.value.documents || []) : [];
    state.company = company.status === "fulfilled" ? (company.value.documents || []) : [];
    state.all = all.status === "fulfilled" ? (all.value.documents || []) : [];
    const policyResult = policies.status === "fulfilled" ? policies.value : {documents: [], conflicts: []};
    state.policies = policyResult.documents || [];
    renderMyDocuments();
    if (isAdmin()) {
      renderCompanyDocuments();
      renderAllDocuments();
      renderPolicies(policyResult);
    }
    updateStats();

    const errors = results.filter((result) => result.status === "rejected");
    setStatus("dmGlobalStatus", errors.length
      ? "Some library information could not be loaded. Use Refresh on the affected section, or sign in again if your session expired."
      : "Libraries are up to date. Search, re-index, delete, or review policy metadata below.", Boolean(errors.length));
  }

  async function refreshOne(view) {
    const buttonId = {mine: "dmRefreshMine", company: "dmRefreshCompany", all: "dmRefreshAll", policies: "dmRefreshPolicies"}[view];
    const button = $(buttonId);
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Refreshing…";
    try { await refreshLibraries({quiet: true}); setStatus("dmGlobalStatus", "Document data refreshed."); }
    catch (error) { setStatus("dmGlobalStatus", error.message || "Refresh failed.", true); }
    finally { button.disabled = false; button.textContent = original; }
  }

  function switchView(view) {
    if (["company", "all", "policies"].includes(view) && !isAdmin()) return;
    state.view = view;
    document.querySelectorAll("[data-dm-view]").forEach((button) => {
      const active = button.dataset.dmView === view;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    document.querySelectorAll("[data-dm-panel]").forEach((panel) => {
      panel.classList.toggle("hidden", panel.dataset.dmPanel !== view);
    });
  }

  async function performDocumentAction(button) {
    const action = button.dataset.dmAction;
    const id = button.dataset.id || button.closest("[data-policy-id]")?.dataset.policyId;
    if (action === "save-policy") return savePolicy(button);
    const kind = button.dataset.kind;
    const filename = button.dataset.filename || button.closest(".documents-record")?.querySelector(".documents-record-title")?.textContent || "this document";
    if (!id || !["personal", "company"].includes(kind)) return;
    if (action === "delete" || action === "admin-delete") {
      const uploader = button.dataset.uploader ? ` uploaded by ${button.dataset.uploader}` : "";
      if (!window.confirm(`Permanently delete "${filename}"${uploader}? This removes the stored file, indexed vectors and document record. This action cannot be undone.`)) return;
    } else if (action === "reindex") {
      if (!window.confirm(`Re-index "${filename}"? Its current searchable chunks will be replaced.`)) return;
    } else return;

    const previous = button.textContent;
    button.disabled = true;
    button.textContent = action === "reindex" ? "Re-indexing…" : "Deleting…";
    try {
      if (action === "reindex") {
        await api(`/api/documents/${kind}/${encodeURIComponent(id)}/reindex`, {method: "POST"});
      } else if (action === "admin-delete") {
        if (!isAdmin()) throw new Error("Administrator access is required.");
        await api(`/api/admin/documents/${kind}/${encodeURIComponent(id)}`, {method: "DELETE"});
      } else {
        await api(`/api/documents/${kind}/${encodeURIComponent(id)}`, {method: "DELETE"});
      }
      await refreshLibraries({quiet: true});
      setStatus("dmGlobalStatus", action === "reindex" ? `Re-indexed ${filename}.` : `Deleted ${filename}.`);
    } catch (error) {
      setStatus("dmGlobalStatus", error.message || "The document action failed.", true);
    } finally {
      button.disabled = false;
      button.textContent = previous;
    }
  }

  async function savePolicy(button) {
    if (!isAdmin()) return;
    const card = button.closest("[data-policy-id]");
    const id = card?.dataset.policyId;
    if (!id) return;
    const payload = {
      policy_family: card.querySelector("[data-policy-family]")?.value.trim() || "",
      version_label: card.querySelector("[data-policy-version]")?.value.trim() || "",
      policy_status: card.querySelector("[data-policy-status]")?.value || "draft",
      effective_date: card.querySelector("[data-policy-date]")?.value || "",
      supersedes_document_id: card.querySelector("[data-policy-supersedes]")?.value || null,
    };
    if (payload.policy_status === "active" && (!payload.policy_family || !payload.version_label || !payload.effective_date)) {
      setStatus("dmPolicyStatusLine", "Active policies require a policy family, version label, and effective date.", true);
      card.querySelector("[data-policy-family]")?.focus();
      return;
    }
    if (payload.supersedes_document_id && payload.policy_status !== "active") {
      setStatus("dmPolicyStatusLine", "Choose Active when setting a document to supersede a previous version.", true);
      return;
    }
    button.disabled = true;
    const previous = button.textContent;
    button.textContent = "Saving…";
    setStatus("dmPolicyStatusLine", "Saving lifecycle metadata and re-indexing the policy…");
    try {
      const result = await api(`/api/admin/policy-documents/${encodeURIComponent(id)}`, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      await refreshLibraries({quiet: true});
      setStatus("dmPolicyStatusLine", result.message || "Policy metadata saved and the document re-indexed.");
    } catch (error) {
      setStatus("dmPolicyStatusLine", error.message || "Could not save policy metadata.", true);
    } finally {
      button.disabled = false;
      button.textContent = previous;
    }
  }

  function wireEvents() {
    document.querySelectorAll("[data-dm-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.dmView)));
    $("dmMineSearch").addEventListener("input", renderMyDocuments);
    $("dmMineStatus").addEventListener("change", renderMyDocuments);
    $("dmCompanySearch").addEventListener("input", renderCompanyDocuments);
    $("dmCompanyStatus").addEventListener("change", renderCompanyDocuments);
    $("dmAllSearch").addEventListener("input", renderAllDocuments);
    $("dmAllStatus").addEventListener("change", renderAllDocuments);
    $("dmRefreshMine").addEventListener("click", () => refreshOne("mine"));
    $("dmRefreshCompany").addEventListener("click", () => refreshOne("company"));
    $("dmRefreshAll").addEventListener("click", () => refreshOne("all"));
    $("dmRefreshPolicies").addEventListener("click", () => refreshOne("policies"));
    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-dm-action]");
      if (button) void performDocumentAction(button);
    });
  }

  async function initialize() {
    try {
      const result = await api("/api/auth/me");
      if (!result.user || result.user.role === "guest") {
        window.location.replace("/?next=/documents");
        return;
      }
      state.user = result.user;
      $("documentsRole").textContent = state.user.role === "admin" ? "Administrator workspace" : "Employee workspace";
      const admin = isAdmin();
      const qualityDashboardLink = $("answerQualityDashboardLink");
      if (qualityDashboardLink) qualityDashboardLink.classList.toggle("hidden", !admin);
      ["company", "all", "policies"].forEach((view) => {
        document.querySelector(`[data-dm-view="${view}"]`)?.classList.toggle("hidden", !admin);
      });
      $("dmPolicyFeature").classList.toggle("hidden", !admin);
      $("documentsQualityLink")?.classList.toggle("hidden", !admin);
      if (!admin) {
        $("dmViewCompany").classList.add("hidden");
        $("dmViewAll").classList.add("hidden");
        $("dmViewPolicies").classList.add("hidden");
      }
      wireEvents();
      await refreshLibraries();
    } catch (error) {
      if (!window.location.pathname.startsWith("/")) return;
      $("documentsRole").textContent = "Sign-in required";
      setStatus("dmGlobalStatus", error.message || "Please sign in to access document management.", true);
      const existingLink = document.createElement("a");
      existingLink.href = "/?next=/documents";
      existingLink.className = "documents-primary-link";
      existingLink.textContent = "Sign in to continue";
      document.querySelector(".documents-top-actions")?.appendChild(existingLink);
    }
  }

  initialize();

  /* HRMIND_AI_SINGLE_QUALITY_NAV_V1: keep exactly one quality link even if older code injects one. */
  function enforceSingleAnswerQualityLink() {
    const nav = document.querySelector(".documents-top-actions");
    if (!nav) return;
    const candidates = Array.from(nav.querySelectorAll("a")).filter((link) => {
      let path = "";
      try { path = new URL(link.getAttribute("href") || "", window.location.origin).pathname.replace(/\/+$/, ""); }
      catch (_) { /* Ignore malformed, unrelated links. */ }
      return path === "/quality" || String(link.textContent || "").trim().toLowerCase() === "answer quality";
    });
    if (!candidates.length) return;
    const keep = candidates.find((link) => link.id === "answerQualityDashboardLink") || candidates[0];
    keep.id = "answerQualityDashboardLink";
    if (keep.getAttribute("href") !== "/quality") keep.setAttribute("href", "/quality");
    if (String(keep.textContent || "").trim() !== "Answer Quality") keep.textContent = "Answer Quality";
    for (const link of candidates) if (link !== keep) link.remove();
  }
  enforceSingleAnswerQualityLink();
  const qualityNav = document.querySelector(".documents-top-actions");
  if (qualityNav && window.MutationObserver) {
    const qualityNavObserver = new MutationObserver(enforceSingleAnswerQualityLink);
    qualityNavObserver.observe(qualityNav, { childList: true, subtree: true });
  }
})();
