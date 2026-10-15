const chat = document.getElementById("chat");
const form = document.getElementById("chatForm");
const question = document.getElementById("question");
const trace = document.getElementById("trace");
const sourceUsed = document.getElementById("sourceUsed");
const submitButton = form.querySelector('button[type="submit"]');

let requestInProgress = false;

// Human-readable names for backend source identifiers.
const SOURCE_LABELS = {
  private_kb: "Company Knowledge Base",
  web_search: "External Web Search",
  web: "External Web Search",
  direct: "Direct Response",
  insufficient_evidence: "Insufficient Evidence",
  running: "Running…",
  error: "Error",
};

// Human-readable names for LangGraph nodes.
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

  if (!key) {
    return "Unknown";
  }

  return SOURCE_LABELS[key] || String(value).replace(/_/g, " ");
}

function formatTraceNode(value = "") {
  const key = String(value || "").trim();

  return TRACE_NODE_LABELS[key] || key.replace(/_/g, " ");
}

// Remove machine-specific directory paths from document titles.
function cleanCitationTitle(citation = {}) {
  const rawTitle = String(citation.title || citation.url || "Source").trim();

  const looksLikeLocalPath =
    citation.type === "private_kb" ||
    /^[a-z]:[\\/]/i.test(rawTitle) ||
    rawTitle.startsWith("/") ||
    rawTitle.includes("\\");

  if (looksLikeLocalPath) {
    return (
      rawTitle.replace(/\\/g, "/").split("/").filter(Boolean).pop() ||
      "Company document"
    );
  }

  return rawTitle;
}

// Allow only HTTP and HTTPS links in source cards.
function safeExternalUrl(value) {
  if (typeof value !== "string" || !value.trim()) {
    return "";
  }

  try {
    const url = new URL(value, window.location.origin);

    if (url.protocol !== "https:" && url.protocol !== "http:") {
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

// Scroll the chat panel on desktop and the page on mobile.
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
                <span
                  class="source-icon"
                  aria-hidden="true"
                >↗</span>

                ${
                  url
                    ? `
                      <a
                        href="${escapeHtml(url)}"
                        target="_blank"
                        rel="noopener noreferrer"
                      >${title}</a>
                    `
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
    ? `
      <div class="answer-source" title="Answer source">
        Source: ${escapeHtml(formatSource(source))}
      </div>
    `
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

// Render the live LangGraph trace in the right panel.
function renderTrace(items = [], running = false) {
  const safeItems = Array.isArray(items) ? items : [];

  trace.setAttribute("role", "status");
  trace.setAttribute("aria-live", "polite");
  trace.setAttribute("aria-relevant", "additions text");

  trace.innerHTML = safeItems.length
    ? safeItems
        .map((item, index) => {
          const isActive = running && index === safeItems.length - 1;

          return `
          <div class="trace-item ${isActive ? "active" : "completed"}">
            <span
              class="trace-dot"
              aria-hidden="true"
            >${isActive ? "•" : "✓"}</span>

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
}

async function askAgent(rawQuestion) {
  const userQuestion = String(rawQuestion || "").trim();

  if (!userQuestion || requestInProgress) {
    return;
  }

  const welcome = document.getElementById("welcomeState");

  if (welcome) {
    welcome.remove();
  }

  const chatExamples = document.getElementById("chatExamples");

  if (chatExamples) {
    chatExamples.classList.remove("hidden");
  }

  addMessage("user", userQuestion);

  question.value = "";

  showTypingIndicator();

  renderTrace(["Starting LangGraph workflow…"], true);

  sourceUsed.textContent = formatSource("running");

  setRequestBusy(true);

  const streamedTrace = [];

  let finalData = null;
  let reader = null;

  function handleStreamLine(line) {
    if (!line.trim()) {
      return;
    }

    let event;

    try {
      event = JSON.parse(line);
    } catch {
      throw new Error("The server returned an invalid streaming response.");
    }

    if (event.type === "trace") {
      const nodeName = formatTraceNode(event.node || "Workflow step");

      streamedTrace.push(`Running → ${nodeName}`);

      renderTrace(streamedTrace, true);
    } else if (event.type === "final") {
      finalData = event;
    } else if (event.type === "error") {
      throw new Error(
        event.detail || "The agent could not complete this request.",
      );
    }
  }

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        question: userQuestion,
      }),
    });

    if (!response.ok) {
      const responseText = await response.text();

      let message = `Request failed (${response.status}).`;

      try {
        const errorData = JSON.parse(responseText);

        message = errorData.detail || message;
      } catch {
        if (responseText.trim()) {
          message = responseText.trim().slice(0, 300);
        }
      }

      throw new Error(message);
    }

    if (!response.body) {
      throw new Error("Streaming is not available in this browser response.");
    }

    reader = response.body.getReader();

    const decoder = new TextDecoder();

    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();

      if (done) {
        break;
      }

      buffer += decoder.decode(value, {
        stream: true,
      });

      const lines = buffer.split("\n");

      buffer = lines.pop() || "";

      for (const line of lines) {
        handleStreamLine(line);
      }
    }

    // Process the last response record even when it has no newline.
    buffer += decoder.decode();

    if (buffer.trim()) {
      handleStreamLine(buffer);
    }

    if (!finalData) {
      throw new Error(
        "The agent ended the response without returning a final answer. Please try again.",
      );
    }

    hideTypingIndicator();

    addMessage(
      "assistant",
      finalData.answer ||
        "The agent completed the request but returned an empty answer.",
      finalData.source_used || "",
      finalData.citations || [],
    );

    renderTrace(finalData.trace || streamedTrace, false);

    sourceUsed.textContent = formatSource(finalData.source_used || "");
  } catch (error) {
    hideTypingIndicator();

    addMessage(
      "assistant",
      `**I couldn't complete that request.** ${
        error?.message || "An unexpected error occurred."
      }`,
    );

    renderTrace(["Request failed"], false);

    sourceUsed.textContent = formatSource("error");
  } finally {
    if (reader) {
      try {
        reader.releaseLock();
      } catch {
        // The stream may already have released its reader.
      }
    }

    hideTypingIndicator();

    setRequestBusy(false);
  }
}

// Enter submits; Shift + Enter adds a new line.
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

// Suggested questions.
document.querySelectorAll(".example").forEach((button) => {
  button.addEventListener("click", () => {
    if (requestInProgress) {
      return;
    }

    askAgent(button.dataset.question || button.textContent.trim());
  });
});

// HR document upload modal.
const modal = document.getElementById("uploadModal");
const fileInput = document.getElementById("fileInput");
const fileLabel = document.getElementById("fileLabel");
const uploadButton = document.getElementById("uploadBtn");

fileInput.addEventListener("change", () => {
  fileLabel.textContent = fileInput.files?.[0]?.name || "Choose HR document";
});

document.getElementById("openUpload").addEventListener("click", () => {
  modal.classList.remove("hidden");
});

document.getElementById("closeUpload").addEventListener("click", () => {
  modal.classList.add("hidden");
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
    status.textContent = `Error: ${error?.message || "Upload failed."}`;
  } finally {
    uploadButton.disabled = false;
    uploadButton.textContent = "Submit Document";
  }
});

// Typing indicator.
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

        <div
          class="typing-dots"
          aria-hidden="true"
        >
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
