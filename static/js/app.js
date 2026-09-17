const chat = document.getElementById("chat");
const form = document.getElementById("chatForm");
const question = document.getElementById("question");
const trace = document.getElementById("trace");
const sourceUsed = document.getElementById("sourceUsed");
function escapeHtml(s = "") {
  return s.replace(
    /[&<>'"]/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[
        c
      ],
  );
}
function formatText(s = "") {
  s = s.replace(/【[^】]*】/g, "");
  return DOMPurify.sanitize(marked.parse(s));
}
function addMessage(role, text, source = "", citations = []) {
  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;
  const citeHtml = citations.length
    ? `
    <div class="citations">
      <strong>Sources</strong>
      <div class="source-list">
        ${citations
          .map(
            (c) => `
              <div class="source-card">
                <span class="source-icon">↗</span>
                ${
                  c.url
                    ? `<a href="${escapeHtml(c.url)}" target="_blank" rel="noopener">
                         ${escapeHtml(c.title)}
                       </a>`
                    : `<span>${escapeHtml(c.title)}</span>`
                }
              </div>
            `,
          )
          .join("")}
      </div>
    </div>
  `
    : "";
  wrap.innerHTML = `<div class="avatar">AI</div><div class="bubble">${role === "user" ? escapeHtml(text).replace(/\n/g, "<br>") : formatText(text)}${source ? `<div class="answer-source">Source: ${escapeHtml(source)}</div>` : ""}${citeHtml}</div>`;
  chat.appendChild(wrap);
  chat.scrollTo({
    top: chat.scrollHeight,
    behavior: "smooth",
  });
}
function renderTrace(items = [], running = false) {
  trace.innerHTML = items.length
    ? items
        .map(
          (x, i) => `
            <div class="trace-item ${running && i === items.length - 1 ? "active" : "completed"}">
              <span class="trace-dot">
                ${running && i === items.length - 1 ? "•" : "✓"}
              </span>
              <span>${escapeHtml(x)}</span>
            </div>
          `,
        )
        .join("")
    : '<div class="empty">No request yet.</div>';
}
async function askAgent(q) {
  const welcome = document.getElementById("welcomeState");
  if (welcome) welcome.remove();

  const chatExamples = document.getElementById("chatExamples");
  if (chatExamples) chatExamples.classList.remove("hidden");

  addMessage("user", q);
  question.value = "";

  showTypingIndicator();
  renderTrace(["Running LangGraph workflow..."], true);
  sourceUsed.textContent = "Running";

  const btn = form.querySelector("button");
  btn.disabled = true;

  let streamedTrace = [];

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    });

    if (!res.ok) {
      const errorData = await res.json();
      throw new Error(errorData.detail || "Request failed");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalData = null;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.trim()) continue;

        const event = JSON.parse(line);

        if (event.type === "trace") {
          streamedTrace.push(`Running → ${event.node}`);
          renderTrace(streamedTrace, true);
        }

        if (event.type === "final") {
          finalData = event;
        }

        if (event.type === "error") {
          throw new Error(event.detail);
        }
      }
    }

    hideTypingIndicator();

    if (finalData) {
      addMessage(
        "assistant",
        finalData.answer,
        finalData.source_used,
        finalData.citations || [],
      );

      renderTrace(finalData.trace || [], false);
      sourceUsed.textContent = finalData.source_used;
    }
  } catch (e) {
    hideTypingIndicator();
    addMessage("assistant", `Error: ${e.message}`);
    renderTrace(["Request failed"], false);
    sourceUsed.textContent = "Error";
  } finally {
    btn.disabled = false;
  }
}
question.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});
form.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = question.value.trim();
  if (q) askAgent(q);
});
document
  .querySelectorAll(".example")
  .forEach((b) =>
    b.addEventListener("click", () =>
      askAgent(b.dataset.question || b.textContent.trim()),
    ),
  );
const modal = document.getElementById("uploadModal");
const fileInput = document.getElementById("fileInput");
const fileLabel = document.getElementById("fileLabel");

fileInput.addEventListener("change", () => {
  fileLabel.textContent = fileInput.files[0]
    ? fileInput.files[0].name
    : "Choose HR document";
});
document.getElementById("openUpload").onclick = () =>
  modal.classList.remove("hidden");
document.getElementById("closeUpload").onclick = () =>
  modal.classList.add("hidden");
document.getElementById("uploadBtn").onclick = async () => {
  const file = document.getElementById("fileInput").files[0];
  const key = document.getElementById("adminKey").value;
  const status = document.getElementById("uploadStatus");
  if (!file) {
    status.textContent = "Choose a file first.";
    return;
  }
  status.textContent = "Submitting document...";
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/api/ingest", {
      method: "POST",
      headers: { "X-Admin-Key": key },
      body: fd,
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || "Upload failed");
    status.textContent = "✓ Document submitted successfully";
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  }
};

function showTypingIndicator() {
  const wrap = document.createElement("div");
  wrap.id = "typingIndicator";
  wrap.className = "message assistant";

  wrap.innerHTML = `
    <div class="avatar">AI</div>
    <div class="bubble">
      <div class="typing-indicator">
        <span>HR Mind AI is thinking</span>
        <div class="typing-dots">
          <span></span>
          <span></span>
          <span></span>
        </div>
      </div>
    </div>
  `;

  chat.appendChild(wrap);
  chat.scrollTop = chat.scrollHeight;
}

function hideTypingIndicator() {
  document.getElementById("typingIndicator")?.remove();
}
