const chat = document.getElementById("chat");
const form = document.getElementById("chatForm");
const question = document.getElementById("question");
const trace = document.getElementById("trace");
const sourceUsed = document.getElementById("sourceUsed");
const submitButton = form.querySelector('button[type="submit"]');

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

let pendingConversationAction = null;
let actionSaving = false;
const initialWelcomeMarkup = chat.innerHTML;

let requestInProgress = false;
let currentConversationId = null;
let conversations = [];

const SOURCE_LABELS = {
  private_kb: "Company Knowledge Base",
  web_search: "External Web Search",
  web: "External Web Search",
  direct: "Direct Response",
  insufficient_evidence: "Insufficient Evidence",
  running: "Running…",
  error: "Error",
};

const TRACE_NODE_LABELS = {
  route_question: "Route question",
  retrieve_kb: "Retrieve private knowledge base",
  grade_kb: "Evaluate private KB evidence",
  search_web: "Search public web with Tavily",
  grade_web: "Evaluate web evidence",
  rewrite_query: "Rewrite query and retry",
  generate_from_kb: "Generate answer from private KB",
  generate_from_web: "Generate answer from web sources",
  direct_answer: "Generate direct response",
  insufficient: "Stop: insufficient evidence",
};

function escapeHtml(value = "") {
  return String(value).replace(
    /[&<>'"]/g,
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

  if (!key) return "Unknown";

  return SOURCE_LABELS[key] || String(value).replace(/_/g, " ");
}

function formatTraceNode(value = "") {
  const key = String(value || "").trim();

  return TRACE_NODE_LABELS[key] || key.replace(/_/g, " ");
}

function cleanCitationTitle(citation = {}) {
  const title = String(citation.title || citation.url || "Source").trim();

  const looksLikeLocalPath =
    citation.type === "private_kb" ||
    /^[a-z]:[\\/]/i.test(title) ||
    title.startsWith("/") ||
    title.includes("\\");

  if (looksLikeLocalPath) {
    return (
      title.replace(/\\/g, "/").split("/").filter(Boolean).pop() ||
      "Company document"
    );
  }

  return title;
}

function safeExternalUrl(value) {
  if (typeof value !== "string" || !value.trim()) {
    return "";
  }

  try {
    const url = new URL(value, window.location.origin);

    if (!["http:", "https:"].includes(url.protocol)) {
      return "";
    }

    return url.href;
  } catch {
    return "";
  }
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

  if (!response.ok) {
    let message = `Request failed (${response.status}).`;

    try {
      const data = await response.json();
      message = data.detail || message;
    } catch {
      // Keep the generic error message.
    }

    throw new Error(message);
  }

  return response.json();
}

function scrollLatestIntoView(element = chat) {
  const styles = window.getComputedStyle(chat);

  const hasInternalScroll =
    styles.overflowY !== "visible" && chat.scrollHeight > chat.clientHeight + 2;

  if (hasInternalScroll) {
    chat.scrollTo({
      top: chat.scrollHeight,
      behavior: "smooth",
    });

    return;
  }

  const target = element === chat ? chat.lastElementChild : element;

  target?.scrollIntoView({
    behavior: "smooth",
    block: "end",
  });
}

function addMessage(role, text, source = "", citations = []) {
  const wrap = document.createElement("div");

  wrap.className = `message ${role === "user" ? "user" : "assistant"}`;

  const citationItems = Array.isArray(citations) ? citations : [];

  const citationHtml = citationItems.length
    ? `
      <div class="citations">
        <strong>Sources</strong>
        <div class="source-list">
          ${citationItems
            .map((citation) => {
              const title = escapeHtml(cleanCitationTitle(citation || {}));
              const url = safeExternalUrl(citation?.url);

              return `
              <div class="source-card">
                <span class="source-icon" aria-hidden="true">↗</span>
                ${
                  url
                    ? `<a href="${escapeHtml(url)}"
                         target="_blank"
                         rel="noopener noreferrer">${title}</a>`
                    : `<span>${title}</span>`
                }
              </div>
            `;
            })
            .join("")}
        </div>
      </div>
    `
    : "";

  const messageContent =
    role === "user"
      ? escapeHtml(text).replace(/\n/g, "<br>")
      : formatText(text);

  const sourceHtml = source
    ? `<div class="answer-source">Source: ${escapeHtml(formatSource(source))}</div>`
    : "";

  wrap.innerHTML = `
    <div class="avatar" aria-hidden="true">
      ${role === "user" ? "You" : "AI"}
    </div>
    <div class="bubble">
      ${messageContent}
      ${sourceHtml}
      ${citationHtml}
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
          const isActive = running && index === safeItems.length - 1;

          return `
          <div class="trace-item ${isActive ? "active" : "completed"}">
            <span class="trace-dot" aria-hidden="true">
              ${isActive ? "•" : "✓"}
            </span>
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
  submitButton.textContent = isBusy ? "Thinking…" : "Ask Agent";

  form.setAttribute("aria-busy", String(isBusy));

  document.querySelectorAll(".example").forEach((button) => {
    button.disabled = isBusy;
  });

  newChatBtn.disabled = isBusy;
  historyNewChat.disabled = isBusy;
}

function setHistoryStatus(message = "", isError = false) {
  historyStatus.textContent = message;
  historyStatus.classList.toggle("error", isError);
}

function resetChatUI() {
  chat.innerHTML = initialWelcomeMarkup;

  document.getElementById("chatExamples")?.classList.add("hidden");

  renderTrace([], false);
  sourceUsed.textContent = "—";

  question.value = "";
  question.disabled = false;
  submitButton.disabled = false;

  setTimeout(() => question.focus(), 0);
}

function closeHistoryModal() {
  historyModal.classList.add("hidden");
}

function openHistoryModal() {
  historyModal.classList.remove("hidden");
  refreshHistory();
}

function formatDate(value) {
  if (!value) return "";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "";
  }

  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

// ---------- Conversation History ----------

async function refreshHistory(showStatus = true) {
  if (showStatus) {
    setHistoryStatus("Loading conversations…");
  }

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
    if (showStatus) {
      setHistoryStatus(error.message, true);
    }

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

      const messageLabel = `${count} message${count === 1 ? "" : "s"}`;

      const updated = formatDate(item.updated_at);

      const isCurrent = item.id === currentConversationId;

      return `
      <div class="history-item ${isCurrent ? "current" : ""}">
        <button
          type="button"
          class="history-open"
          data-history-action="open"
          data-id="${id}"
          title="Open ${title}"
        >
          <span class="history-title">${title}</span>
          <span class="history-meta">
            ${messageLabel}${updated ? ` · ${escapeHtml(updated)}` : ""}
          </span>
        </button>

        <div class="history-actions">
          <button
            type="button"
            class="history-action"
            data-history-action="rename"
            data-id="${id}"
          >Rename</button>

          <button
            type="button"
            class="history-action delete"
            data-history-action="delete"
            data-id="${id}"
          >Delete</button>
        </div>
      </div>
    `;
    })
    .join("");
}

async function createNewChat() {
  if (requestInProgress) return;

  try {
    const data = await apiRequest("/api/conversations", {
      method: "POST",
    });

    const conversation = data.conversation;

    if (!conversation?.id) {
      throw new Error("The server did not return a conversation ID.");
    }

    currentConversationId = conversation.id;

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
  const conversation = conversations.find((item) => item.id === conversationId);

  if (!conversation || actionSaving) return;

  const isRename = type === "rename";

  pendingConversationAction = {
    type,
    conversationId,
  };

  conversationActionTitle.textContent = isRename
    ? "Rename conversation"
    : "Delete conversation?";

  conversationActionDescription.textContent = isRename
    ? "Choose a name that's easy to recognize later."
    : `Delete "${conversation.title || "New chat"}" and all its saved messages? This action cannot be undone.`;

  conversationRenameFields.hidden = !isRename;

  conversationActionInput.value = isRename ? conversation.title || "" : "";

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

function renameConversation(conversationId) {
  openConversationAction("rename", conversationId);
}

function deleteConversation(conversationId) {
  openConversationAction("delete", conversationId);
}

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
    const encodedId = encodeURIComponent(conversationId);

    if (isRename) {
      await apiRequest(`/api/conversations/${encodedId}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ title }),
      });
    } else {
      await apiRequest(`/api/conversations/${encodedId}`, {
        method: "DELETE",
      });

      if (currentConversationId === conversationId) {
        currentConversationId = null;
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
  if (event.target === conversationActionModal) {
    closeConversationActionDialog();
  }
});

// Delegate history actions so the buttons continue working after re-render.
historyList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-history-action]");

  if (!button) return;

  const action = button.dataset.historyAction;
  const id = button.dataset.id;

  if (!id) return;

  if (action === "open") {
    await openConversation(id);
  } else if (action === "rename") {
    await renameConversation(id);
  } else if (action === "delete") {
    await deleteConversation(id);
  }
});

historyToggle.addEventListener("click", openHistoryModal);

newChatBtn.addEventListener("click", createNewChat);
historyNewChat.addEventListener("click", createNewChat);

closeHistory.addEventListener("click", closeHistoryModal);

refreshHistoryBtn.addEventListener("click", () => {
  refreshHistory();
});

historyModal.addEventListener("click", (event) => {
  if (event.target === historyModal) {
    closeHistoryModal();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeConversationActionDialog();
    closeHistoryModal();
    document.getElementById("uploadModal")?.classList.add("hidden");
  }
});

// ---------- Agent Chat ----------

async function askAgent(rawQuestion) {
  const userQuestion = String(rawQuestion || "").trim();

  if (!userQuestion || requestInProgress) return;

  setRequestBusy(true);

  try {
    // Create a conversation if the user starts chatting before one is selected.
    if (!currentConversationId) {
      const data = await apiRequest("/api/conversations", {
        method: "POST",
      });

      currentConversationId = data.conversation.id;

      resetChatUI();

      await refreshHistory(false);
    }

    document.getElementById("welcomeState")?.remove();

    document.getElementById("chatExamples")?.classList.remove("hidden");

    addMessage("user", userQuestion);

    question.value = "";

    showTypingIndicator();

    renderTrace(["Starting LangGraph workflow…"], true);
    sourceUsed.textContent = "Running…";

    const streamedTrace = [];

    let finalData = null;
    let reader = null;

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          question: userQuestion,
          conversation_id: currentConversationId,
        }),
      });

      if (!response.ok) {
        let message = `Request failed (${response.status}).`;

        try {
          const data = await response.json();
          message = data.detail || message;
        } catch {
          // Keep the default message.
        }

        throw new Error(message);
      }

      if (!response.body) {
        throw new Error("Streaming is not available for this response.");
      }

      reader = response.body.getReader();

      const decoder = new TextDecoder();

      let buffer = "";

      function handleStreamLine(line) {
        if (!line.trim()) return;

        let event;

        try {
          event = JSON.parse(line);
        } catch {
          throw new Error("The server returned an invalid stream event.");
        }

        if (event.type === "trace") {
          streamedTrace.push(
            `Running → ${formatTraceNode(event.node || "Workflow step")}`,
          );

          renderTrace(streamedTrace, true);
        } else if (event.type === "final") {
          finalData = event;
        } else if (event.type === "error") {
          throw new Error(
            event.detail || "The agent could not complete this request.",
          );
        }
      }

      while (true) {
        const { value, done } = await reader.read();

        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");

        buffer = lines.pop() || "";

        for (const line of lines) {
          handleStreamLine(line);
        }
      }

      buffer += decoder.decode();

      if (buffer.trim()) {
        handleStreamLine(buffer);
      }

      if (!finalData) {
        throw new Error(
          "The agent finished without returning a final answer. Please try again.",
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
    } finally {
      if (reader) {
        try {
          reader.releaseLock();
        } catch {
          // The reader may already be released.
        }
      }
    }
  } catch (error) {
    hideTypingIndicator();

    addMessage(
      "assistant",
      `**I couldn't complete that request.** ${
        error.message || "An unexpected error occurred."
      }`,
    );

    renderTrace(["Request failed"], false);
    sourceUsed.textContent = "Error";
  } finally {
    hideTypingIndicator();

    setRequestBusy(false);

    // Refresh conversation titles and message counts after each request.
    try {
      await refreshHistory(false);
    } catch {
      // A history refresh failure should not remove the chat answer.
    }
  }
}

// Enter submits. Shift + Enter adds a line break.
question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();

    if (!requestInProgress) {
      form.requestSubmit();
    }
  }
});

form.addEventListener("submit", (event) => {
  event.preventDefault();

  if (!requestInProgress) {
    askAgent(question.value);
  }
});

// Suggested questions, including those in the welcome screen.
document.addEventListener("click", (event) => {
  const button = event.target.closest(".example");

  if (!button || requestInProgress) return;

  event.preventDefault();

  askAgent(button.dataset.question || button.textContent.trim());
});

// ---------- HR Document Upload ----------

const uploadModal = document.getElementById("uploadModal");
const fileInput = document.getElementById("fileInput");
const fileLabel = document.getElementById("fileLabel");
const uploadButton = document.getElementById("uploadBtn");

fileInput.addEventListener("change", () => {
  fileLabel.textContent = fileInput.files?.[0]?.name || "Choose HR document";
});

document.getElementById("openUpload").addEventListener("click", () => {
  uploadModal.classList.remove("hidden");
});

document.getElementById("closeUpload").addEventListener("click", () => {
  uploadModal.classList.add("hidden");
});

uploadButton.addEventListener("click", async () => {
  const file = fileInput.files?.[0];
  const key = document.getElementById("adminKey").value;
  const status = document.getElementById("uploadStatus");

  if (!file) {
    status.textContent = "Choose a file first.";
    return;
  }

  const formData = new FormData();
  formData.append("file", file);

  uploadButton.disabled = true;
  uploadButton.textContent = "Submitting…";
  status.textContent = "Submitting document…";

  try {
    const response = await fetch("/api/ingest", {
      method: "POST",
      headers: {
        "X-Admin-Key": key,
      },
      body: formData,
    });

    const responseText = await response.text();

    let data = {};

    try {
      data = responseText ? JSON.parse(responseText) : {};
    } catch {
      throw new Error("The server returned an unreadable response.");
    }

    if (!response.ok) {
      throw new Error(data.detail || `Upload failed (${response.status}).`);
    }

    status.textContent = "✓ Document submitted successfully";
  } catch (error) {
    status.textContent = `Error: ${error.message || "Upload failed."}`;
  } finally {
    uploadButton.disabled = false;
    uploadButton.textContent = "Submit Document";
  }
});

// ---------- Typing Indicator ----------

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
        <div class="typing-dots" aria-hidden="true">
          <span></span>
          <span></span>
          <span></span>
        </div>
      </div>
    </div>
  `;

  chat.appendChild(wrap);

  scrollLatestIntoView(wrap);
}

function hideTypingIndicator() {
  document.getElementById("typingIndicator")?.remove();
}

// ---------- Startup ----------

async function initializeApp() {
  try {
    const items = await refreshHistory(false);

    if (items.length) {
      // Restore the most recently updated conversation on page refresh.
      await openConversation(items[0].id);
    } else {
      resetChatUI();
    }
  } catch {
    resetChatUI();
    setHistoryStatus(
      "History is unavailable. You can still try starting a new chat.",
      true,
    );
  }
}

initializeApp();
