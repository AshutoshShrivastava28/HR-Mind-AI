(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const range = $("qualityRange");
  const status = $("qualityStatus");
  const dashboard = $("qualityDashboard");
  const denied = $("qualityDenied");
  const reasonLabels = {
    inaccurate: "Inaccurate or unsupported",
    missing_context: "Missing context",
    citation_issue: "Source or citation issue",
    not_relevant: "Not relevant",
    other: "Other",
  };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
  const pct = (value) => `${Number(value || 0).toFixed(1)}%`;
  const fmtDate = (value) => {
    if (!value) return "Date unavailable";
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? String(value) : new Intl.DateTimeFormat(undefined, {month:"short", day:"numeric", hour:"2-digit", minute:"2-digit"}).format(d);
  };
  async function api(url, options = {}) {
    const response = await fetch(url, {credentials:"same-origin", ...options, headers:{...(options.headers || {})}});
    let data = {};
    try { data = await response.json(); } catch { /* No JSON body. */ }
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status}).`);
    return data;
  }
  function setStatus(message, isError = false) {
    status.textContent = message || "";
    status.classList.toggle("is-error", Boolean(isError));
  }
  function renderTrend(rows) {
    const target = $("qualityTrend");
    if (!rows?.length) {
      target.innerHTML = '<div class="quality-empty">No activity recorded in this period yet.</div>';
      return;
    }
    const visible = rows.slice(-14);
    const max = Math.max(1, ...visible.map((r) => Number(r.answers || 0)));
    target.innerHTML = visible.map((row) => {
      const answers = Number(row.answers || 0);
      const helpful = Number(row.helpful || 0);
      const gaps = Number(row.evidence_gaps || 0);
      const barHeight = (value) => value ? Math.max(4, Math.round(138 * value / max)) : 2;
      const label = String(row.day || "").slice(5);
      return `<div class="quality-trend-day" title="${esc(row.day)}: ${answers} answers, ${helpful} helpful ratings, ${gaps} evidence gaps"><div class="quality-trend-stack"><span class="quality-trend-bar answers" style="height:${barHeight(answers)}px"></span><span class="quality-trend-bar helpful" style="height:${barHeight(helpful)}px"></span><span class="quality-trend-bar gaps" style="height:${barHeight(gaps)}px"></span></div><span class="quality-trend-label">${esc(label)}</span></div>`;
    }).join("");
  }
  function renderTopics(rows) {
    const target = $("qualityTopics");
    if (!rows?.length) { target.innerHTML = '<div class="quality-empty">No answer topics recorded yet.</div>'; return; }
    const max = Math.max(1, ...rows.map((row) => Number(row.total || 0)));
    target.innerHTML = rows.slice(0, 8).map((row) => {
      const total = Number(row.total || 0);
      const helpful = Number(row.helpful_count || 0);
      const notHelpful = Number(row.not_helpful_count || 0);
      const gaps = Number(row.evidence_gaps || 0);
      return `<div class="quality-topic-row"><span class="quality-topic-title">${esc(row.topic)}</span><span class="quality-topic-count">${total} answers</span><div class="quality-topic-track"><div class="quality-topic-fill" style="width:${Math.max(0, Math.min(100, total / max * 100))}%"></div></div><span class="quality-topic-sub">${helpful} helpful · ${notHelpful} needs work · ${gaps} evidence gaps</span></div>`;
    }).join("");
  }
  function renderGaps(rows) {
    const target = $("qualityGaps");
    if (!rows?.length) { target.innerHTML = '<div class="quality-empty">No gaps matched the current rules. Test questions whose answers are absent from the uploaded policies; zero flags do not prove full coverage.</div>'; return; }
    target.innerHTML = rows.map((row) => `<div class="quality-gap-row"><strong>${esc(row.topic)}</strong><span>${Number(row.count || 0)} gap${Number(row.count || 0) === 1 ? "" : "s"}</span></div>`).join("");
  }
  function renderReasons(rows) {
    const target = $("qualityReasons");
    if (!rows?.length) { target.innerHTML = '<div class="quality-empty">No negative ratings yet. Rate new signed-in answers with “Needs work” to record genuine improvement reasons.</div>'; return; }
    target.innerHTML = rows.map((row) => `<div class="quality-reason-row"><strong>${esc(reasonLabels[row.reason] || row.reason || "Not specified")}</strong><span>${Number(row.total || 0)}</span></div>`).join("");
  }
  function renderRecent(rows) {
    const target = $("qualityRecent");
    if (!rows?.length) { target.innerHTML = '<div class="quality-empty">No ratings yet. The prompt batch does not create human feedback; use the Helpful / Needs work controls under new signed-in answers.</div>'; return; }
    target.innerHTML = rows.map((row) => `<article class="quality-recent-item"><div><div class="quality-recent-topic">${esc(row.topic)}</div><div class="quality-recent-meta">${esc(fmtDate(row.created_at))} · Source: ${esc(row.source_used || "unknown")}${row.reason ? ` · ${esc(reasonLabels[row.reason] || row.reason)}` : ""}</div></div><span class="quality-rating ${row.rating === "helpful" ? "helpful" : "not_helpful"}">${row.rating === "helpful" ? "Helpful" : "Needs work"}</span></article>`).join("");
  }
  function renderQualityInsights(data) {
    const target = $("qualityInsights");
    if (!target) return;
    const s = data.summary || {};
    const topics = Array.isArray(data.topics) ? data.topics : [];
    const total = Number(s.total_answers || 0);
    const retrieval = Number(s.retrieval_answers || 0);
    const cited = Number(s.answers_with_citations || 0);
    const citationRate = Number(s.citation_coverage_rate || 0);
    const feedback = Number(s.feedback_responses || 0);
    const feedbackRate = Number(s.feedback_response_rate || 0);
    const engaged = Number(s.source_engaged_count || 0);
    const gaps = Number(s.evidence_gaps || 0);
    const other = topics.find((row) => String(row.topic || "").toLowerCase() === "other hr questions");
    const otherCount = Number(other?.total || 0);
    const otherRate = total > 0 ? Math.round((otherCount / total) * 100) : 0;
    const countText = (value) => Number(value || 0).toLocaleString();
    const insights = [];

    if (!total) {
      insights.push({ tone: "neutral", title: "No activity in this period", body: "Run a small set of signed-in questions, then refresh the dashboard to generate operational insights." });
    } else if (!feedback) {
      insights.push({ tone: "attention", title: "Helpfulness is not measured yet", body: `0 of ${countText(total)} answers have a human rating. The 0% helpfulness figure is a missing-feedback signal, not evidence that answers are poor or good.` });
    } else if (feedbackRate < 25) {
      insights.push({ tone: "attention", title: "Feedback coverage is low", body: `${countText(feedback)} of ${countText(total)} answers are rated (${feedbackRate}%). Collect more ratings before using helpfulness as a reliable quality measure.` });
    } else {
      insights.push({ tone: "positive", title: "Feedback is being collected", body: `${countText(feedback)} of ${countText(total)} answers have a rating (${feedbackRate}%). Review topic-level results before drawing broad conclusions.` });
    }

    if (retrieval > 0 && citationRate >= 90) {
      insights.push({ tone: "positive", title: "Citation presence is strong", body: `${countText(cited)} of ${countText(retrieval)} retrieval-based answers include citations (${citationRate}%). This measures citation presence, not whether the cited policy supports the answer.` });
    } else if (retrieval > 0) {
      insights.push({ tone: "attention", title: "Review citation coverage", body: `${countText(cited)} of ${countText(retrieval)} retrieval-based answers include citations (${citationRate}%). Inspect uncited answers and check whether citation rendering is working.` });
    }

    if (cited > 0 && engaged === 0) {
      insights.push({ tone: "attention", title: "No source-open events recorded", body: `0 of ${countText(cited)} cited answers triggered a source-open event. Check the citation click-tracking path and test it in the chat UI; this may be an instrumentation or usage issue.` });
    } else if (cited > 0) {
      const engagementRate = Number(s.source_engagement_rate || 0);
      insights.push({ tone: "neutral", title: "Source engagement", body: `${countText(engaged)} of ${countText(cited)} cited answers recorded a source open (${engagementRate}%). Use this alongside ratings, not as a standalone quality score.` });
    }

    if (total > 0 && otherRate >= 20) {
      insights.push({ tone: "attention", title: "The topic classifier has a broad bucket", body: `${countText(otherCount)} of ${countText(total)} answers (${otherRate}%) are labelled “Other HR questions”. Consider adding categories for topics such as security, offboarding, workplace concerns and policy exceptions.` });
    } else if (topics.length) {
      insights.push({ tone: "neutral", title: "Topic coverage is available", body: `${topics.length} topic groups appear in this period. Compare counts with uploaded policy coverage to find areas that may need more test questions.` });
    }

    if (retrieval > 0 && gaps === 0) {
      insights.push({ tone: "attention", title: "No evidence gaps matched current rules", body: `0 of ${countText(retrieval)} retrieval-based answers were flagged. This does not prove policy coverage is complete; test questions whose answers are deliberately absent from the uploaded HR documents.` });
    } else if (gaps > 0) {
      insights.push({ tone: "attention", title: "Evidence gaps need review", body: `${countText(gaps)} answer${gaps === 1 ? " was" : "s were"} flagged by the current evidence rules. Review the listed topics and add or clarify authoritative policy content where appropriate.` });
    }

    target.innerHTML = insights.map((item) => `<article class="quality-insight" data-tone="${esc(item.tone)}"><h3>${esc(item.title)}</h3><p>${esc(item.body)}</p></article>`).join("");
  }

  function arrangeQualityColumns() {
    const grid = document.querySelector(".quality-grid");
    if (!grid || grid.dataset.qualityColumnsReady === "true") return;
    const findPanel = (id) => document.getElementById(id)?.closest(".quality-panel");
    const trend = findPanel("qualityTrend");
    const topics = findPanel("qualityTopics");
    const gaps = findPanel("qualityGaps");
    const reasons = findPanel("qualityReasons");
    const recent = findPanel("qualityRecent");
    const panels = [trend, topics, gaps, reasons, recent];
    if (panels.some((panel) => !panel) || new Set(panels).size !== panels.length) return;
    const columns = document.createElement("div");
    columns.className = "quality-columns";
    const left = document.createElement("div");
    left.className = "quality-column";
    const right = document.createElement("div");
    right.className = "quality-column";
    left.append(topics, reasons);
    right.append(gaps, recent);
    columns.append(left, right);
    grid.replaceChildren(trend, columns);
    grid.classList.add("quality-grid-rebalanced");
    grid.dataset.qualityColumnsReady = "true";
  }

  function render(data) {
    const s = data.summary || {};
    $("qTotalAnswers").textContent = Number(s.total_answers || 0).toLocaleString();
    $("qHelpfulness").textContent = pct(s.helpfulness_rate);
    $("qFeedbackResponses").textContent = `${Number(s.feedback_responses || 0)} ratings · ${pct(s.feedback_response_rate)} response rate`;
    $("qCitationCoverage").textContent = pct(s.citation_coverage_rate);
    $("qSourceEngagement").textContent = pct(s.source_engagement_rate);
    $("qSourceEngagementDetail").textContent = `${Number(s.source_engaged_count || 0)} of ${Number(s.answers_with_citations || 0)} cited answers opened a source`;
    $("qEvidenceGaps").textContent = Number(s.evidence_gaps || 0).toLocaleString();
    $("qEvidenceGapRate").textContent = `${pct(s.evidence_gap_rate)} of ${Number(s.retrieval_answers || 0)} retrieval-based answers`;
    renderTrend(data.trend || []);
    renderTopics(data.topics || []);
    renderGaps(data.evidence_gaps_by_topic || []);
    renderReasons(data.feedback_reasons || []);
    renderRecent(data.recent_feedback || []);
    renderQualityInsights(data);
    $("qualityPrivacyNote").textContent = data.privacy_note || "This view contains aggregated answer-quality metadata only.";
  }
  async function load() {
    const button = $("qualityRefresh");
    const previous = button.textContent;
    button.disabled = true;
    button.textContent = "Refreshing…";
    setStatus("Loading answer quality metrics…");
    try {
      const data = await api(`/api/admin/answer-quality?days=${encodeURIComponent(range.value)}`);
      render(data);
      setStatus(`Showing the last ${data.days} days. Metrics update as new signed-in answers and feedback arrive.`);
    } catch (error) {
      setStatus(error.message || "Could not load the dashboard.", true);
    } finally {
      button.disabled = false;
      button.textContent = previous;
    }
  }
  async function initialize() {
    try {
      const me = await api("/api/auth/me");
      if (!me.user || me.user.role !== "admin") {
        dashboard.classList.add("hidden");
        denied.classList.remove("hidden");
        setStatus("This workspace is restricted to administrators.", true);
        return;
      }
      dashboard.classList.remove("hidden");
      setStatus("Administrator access confirmed.");
      await load();
    } catch (error) {
      denied.classList.remove("hidden");
      const link = denied.querySelector("a");
      if (link) link.href = "/?next=/quality";
      setStatus(error.message || "Sign in with an administrator account to continue.", true);
    }
  }
  $("qualityRefresh").addEventListener("click", () => { void load(); });
  range.addEventListener("change", () => { void load(); });
  arrangeQualityColumns();
  void initialize();
})();
