const chat = document.getElementById("chat");
const form = document.getElementById("chatForm");
const question = document.getElementById("question");
const trace = document.getElementById("trace");
const sourceUsed = document.getElementById("sourceUsed");
const submitButton = form.querySelector('button[type="submit"]');
const stopGenerationBtn = document.getElementById("stopGenerationBtn");

const historyModal = document.getElementById("historyModal");
const historyList = document.getElementById("historyList");
const historyStatus = document.getElementById("historyStatus");
const historyToggle = document.getElementById("historyToggle");
const newChatBtn = document.getElementById("newChatBtn");
const historyNewChat = document.getElementById("historyNewChat");
const closeHistory = document.getElementById("closeHistory");
const refreshHistoryBtn = document.getElementById("refreshHistory");

const conversationActionModal = document.getElementById(
  "conversationActionModal",
);
const conversationActionTitle = document.getElementById(
  "conversationActionTitle",
);
const conversationActionDescription = document.getElementById(
  "conversationActionDescription",
);
const conversationActionForm = document.getElementById(
  "conversationActionForm",
);
const conversationRenameFields = document.getElementById(
  "conversationRenameFields",
);
const conversationActionInput = document.getElementById(
  "conversationActionInput",
);
const conversationActionError = document.getElementById(
  "conversationActionError",
);
const confirmConversationAction = document.getElementById(
  "confirmConversationAction",
);
const closeConversationAction = document.getElementById(
  "closeConversationAction",
);
const cancelConversationAction = document.getElementById(
  "cancelConversationAction",
);

const uploadModal = document.getElementById("uploadModal");
const fileInput = document.getElementById("fileInput");
const fileLabel = document.getElementById("fileLabel");
const uploadButton = document.getElementById("uploadBtn");
const uploadScope = document.getElementById("uploadScope");
const uploadStatus = document.getElementById("uploadStatus");
const personalDocumentsList = document.getElementById("personalDocumentsList");
const companyDocumentsList = document.getElementById("companyDocumentsList");
const personalDocsPanel = document.getElementById("personalDocsPanel");
const companyDocsPanel = document.getElementById("companyDocsPanel");

const conversationToolbar = document.getElementById("conversationToolbar");
const exportConversationBtn = document.getElementById("exportConversationBtn");
const shareConversationBtn = document.getElementById("shareConversationBtn");

const shareModal = document.getElementById("shareModal");
const closeShareModal = document.getElementById("closeShareModal");
const cancelShareModal = document.getElementById("cancelShareModal");
const createShareLinkBtn = document.getElementById("createShareLinkBtn");
const copyShareLinkBtn = document.getElementById("copyShareLinkBtn");
const shareLinkInput = document.getElementById("shareLinkInput");
const shareLinkPanel = document.getElementById("shareLinkPanel");
const shareLinkExpiry = document.getElementById("shareLinkExpiry");
const shareStatus = document.getElementById("shareStatus");
const refreshShareLinksBtn = document.getElementById("refreshShareLinksBtn");
const shareLinksList = document.getElementById("shareLinksList");

let shareModalConversationId = null;
let nextMessageRenderId = 0;

let requestInProgress = false;
let activeGeneration = null;
let currentConversationId = null;
let currentConversationHasMessages = false;
let conversations = [];
let pendingConversationAction = null;
let actionSaving = false;
let uploadInProgress = false;

const initialWelcomeMarkup = chat.innerHTML;

const SOURCE_LABELS = {
  private_kb: "Company & Personal Knowledge Base",
  web_search: "External Web Search",
  web: "External Web Search",
  direct: "Direct Response",
  insufficient_evidence: "Insufficient Evidence",
  running: "Running…",
  error: "Error",
};

const TRACE_NODE_LABELS = {
  route_question: "Route question",
  retrieve_kb: "Retrieve company and personal knowledge",
  grade_kb: "Evaluate private KB evidence",
  search_web: "Search public web with Tavily",
  grade_web: "Evaluate web evidence",
  rewrite_query: "Rewrite query and retry",
  generate_from_kb: "Generate answer from knowledge base",
  generate_from_web: "Generate answer from web sources",
  direct_answer: "Generate direct response",
  insufficient: "Stop: insufficient evidence",
};

function escapeHtml(value = "") {
  return String(value).replace(
    /[&<>\'"]/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "'": "&#39;",
        '"': "&quot;",
      })[character],
  );
}

function formatSource(value = "") {
  const key = String(value || "")
    .trim()
    .toLowerCase();
  return key
    ? SOURCE_LABELS[key] || String(value).replace(/_/g, " ")
    : "Unknown";
}

function formatTraceNode(value = "") {
  const key = String(value || "").trim();
  return TRACE_NODE_LABELS[key] || key.replace(/_/g, " ");
}

function safeExternalUrl(value) {
  if (typeof value !== "string" || !value.trim()) return "";

  try {
    const url = new URL(value, window.location.origin);
    return ["https:", "http:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function cleanCitationTitle(citation = {}) {
  const title = String(citation.title || citation.url || "Source").trim();
  const localPath =
    citation.type === "private_kb" ||
    /^[a-z]:[\\/]/i.test(title) ||
    title.startsWith("/") ||
    title.includes("\\");

  return localPath
    ? title.replace(/\\/g, "/").split("/").filter(Boolean).pop() ||
        "Company document"
    : title;
}

function formatText(value = "") {
  const text = String(value).replace(/【[^】]*】/g, "");

  if (!window.marked || !window.DOMPurify) {
    return escapeHtml(text).replace(/\n/g, "<br>");
  }

  return DOMPurify.sanitize(marked.parse(text));
}

async function apiRequest(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers: {
      ...(options.headers || {}),
    },
  });

  let data = {};

  try {
    data = await response.json();
  } catch {
    // The response may not contain JSON.
  }

  if (!response.ok) {
    throw new Error(data.detail || `Request failed (${response.status}).`);
  }

  return data;
}

// ---------------------------------------------------------
// Conversation export and sharing
// ---------------------------------------------------------

function updateConversationToolbar() {
  if (!conversationToolbar) return;

  const isGuest = window.hrmindCurrentUser?.role === "guest";
  const shouldShow =
    Boolean(currentConversationId) &&
    currentConversationHasMessages &&
    !isGuest;

  conversationToolbar.classList.toggle("hidden", !shouldShow);

  [exportConversationBtn, shareConversationBtn].forEach((button) => {
    if (button) button.disabled = requestInProgress || !shouldShow;
  });
}

function setShareStatus(message = "", isError = false) {
  if (!shareStatus) return;
  shareStatus.textContent = message;
  shareStatus.classList.toggle("error", isError);
}

function openShareModal() {
  if (
    !shareModal ||
    !shareLinkInput ||
    !shareLinkExpiry ||
    !shareLinkPanel ||
    !currentConversationId ||
    window.hrmindCurrentUser?.role === "guest"
  ) {
    return;
  }

  shareModalConversationId = currentConversationId;
  shareLinkInput.value = "";
  shareLinkExpiry.textContent = "";
  shareLinkPanel.classList.add("hidden");

  setShareStatus("Create a link to share a read-only snapshot of this chat.");
  if (shareLinksList) {
    shareLinksList.innerHTML = "";
  }
  shareModal.classList.remove("hidden");
}

function closeShareDialog() {
  shareModal?.classList.add("hidden");
  shareModalConversationId = null;
}

async function exportCurrentConversation() {
  if (!exportConversationBtn || !currentConversationId || requestInProgress)
    return;

  const conversationId = currentConversationId;
  const originalText = exportConversationBtn.textContent;

  exportConversationBtn.disabled = true;
  exportConversationBtn.textContent = "Exporting…";

  try {
    const response = await fetch(
      `/api/conversations/${encodeURIComponent(conversationId)}/export`,
      {
        credentials: "same-origin",
        cache: "no-store",
      },
    );

    if (!response.ok) {
      let detail = `Export failed (${response.status}).`;

      try {
        const payload = await response.json();
        detail = payload.detail || detail;
      } catch {
        // Keep the generic error.
      }

      throw new Error(detail);
    }

    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename\s*=\s*"?([^";]+)"?/i);
    const filename = match?.[1] || "hr-mind-ai-conversation.md";

    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");

    link.href = objectUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();

    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  } catch (error) {
    window.alert(error.message || "Could not export this conversation.");
  } finally {
    exportConversationBtn.textContent = originalText;
    updateConversationToolbar();
  }
}

async function createConversationShare() {
  const conversationId = shareModalConversationId;

  if (
    !createShareLinkBtn ||
    !shareLinkInput ||
    !shareLinkPanel ||
    !conversationId
  ) {
    return;
  }

  createShareLinkBtn.disabled = true;
  createShareLinkBtn.textContent = "Creating link…";
  setShareStatus("Creating a read-only snapshot…");

  try {
    const data = await apiRequest(
      `/api/conversations/${encodeURIComponent(conversationId)}/share`,
      { method: "POST" },
    );

    const shareUrl = new URL(data.path, window.location.origin).href;
    shareLinkInput.value = shareUrl;
    shareLinkPanel.classList.remove("hidden");

    shareLinkExpiry.textContent =
      "This is a read-only snapshot of the chat when the link was created. It expires after 7 days or sooner if replaced by a newer link.";

    setShareStatus("New share link created.");
    await refreshShareLinks();
  } catch (error) {
    setShareStatus(error.message || "Could not create a share link.", true);
  } finally {
    createShareLinkBtn.disabled = false;
    createShareLinkBtn.textContent = "Create share link";
  }
}

async function copyShareLink() {
  if (!shareLinkInput) return;

  const value = shareLinkInput.value.trim();

  if (!value) {
    setShareStatus("Create a share link first.", true);
    return;
  }

  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
    } else {
      shareLinkInput.focus();
      shareLinkInput.select();

      if (!document.execCommand("copy")) {
        throw new Error("Select the link and copy it manually.");
      }
    }

    setShareStatus("Link copied to clipboard.");
  } catch (error) {
    shareLinkInput.focus();
    shareLinkInput.select();
    setShareStatus(
      error.message || "Select the link and copy it manually.",
      true,
    );
  }
}

async function refreshShareLinks() {
  const conversationId = shareModalConversationId;
  if (!conversationId || !shareLinksList) return;

  shareLinksList.innerHTML = '<div class="empty">Loading links…</div>';

  try {
    const data = await apiRequest(
      `/api/conversations/${encodeURIComponent(conversationId)}/shares`,
    );

    const shares = Array.isArray(data.shares) ? data.shares : [];

    if (!shares.length) {
      shareLinksList.innerHTML =
        '<div class="empty">No share links created for this conversation.</div>';
      return;
    }

    shareLinksList.innerHTML = shares
      .map((share) => {
        const expiresMs = Date.parse(share.expires_at || "");
        const isExpired = Number.isFinite(expiresMs) && expiresMs <= Date.now();
        const isRevoked = Boolean(share.revoked_at);
        const status = isRevoked ? "Revoked" : isExpired ? "Expired" : "Active";
        const canRevoke = !isRevoked && !isExpired;
        const id = escapeHtml(String(share.id || ""));

        return `
          <div class="share-link-item">
            <div class="share-link-details">
              <strong>${status}</strong>
              <span>Created ${escapeHtml(formatDate(share.created_at) || "unknown")}</span>
              <span>Expires ${escapeHtml(formatDate(share.expires_at) || "unknown")}</span>
            </div>
            ${
              canRevoke
                ? `<button class="history-action delete" type="button" data-share-revoke="${id}">Revoke</button>`
                : ""
            }
          </div>
        `;
      })
      .join("");
  } catch (error) {
    shareLinksList.innerHTML =
      '<div class="empty">Could not load share links.</div>';
    setShareStatus(error.message || "Could not load share links.", true);
  }
}

shareLinksList?.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-share-revoke]");
  if (!button || !shareModalConversationId) return;

  const shareId = button.dataset.shareRevoke;

  if (
    !shareId ||
    !window.confirm("Revoke this share link? Anyone using it will lose access.")
  ) {
    return;
  }

  button.disabled = true;

  try {
    await apiRequest(
      `/api/conversations/${encodeURIComponent(shareModalConversationId)}/shares/${encodeURIComponent(shareId)}`,
      { method: "DELETE" },
    );

    setShareStatus("Share link revoked.");
    await refreshShareLinks();
  } catch (error) {
    setShareStatus(error.message || "Could not revoke share link.", true);
    button.disabled = false;
  }
});

exportConversationBtn?.addEventListener("click", exportCurrentConversation);
shareConversationBtn?.addEventListener("click", openShareModal);
closeShareModal?.addEventListener("click", closeShareDialog);
cancelShareModal?.addEventListener("click", closeShareDialog);
createShareLinkBtn?.addEventListener("click", createConversationShare);
copyShareLinkBtn?.addEventListener("click", copyShareLink);

refreshShareLinksBtn?.addEventListener("click", () => {
  refreshShareLinks().catch(() => {});
});

shareModal?.addEventListener("click", (event) => {
  if (event.target === shareModal) closeShareDialog();
});

// ---------------------------------------------------------
// Messages, citations and scrolling
// ---------------------------------------------------------

function scrollLatestIntoView(element = chat) {
  const styles = window.getComputedStyle(chat);
  const hasInternalScroll =
    styles.overflowY !== "visible" && chat.scrollHeight > chat.clientHeight + 2;

  if (hasInternalScroll) {
    chat.scrollTo({ top: chat.scrollHeight, behavior: "smooth" });
    return;
  }

  const target = element === chat ? chat.lastElementChild : element;
  target?.scrollIntoView({ behavior: "smooth", block: "end" });
}

function addMessage(role, text, source = "", citations = []) {
  const wrap = document.createElement("div");
  const messageKey = ++nextMessageRenderId;

  wrap.className = `message ${role === "user" ? "user" : "assistant"}`;

  const items = Array.isArray(citations) ? citations : [];
  const citationMap = new Map();

  items.forEach((citation, index) => {
    const suppliedId = Number(citation?.id);
    const citationNumber =
      Number.isInteger(suppliedId) && suppliedId > 0 ? suppliedId : index + 1;

    if (!citationMap.has(citationNumber)) {
      citationMap.set(citationNumber, citation || {});
    }
  });

  let displayText = String(text ?? "");

  if (role !== "user" && citationMap.size) {
    displayText = displayText.replace(
      /\[(\d{1,3})\](?!\()/g,
      (match, rawId) => {
        const citationNumber = Number(rawId);
        if (!citationMap.has(citationNumber)) return match;

        return `[${citationNumber}](#citation-source-${messageKey}-${citationNumber} "Jump to source ${citationNumber}")`;
      },
    );
  }

  const citationsHtml = items.length
    ? `
      <div class="citations">
        <strong>Sources</strong>
        <div class="source-list">
          ${items
            .map((citation, index) => {
              const item = citation || {};
              const suppliedId = Number(item.id);
              const citationNumber =
                Number.isInteger(suppliedId) && suppliedId > 0
                  ? suppliedId
                  : index + 1;
              const title = escapeHtml(cleanCitationTitle(item));
              const url = safeExternalUrl(item.url);

              const page =
                item.page !== undefined &&
                item.page !== null &&
                String(item.page) !== ""
                  ? `<span class="source-page">Page ${escapeHtml(String(item.page))}</span>`
                  : "";

              const excerpt = item.excerpt
                ? `<p class="source-excerpt">${escapeHtml(String(item.excerpt))}</p>`
                : "";

              const safeTitle = url
                ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer nofollow">${title}</a>`
                : `<span class="source-title">${title}</span>`;

              return `
              <article
                class="source-card"
                id="citation-source-${messageKey}-${citationNumber}"
                data-citation-number="${citationNumber}"
              >
                <span class="citation-number">[${citationNumber}]</span>
                <span class="source-icon" aria-hidden="true">${url ? "↗" : "▤"}</span>
                <div class="source-card-body">
                  ${safeTitle}
                  ${page}
                  ${excerpt}
                </div>
              </article>
            `;
            })
            .join("")}
        </div>
      </div>
    `
    : "";

  const body =
    role === "user"
      ? escapeHtml(displayText).replace(/\n/g, "<br>")
      : formatText(displayText);

  const sourceHtml = source
    ? `<div class="answer-source">Source: ${escapeHtml(formatSource(source))}</div>`
    : "";

  wrap.innerHTML = `
    <div class="avatar" aria-hidden="true">${role === "user" ? "You" : "AI"}</div>
    <div class="bubble">
      ${body}
      ${sourceHtml}
      ${citationsHtml}
    </div>
  `;

  chat.appendChild(wrap);
  scrollLatestIntoView(wrap);
  return wrap;
}

function renderTrace(items = [], running = false) {
  const safeItems = Array.isArray(items) ? items : [];

  trace.innerHTML = safeItems.length
    ? safeItems
        .map((item, index) => {
          const active = running && index === safeItems.length - 1;
          return `
          <div class="trace-item ${active ? "active" : "completed"}">
            <span class="trace-dot" aria-hidden="true">${active ? "•" : "✓"}</span>
            <span>${escapeHtml(String(item))}</span>
          </div>
        `;
        })
        .join("")
    : '<div class="empty">No request yet.</div>';
}

function setRequestBusy(isBusy) {
  requestInProgress = isBusy;
  question.disabled = isBusy;
  submitButton.disabled = isBusy;
  submitButton.textContent = "Ask Agent";
  submitButton.classList.toggle("hidden", isBusy);

  if (stopGenerationBtn) {
    const canStop = isBusy && Boolean(activeGeneration);

    stopGenerationBtn.classList.toggle("hidden", !canStop);
    stopGenerationBtn.disabled =
      !canStop || activeGeneration?.cancelled === true;

    stopGenerationBtn.textContent = activeGeneration?.cancelled
      ? "Stopping…"
      : "Stop";
  }

  form.setAttribute("aria-busy", String(isBusy));

  document.querySelectorAll(".example").forEach((button) => {
    button.disabled = isBusy;
  });

  newChatBtn.disabled = isBusy;
  historyNewChat.disabled = isBusy;

  updateConversationToolbar();
}

function createGenerationContext(kind) {
  const requestId =
    window.crypto?.randomUUID?.() ||
    `hrmind-${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;

  const endpoint = kind === "guest" ? "/api/guest/chat" : "/api/chat";

  const generation = {
    requestId,
    controller: new AbortController(),
    cancelUrl: `${endpoint}/${encodeURIComponent(requestId)}/cancel`,
    cancelled: false,
  };

  activeGeneration = generation;
  return generation;
}

function stopActiveGeneration() {
  const generation = activeGeneration;

  if (!generation || generation.cancelled) return;

  generation.cancelled = true;

  if (stopGenerationBtn) {
    stopGenerationBtn.disabled = true;
    stopGenerationBtn.textContent = "Stopping…";
  }

  // Ask the backend to cancel at a safe workflow boundary.
  fetch(generation.cancelUrl, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
  }).catch(() => {});

  // Stop waiting for and rendering the current response immediately.
  generation.controller.abort();
}

function showGenerationStopped() {
  hideTypingIndicator();

  addMessage("assistant", "Generation stopped.");

  renderTrace(["Generation stopped by user"], false);
  sourceUsed.textContent = "Stopped";
}

stopGenerationBtn?.addEventListener("click", stopActiveGeneration);

function setHistoryStatus(message = "", isError = false) {
  historyStatus.textContent = message;
  historyStatus.classList.toggle("error", isError);
}

function resetChatUI() {
  currentConversationHasMessages = false;
  chat.innerHTML = initialWelcomeMarkup;
  document.getElementById("chatExamples")?.classList.add("hidden");

  renderTrace([], false);
  sourceUsed.textContent = "—";
  question.value = "";
  question.disabled = false;
  submitButton.disabled = false;

  updateConversationToolbar();
  setTimeout(() => question.focus(), 0);
}

function closeHistoryModal() {
  historyModal.classList.add("hidden");
}

async function openHistoryModal() {
  historyModal.classList.remove("hidden");

  try {
    await refreshHistory();
  } catch {
    // Error details are shown in the dialog.
  }
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";

  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------
// Conversation history
// ---------------------------------------------------------

async function refreshHistory(showStatus = true) {
  if (showStatus) setHistoryStatus("Loading conversations…");

  try {
    const data = await apiRequest("/api/conversations");
    conversations = Array.isArray(data.conversations) ? data.conversations : [];
    renderHistory();

    setHistoryStatus(
      conversations.length
        ? `${conversations.length} saved conversation${conversations.length === 1 ? "" : "s"}`
        : "No saved conversations yet.",
    );

    return conversations;
  } catch (error) {
    if (showStatus) setHistoryStatus(error.message, true);
    historyList.innerHTML =
      '<div class="empty">Could not load conversation history.</div>';
    throw error;
  }
}

function renderHistory() {
  if (!conversations.length) {
    historyList.innerHTML =
      '<div class="empty">No saved conversations yet. Start a new chat to begin.</div>';
    return;
  }

  historyList.innerHTML = conversations
    .map((item) => {
      const id = escapeHtml(item.id);
      const title = escapeHtml(item.title || "New chat");
      const count = Number(item.message_count || 0);
      const updated = formatDate(item.updated_at);

      return `
      <div class="history-item ${item.id === currentConversationId ? "current" : ""}">
        <button type="button" class="history-open" data-history-action="open" data-id="${id}" title="Open ${title}">
          <span class="history-title">${title}</span>
          <span class="history-meta">
            ${count} message${count === 1 ? "" : "s"}${updated ? ` · ${escapeHtml(updated)}` : ""}
          </span>
        </button>
        <div class="history-actions">
          <button type="button" class="history-action" data-history-action="rename" data-id="${id}">Rename</button>
          <button type="button" class="history-action delete" data-history-action="delete" data-id="${id}">Delete</button>
        </div>
      </div>
    `;
    })
    .join("");
}

async function createNewChat() {
  if (requestInProgress) return;

  try {
    const data = await apiRequest("/api/conversations", { method: "POST" });

    if (!data.conversation?.id) {
      throw new Error("Server did not return a conversation ID.");
    }

    currentConversationId = data.conversation.id;
    currentConversationHasMessages = false;
    updateConversationToolbar();
    resetChatUI();
    closeHistoryModal();
    await refreshHistory(false);
  } catch (error) {
    addMessage(
      "assistant",
      `**Couldn't create a new conversation.** ${error.message}`,
    );
  }
}

async function openConversation(conversationId) {
  try {
    setHistoryStatus("Opening conversation…");

    const data = await apiRequest(
      `/api/conversations/${encodeURIComponent(conversationId)}`,
    );

    currentConversationId = data.conversation.id;

    const messages = Array.isArray(data.messages) ? data.messages : [];
    currentConversationHasMessages = messages.length > 0;
    updateConversationToolbar();

    chat.innerHTML = "";

    if (!messages.length) {
      resetChatUI();
    } else {
      messages.forEach((message) => {
        addMessage(
          message.role,
          message.content,
          message.source_used || "",
          message.citations || [],
        );
      });

      document.getElementById("chatExamples")?.classList.remove("hidden");

      const lastAssistant = [...messages]
        .reverse()
        .find((message) => message.role === "assistant");

      if (lastAssistant) {
        renderTrace(lastAssistant.trace || [], false);
        sourceUsed.textContent = formatSource(lastAssistant.source_used || "");
      } else {
        renderTrace([], false);
        sourceUsed.textContent = "—";
      }
    }

    closeHistoryModal();
    await refreshHistory(false);
    scrollLatestIntoView();
  } catch (error) {
    setHistoryStatus(error.message, true);
  }
}

function openConversationAction(type, conversationId) {
  const item = conversations.find((row) => row.id === conversationId);
  if (!item || actionSaving) return;

  const isRename = type === "rename";
  pendingConversationAction = { type, conversationId };

  conversationActionTitle.textContent = isRename
    ? "Rename conversation"
    : "Delete conversation?";
  conversationActionDescription.textContent = isRename
    ? "Choose a name that's easy to recognize later."
    : `Delete “${item.title || "New chat"}” and all its saved messages? This cannot be undone.`;

  conversationRenameFields.hidden = !isRename;
  conversationActionInput.value = isRename ? item.title || "" : "";
  conversationActionError.textContent = "";

  confirmConversationAction.textContent = isRename
    ? "Save changes"
    : "Delete conversation";
  confirmConversationAction.classList.toggle("danger", !isRename);
  conversationActionModal.classList.remove("hidden");

  setTimeout(() => {
    if (isRename) {
      conversationActionInput.focus();
      conversationActionInput.select();
    } else {
      confirmConversationAction.focus();
    }
  }, 0);
}

function closeConversationActionDialog() {
  if (actionSaving) return;
  conversationActionModal.classList.add("hidden");
  pendingConversationAction = null;
  conversationActionError.textContent = "";
}

historyList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-history-action]");
  if (!button) return;

  const action = button.dataset.historyAction;
  const id = button.dataset.id;
  if (!id) return;

  if (action === "open") {
    await openConversation(id);
  } else if (action === "rename" || action === "delete") {
    openConversationAction(action, id);
  }
});

conversationActionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!pendingConversationAction || actionSaving) return;

  const { type, conversationId } = pendingConversationAction;
  const isRename = type === "rename";
  const title = conversationActionInput.value.trim();

  if (isRename && !title) {
    conversationActionError.textContent = "Please enter a conversation name.";
    conversationActionInput.focus();
    return;
  }

  if (isRename && title.length > 80) {
    conversationActionError.textContent =
      "The name must be 80 characters or fewer.";
    return;
  }

  actionSaving = true;
  confirmConversationAction.disabled = true;
  cancelConversationAction.disabled = true;
  closeConversationAction.disabled = true;
  confirmConversationAction.textContent = isRename ? "Saving…" : "Deleting…";
  conversationActionError.textContent = "";

  try {
    const encoded = encodeURIComponent(conversationId);

    if (isRename) {
      await apiRequest(`/api/conversations/${encoded}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
    } else {
      await apiRequest(`/api/conversations/${encoded}`, { method: "DELETE" });

      if (currentConversationId === conversationId) {
        currentConversationId = null;
        updateConversationToolbar();
        resetChatUI();
      }
    }

    conversationActionModal.classList.add("hidden");
    pendingConversationAction = null;
    await refreshHistory();

    setHistoryStatus(
      isRename
        ? "Conversation renamed successfully."
        : "Conversation deleted successfully.",
    );
  } catch (error) {
    conversationActionError.textContent =
      error.message || "Something went wrong. Please try again.";
  } finally {
    actionSaving = false;
    confirmConversationAction.disabled = false;
    cancelConversationAction.disabled = false;
    closeConversationAction.disabled = false;
    confirmConversationAction.textContent =
      pendingConversationAction?.type === "delete"
        ? "Delete conversation"
        : "Save changes";
  }
});

closeConversationAction.addEventListener(
  "click",
  closeConversationActionDialog,
);
cancelConversationAction.addEventListener(
  "click",
  closeConversationActionDialog,
);

conversationActionModal.addEventListener("click", (event) => {
  if (event.target === conversationActionModal) closeConversationActionDialog();
});

historyToggle.addEventListener("click", openHistoryModal);
newChatBtn.addEventListener("click", createNewChat);
historyNewChat.addEventListener("click", createNewChat);
closeHistory.addEventListener("click", closeHistoryModal);

refreshHistoryBtn.addEventListener("click", () => {
  refreshHistory().catch(() => {});
});

historyModal.addEventListener("click", (event) => {
  if (event.target === historyModal) closeHistoryModal();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeConversationActionDialog();
    closeHistoryModal();
    closeShareDialog();
    uploadModal.classList.add("hidden");
  }
});

// ---------------------------------------------------------
// Streaming chat
// ---------------------------------------------------------

async function askGuestAgent(userQuestion) {
  const generation = createGenerationContext("guest");
  setRequestBusy(true);

  try {
    document.getElementById("welcomeState")?.remove();
    document.getElementById("chatExamples")?.classList.add("hidden");

    addMessage("user", userQuestion);
    question.value = "";

    showTypingIndicator();
    renderTrace(["Starting temporary guest session…"], true);
    sourceUsed.textContent = "Public Web Search";

    const response = await fetch("/api/guest/chat", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      signal: generation.controller.signal,
      body: JSON.stringify({
        question: userQuestion,
        request_id: generation.requestId,
      }),
    });

    if (!response.ok) {
      let message = `Request failed (${response.status}).`;

      try {
        message = (await response.json()).detail || message;
      } catch {
        // Keep the generic error.
      }

      throw new Error(message);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    let buffer = "";
    let finalData = null;
    const streamedTrace = [];

    function handleGuestLine(line) {
      if (!line.trim()) return;

      const event = JSON.parse(line);

      if (event.type === "trace") {
        streamedTrace.push(
          `Guest → ${formatTraceNode(event.node || "Public search")}`,
        );

        renderTrace(streamedTrace, true);
      } else if (event.type === "final") {
        finalData = event;
      } else if (event.type === "cancelled") {
        generation.cancelled = true;
      } else if (event.type === "error") {
        throw new Error(event.detail || "Guest search could not complete.");
      }
    }

    try {
      while (true) {
        const { value, done } = await reader.read();

        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          handleGuestLine(line);
        }
      }

      buffer += decoder.decode();

      if (buffer.trim()) {
        handleGuestLine(buffer);
      }
    } finally {
      try {
        reader.releaseLock();
      } catch {
        // Reader already released.
      }
    }

    if (generation.cancelled) {
      showGenerationStopped();
      return;
    }

    if (!finalData) {
      throw new Error("No answer was returned. Please try again.");
    }

    hideTypingIndicator();

    addMessage(
      "assistant",
      finalData.answer || "No answer was returned.",
      finalData.source_used || "web_search",
      finalData.citations || [],
    );

    renderTrace(finalData.trace || streamedTrace, false);

    sourceUsed.textContent = formatSource(
      finalData.source_used || "web_search",
    );
  } catch (error) {
    hideTypingIndicator();

    if (generation.cancelled || error?.name === "AbortError") {
      showGenerationStopped();
    } else {
      addMessage(
        "assistant",
        `**Guest chat error:** ${error.message || "Please try again."}`,
      );

      renderTrace(["Guest request failed"], false);
      sourceUsed.textContent = "Error";
    }
  } finally {
    hideTypingIndicator();

    if (activeGeneration === generation) {
      activeGeneration = null;
    }

    setRequestBusy(false);
  }
}

async function askAgent(rawQuestion) {
  const userQuestion = String(rawQuestion || "").trim();

  if (!userQuestion || requestInProgress) return;

  if (window.hrmindCurrentUser?.role === "guest") {
    await askGuestAgent(userQuestion);
    return;
  }

  const generation = createGenerationContext("chat");
  setRequestBusy(true);

  try {
    if (!currentConversationId) {
      const created = await apiRequest("/api/conversations", {
        method: "POST",
        signal: generation.controller.signal,
      });

      if (generation.cancelled) {
        throw new DOMException("Stopped", "AbortError");
      }

      currentConversationId = created.conversation.id;
      currentConversationHasMessages = false;

      updateConversationToolbar();
      resetChatUI();

      await refreshHistory(false);
    }

    if (generation.cancelled) {
      throw new DOMException("Stopped", "AbortError");
    }

    document.getElementById("welcomeState")?.remove();
    document.getElementById("chatExamples")?.classList.remove("hidden");

    addMessage("user", userQuestion);

    currentConversationHasMessages = true;
    updateConversationToolbar();

    question.value = "";

    showTypingIndicator();

    renderTrace(["Starting LangGraph workflow…"], true);
    sourceUsed.textContent = "Running…";

    const response = await fetch("/api/chat", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      signal: generation.controller.signal,
      body: JSON.stringify({
        question: userQuestion,
        conversation_id: currentConversationId,
        request_id: generation.requestId,
      }),
    });

    if (!response.ok) {
      let message = `Request failed (${response.status}).`;

      try {
        message = (await response.json()).detail || message;
      } catch {
        // Keep the default error message.
      }

      throw new Error(message);
    }

    if (!response.body) {
      throw new Error("Streaming is not available for this response.");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    let buffer = "";
    let finalData = null;
    const streamedTrace = [];

    function handleLine(line) {
      if (!line.trim()) return;

      let event;

      try {
        event = JSON.parse(line);
      } catch {
        throw new Error("The server returned an invalid streaming response.");
      }

      if (event.type === "trace") {
        streamedTrace.push(
          `Running → ${formatTraceNode(event.node || "Workflow step")}`,
        );

        renderTrace(streamedTrace, true);
      } else if (event.type === "final") {
        finalData = event;
      } else if (event.type === "cancelled") {
        generation.cancelled = true;
      } else if (event.type === "error") {
        throw new Error(
          event.detail || "The agent could not complete this request.",
        );
      }
    }

    try {
      while (true) {
        const { value, done } = await reader.read();

        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          handleLine(line);
        }
      }

      buffer += decoder.decode();

      if (buffer.trim()) {
        handleLine(buffer);
      }
    } finally {
      try {
        reader.releaseLock();
      } catch {
        // Reader already released.
      }
    }

    if (generation.cancelled) {
      showGenerationStopped();
      return;
    }

    if (!finalData) {
      throw new Error(
        "The agent ended without a final answer. Please try again.",
      );
    }

    hideTypingIndicator();

    addMessage(
      "assistant",
      finalData.answer || "The agent returned an empty answer.",
      finalData.source_used || "",
      finalData.citations || [],
    );

    renderTrace(finalData.trace || streamedTrace, false);

    sourceUsed.textContent = formatSource(finalData.source_used || "");
  } catch (error) {
    hideTypingIndicator();

    if (generation.cancelled || error?.name === "AbortError") {
      showGenerationStopped();
    } else {
      addMessage(
        "assistant",
        `**I couldn't complete that request.** ${
          error.message || "An unexpected error occurred."
        }`,
      );

      renderTrace(["Request failed"], false);
      sourceUsed.textContent = "Error";
    }
  } finally {
    hideTypingIndicator();

    if (activeGeneration === generation) {
      activeGeneration = null;
    }

    setRequestBusy(false);

    try {
      await refreshHistory(false);
    } catch {
      // Keep the chat visible if history refresh fails.
    }
  }
}
question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    if (!requestInProgress) form.requestSubmit();
  }
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!requestInProgress) askAgent(question.value);
});

document.addEventListener("click", (event) => {
  const button = event.target.closest(".example");
  if (!button || requestInProgress) return;

  event.preventDefault();
  askAgent(button.dataset.question || button.textContent.trim());
});

// ---------------------------------------------------------
// Company and personal document management
// ---------------------------------------------------------

function updateDocumentScopeUI() {
  const isAdmin = window.hrmindCurrentUser?.role === "admin";
  const scope = uploadScope.value;

  companyDocsPanel.classList.toggle(
    "hidden",
    !(isAdmin && scope === "company"),
  );
  personalDocsPanel.classList.toggle("hidden", scope !== "personal");

  document.getElementById("uploadDescription").textContent =
    scope === "company"
      ? "This document becomes available to all verified employees in the company HR knowledge base."
      : "This document is private to your account and is searchable only in your own conversations.";
}

function renderDocumentList(container, docs, kind) {
  if (!docs.length) {
    container.innerHTML = '<div class="empty">No documents uploaded yet.</div>';
    return;
  }

  container.innerHTML = docs
    .map(
      (doc) => `
    <div class="document-item">
      <div class="document-item-info">
        <strong>${escapeHtml(doc.filename || "Document")}</strong>
        <span>
          ${(Number(doc.size_bytes || 0) / 1024).toFixed(0)} KB
          · ${escapeHtml(formatDate(doc.created_at) || "Recently added")}
        </span>
      </div>
      <button class="document-delete-btn" type="button"
        data-document-kind="${kind}" data-document-id="${escapeHtml(doc.id)}">Delete</button>
    </div>
  `,
    )
    .join("");
}

async function refreshDocuments() {
  const user = window.hrmindCurrentUser;
  if (!user) return;

  personalDocumentsList.innerHTML =
    '<div class="empty">Loading documents…</div>';

  try {
    const personal = await apiRequest("/api/documents/personal");
    renderDocumentList(
      personalDocumentsList,
      personal.documents || [],
      "personal",
    );
  } catch (error) {
    personalDocumentsList.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }

  if (user.role === "admin") {
    try {
      const company = await apiRequest("/api/documents/company");
      renderDocumentList(
        companyDocumentsList,
        company.documents || [],
        "company",
      );
    } catch (error) {
      companyDocumentsList.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    }
  }

  updateDocumentScopeUI();
}

uploadScope.addEventListener("change", updateDocumentScopeUI);

document.getElementById("openUpload").addEventListener("click", async () => {
  uploadModal.classList.remove("hidden");
  fileInput.value = "";
  fileLabel.textContent = "Choose HR document";
  uploadStatus.textContent = "";
  updateDocumentScopeUI();
  await refreshDocuments();
});

document.getElementById("closeUpload").addEventListener("click", () => {
  uploadModal.classList.add("hidden");
});

fileInput.addEventListener("change", () => {
  fileLabel.textContent = fileInput.files?.[0]?.name || "Choose HR document";
});

uploadButton.addEventListener("click", async () => {
  if (uploadInProgress) return;

  const file = fileInput.files?.[0];

  if (!file) {
    uploadStatus.textContent = "Choose a file first.";
    return;
  }

  if (file.size > 10 * 1024 * 1024) {
    uploadStatus.textContent = "Maximum upload size is 10 MB.";
    return;
  }

  const scope = uploadScope.value;
  const user = window.hrmindCurrentUser;

  if (scope === "company" && user?.role !== "admin") {
    uploadStatus.textContent =
      "Only administrators can upload company-wide documents.";
    return;
  }

  const endpoint =
    scope === "company" ? "/api/ingest" : "/api/documents/personal";
  const body = new FormData();
  body.append("file", file);

  uploadInProgress = true;
  uploadButton.disabled = true;
  uploadButton.textContent = "Uploading and indexing…";
  uploadStatus.textContent =
    "Uploading document and preparing searchable chunks…";

  try {
    const result = await apiRequest(endpoint, { method: "POST", body });
    uploadStatus.textContent = `✓ ${result.file || file.name} indexed successfully.`;
    fileInput.value = "";
    fileLabel.textContent = "Choose HR document";
    await refreshDocuments();
  } catch (error) {
    uploadStatus.textContent = `Error: ${error.message || "Upload failed."}`;
  } finally {
    uploadInProgress = false;
    uploadButton.disabled = false;
    uploadButton.textContent = "Upload and index";
  }
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-document-kind]");
  if (!button) return;

  const kind = button.dataset.documentKind;
  const id = button.dataset.documentId;
  const isCompany = kind === "company";

  if (
    !window.confirm(
      `Delete this ${isCompany ? "company-wide" : "private"} document and its indexed chunks?`,
    )
  ) {
    return;
  }

  button.disabled = true;

  try {
    await apiRequest(
      `/api/documents/${isCompany ? "company" : "personal"}/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    );

    uploadStatus.textContent = "Document deleted.";
    await refreshDocuments();
  } catch (error) {
    uploadStatus.textContent = `Could not delete document: ${error.message}`;
  } finally {
    button.disabled = false;
  }
});

// ---------------------------------------------------------
// Typing indicator
// ---------------------------------------------------------

function showTypingIndicator() {
  hideTypingIndicator();

  const wrap = document.createElement("div");
  wrap.id = "typingIndicator";
  wrap.className = "message assistant";
  wrap.setAttribute("role", "status");
  wrap.setAttribute("aria-live", "polite");

  wrap.innerHTML = `
    <div class="avatar" aria-hidden="true">AI</div>
    <div class="bubble">
      <div class="typing-indicator">
        <span>HR Mind AI is thinking</span>
        <div class="typing-dots" aria-hidden="true"><span></span><span></span><span></span></div>
      </div>
    </div>
  `;

  chat.appendChild(wrap);
  scrollLatestIntoView(wrap);
}

function hideTypingIndicator() {
  document.getElementById("typingIndicator")?.remove();
}

// ---------------------------------------------------------
// Initialization and account switching
// ---------------------------------------------------------

async function initializeApp() {
  currentConversationId = null;
  updateConversationToolbar();
  resetChatUI();

  try {
    // Load history for the History dialog, but do not reopen an old chat.
    await refreshHistory(false);
  } catch (error) {
    console.error("Could not load conversation history:", error);
  }

  try {
    await refreshDocuments();
  } catch (error) {
    console.error("Could not load documents:", error);
  }

  updateConversationToolbar();
}

window.addEventListener("hrmind:authenticated", () => {
  initializeApp();
});

window.addEventListener("hrmind:loggedout", () => {
  currentConversationId = null;
  conversations = [];
  chat.innerHTML = initialWelcomeMarkup;
  renderTrace([], false);
  sourceUsed.textContent = "—";
  historyModal.classList.add("hidden");
  uploadModal.classList.add("hidden");
  conversationActionModal.classList.add("hidden");
  shareModal.classList.add("hidden");
  shareModalConversationId = null;
});

function initializeGuestMode() {
  currentConversationId = null;
  conversations = [];
  resetChatUI();
  updateConversationToolbar();

  document.getElementById("historyToggle").classList.add("hidden");
  document.getElementById("newChatBtn").classList.add("hidden");
  document.getElementById("openUpload").classList.add("hidden");
  document.getElementById("manageUsersHeaderBtn").classList.add("hidden");

  question.placeholder = "Ask a general HR question using public information…";

  const heading = document.querySelector("#welcomeState h2");
  const intro = document.querySelector("#welcomeState p");

  if (heading) heading.textContent = "Try HR Mind AI as a guest";

  if (intro) {
    intro.textContent =
      "Explore general HR topics using public web information. Company policies and private documents require sign-in.";
  }

  const welcomeCards = document.querySelectorAll("#welcomeState .welcome-card");

  const generalQuestions = [
    {
      title: "Annual Leave Basics",
      description: "Understand common leave policies and terminology.",
      question: "What is annual leave and how is it commonly calculated?",
    },
    {
      title: "Remote Work",
      description: "Learn about common remote-work practices.",
      question: "What are common best practices for remote working?",
    },
    {
      title: "Expense Policies",
      description: "Explore typical business expense procedures.",
      question: "What should a business travel expense policy include?",
    },
    {
      title: "Employee Rights",
      description: "Explore general employment information.",
      question:
        "What should employees consider when reviewing an employment contract?",
    },
  ];

  welcomeCards.forEach((card, index) => {
    const suggestion = generalQuestions[index];
    if (!suggestion) return;

    card.dataset.question = suggestion.question;
    const title = card.querySelector("strong");
    const description = card.querySelector("span");

    if (title) title.textContent = suggestion.title;
    if (description) description.textContent = suggestion.description;
  });
}

window.addEventListener("hrmind:guest", initializeGuestMode);
