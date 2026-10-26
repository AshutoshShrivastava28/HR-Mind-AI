(() => {
  "use strict";

  const menuButton = document.getElementById("scMenuButton");
  const mobileNav = document.getElementById("scNavMobile");
  const form = document.getElementById("scDemoForm");
  const input = document.getElementById("scDemoQuestion");
  const messages = document.getElementById("scDemoMessages");
  const emptyState = document.getElementById("scDemoEmpty");
  const status = document.getElementById("scDemoStatus");
  const submitButton = document.getElementById("scDemoSubmit");

  if (menuButton && mobileNav) {
    menuButton.addEventListener("click", () => {
      const expanded = menuButton.getAttribute("aria-expanded") === "true";
      menuButton.setAttribute("aria-expanded", String(!expanded));
      mobileNav.hidden = expanded;
    });
    mobileNav.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => {
        mobileNav.hidden = true;
        menuButton.setAttribute("aria-expanded", "false");
      });
    });
  }

  const sourceFiles = {
    leave: "HR_Policy_Leave_Attendance_Flexible_Work_Demo.docx",
    travel: "HR_Mind_AI_Demo_Travel_Expense_Policy.docx",
  };

  const passages = [
    {
      id: "annual-leave",
      file: sourceFiles.leave,
      section: "Section 3.1 · Annual leave",
      keywords: ["annual leave", "leave days", "paid leave", "vacation days", "leave allowance"],
      excerpt: "Eligible regular full-time employees receive 20 paid annual-leave days per calendar year. Employees should notify their manager promptly. Approval depends on eligibility, available balance, operational coverage and applicable law.",
      answer: "Eligible regular full-time employees receive 20 paid annual-leave days per calendar year. Approval depends on eligibility, available balance, operational coverage and applicable law.",
    },
    {
      id: "leave-carry-forward",
      file: sourceFiles.leave,
      section: "Section 3.4 · Carry-forward",
      keywords: ["carry forward", "carry-forward", "carry over", "unused leave", "roll over"],
      excerpt: "Employees may carry forward up to 5 unused annual-leave days into the next calendar year. Carried-forward days should be used by 31 March; unused carried-forward days expire unless applicable law or a written agreement provides otherwise.",
      answer: "Up to 5 unused annual-leave days may be carried into the next calendar year. They should be used by 31 March; unused carried-forward days expire unless applicable law or a written agreement provides otherwise.",
    },
    {
      id: "sick-leave",
      file: sourceFiles.leave,
      section: "Section 3.2 · Sick leave",
      keywords: ["sick leave", "medical leave", "illness", "health leave"],
      excerpt: "Eligible full-time employees receive 10 paid sick-leave days per calendar year. Employees should notify their manager promptly when illness affects scheduled work.",
      answer: "Eligible full-time employees receive 10 paid sick-leave days per calendar year and should notify their manager promptly when illness affects scheduled work.",
    },
    {
      id: "personal-leave",
      file: sourceFiles.leave,
      section: "Section 3.3 · Personal / emergency leave",
      keywords: ["personal leave", "emergency leave", "urgent personal matters"],
      excerpt: "Eligible full-time employees receive 3 paid personal or emergency leave days per calendar year for urgent personal matters.",
      answer: "The sample policy provides 3 paid personal or emergency leave days per calendar year for urgent personal matters.",
    },
    {
      id: "remote-work",
      file: sourceFiles.leave,
      section: "Section 4.1 · Remote work",
      keywords: ["remote work", "work from home", "wfh", "remote days", "home working"],
      excerpt: "Employees in eligible roles may request up to 2 remote-work days per week, subject to manager approval. Remote work is a working arrangement, not an additional leave benefit. Eligibility depends on role duties, information-security requirements, client commitments and performance expectations.",
      answer: "Employees in eligible roles may request up to 2 remote-work days per week, subject to manager approval. Remote work is not automatic; eligibility also depends on role duties, security requirements, client commitments and performance expectations.",
    },
    {
      id: "company-holidays",
      file: sourceFiles.leave,
      section: "Section 3.3 · Company holidays",
      keywords: ["company holidays", "public holidays", "holiday calendar", "annual holidays"],
      excerpt: "The sample policy lists 11 company-designated holidays. Employees should refer to the published company holiday calendar for the applicable year and location.",
      answer: "The sample policy lists 11 company-designated holidays. The applicable dates should be checked against the published holiday calendar for the relevant year and location.",
    },
    {
      id: "metro-hotel",
      file: sourceFiles.travel,
      section: "Section 2 · Accommodation",
      keywords: ["hotel", "hotel limit", "accommodation", "room rate", "metro city"],
      excerpt: "For a metro city, the sample policy caps hotel room rates at INR 6,500 per night, excluding non-reimbursable extras. Hotel limits apply to the room rate per night and do not permit personal add-ons.",
      answer: "The sample policy caps hotel room rates in a metro city at INR 6,500 per night, excluding non-reimbursable extras. Personal add-ons are not covered by the room-rate cap.",
    },
    {
      id: "meal-allowance",
      file: sourceFiles.travel,
      section: "Section 3 · Meals",
      keywords: ["meal allowance", "meals", "food allowance", "daily meal", "daily allowance"],
      excerpt: "The sample policy allows up to INR 1,200 per travel day for meals. Claims must relate to the approved business trip and follow the expense-submission requirements.",
      answer: "The sample policy allows up to INR 1,200 per travel day for meals, subject to the approved business trip and the expense-submission requirements.",
    },
    {
      id: "expense-deadline",
      file: sourceFiles.travel,
      section: "Section 5 · Expense submission",
      keywords: ["expense deadline", "submit expenses", "expense submission", "receipts", "reimbursement deadline", "claim expenses"],
      excerpt: "Employees must submit travel expenses within 10 calendar days after the trip ends. The submission should include required receipts, business purpose and trip details.",
      answer: "Employees must submit travel expenses within 10 calendar days after the trip ends, including required receipts, the business purpose and trip details.",
    },
    {
      id: "personal-vehicle",
      file: sourceFiles.travel,
      section: "Section 4 · Personal vehicle",
      keywords: ["personal car", "car reimbursement", "mileage", "per kilometre", "per kilometer", "km rate"],
      excerpt: "A personal vehicle used for an approved business journey is reimbursed at INR 12 per approved kilometre. Employees should record the date, business purpose, origin, destination and kilometres travelled.",
      answer: "The sample policy reimburses an approved personal vehicle at INR 12 per approved kilometre for business travel. The date, purpose, origin, destination and distance must be recorded.",
    },
    {
      id: "domestic-travel-approval",
      file: sourceFiles.travel,
      section: "Section 1 · Travel approval",
      keywords: ["domestic travel", "travel approval", "approve travel", "trip approval", "booking a trip"],
      excerpt: "Domestic business travel should be approved at least 5 business days before departure. Employees should use approved booking options and obtain written approval before booking when a practical option exceeds the policy limit.",
      answer: "Domestic business travel should be approved at least 5 business days before departure. Written approval is also required before booking when a practical option exceeds the policy limit.",
    },
  ];

  const stopWords = new Set([
    "a", "an", "and", "are", "be", "can", "do", "does", "for", "from", "how",
    "i", "in", "is", "it", "many", "me", "my", "of", "on", "or", "please", "the",
    "to", "what", "when", "where", "which", "who", "with", "would", "you", "your",
    "employee", "employees", "policy", "sample", "tell", "about", "allowed", "receive",
  ]);

  function tokenize(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .split(/\s+/)
      .filter((word) => word.length > 2 && !stopWords.has(word));
  }

  function retrieve(question) {
    const q = question.toLowerCase().replace(/[’']/g, "'");
    const selected = [];
    const add = (id) => {
      const passage = passages.find((item) => item.id === id);
      if (passage && !selected.some((item) => item.id === id)) selected.push(passage);
    };

    // Prefer specific intents so a question does not retrieve every passage from a broad word such as "policy".
    if (/carry.?forward|carry over|roll over|unused (annual )?leave/.test(q)) add("leave-carry-forward");
    if (/annual leave|leave days|paid leave|vacation days|leave allowance/.test(q) && !/sick|personal|emergency/.test(q)) add("annual-leave");
    if (/remote|work from home|\bwfh\b/.test(q)) add("remote-work");
    if (/sick leave|medical leave|illness/.test(q)) add("sick-leave");
    if (/personal leave|emergency leave|urgent personal/.test(q)) add("personal-leave");
    if (/company holidays|public holidays|holiday calendar/.test(q)) add("company-holidays");
    if (/hotel|accommodation|room rate|metro city/.test(q)) add("metro-hotel");
    if (/meal|food allowance|daily allowance/.test(q)) add("meal-allowance");
    if (/expense|receipt|reimbursement deadline|submit.*claim|claim.*submit/.test(q)) add("expense-deadline");
    if (/personal car|vehicle|mileage|kilomet|kilometer|\bkm\b/.test(q)) add("personal-vehicle");
    if (/domestic travel|travel approval|approve travel|trip approval|booking a trip/.test(q)) add("domestic-travel-approval");

    if (selected.length) return selected.slice(0, 3);

    const queryTokens = [...new Set(tokenize(q))];
    const scored = passages.map((passage) => {
      const searchable = tokenize(`${passage.section} ${passage.keywords.join(" ")} ${passage.excerpt}`).join(" ");
      const score = queryTokens.reduce((total, token) => total + (searchable.includes(token) ? 1 : 0), 0);
      return { passage, score };
    }).filter((item) => item.score >= 2).sort((a, b) => b.score - a.score);

    if (!scored.length) return [];
    const bestScore = scored[0].score;
    return scored.filter((item) => item.score >= Math.max(2, bestScore - 1)).slice(0, 2).map((item) => item.passage);
  }

  function makeElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function renderQuestion(question) {
    if (emptyState) emptyState.remove();
    const q = makeElement("div", "sc-demo-question", question);
    messages.appendChild(q);
  }

  function renderNotFound() {
    const answer = makeElement("article", "sc-demo-answer");
    const head = makeElement("div", "sc-demo-answer-head");
    head.appendChild(makeElement("span", "sc-empty-mark", "H"));
    head.appendChild(makeElement("strong", "", "HR Mind AI · Guided preview"));
    answer.appendChild(head);
    answer.appendChild(makeElement("p", "", "This sample workspace covers annual and sick leave, carry-forward rules, remote work, company holidays, hotel and meal limits, expense deadlines, personal-vehicle reimbursement, and domestic travel approval. Try one of the suggested questions to see the matching excerpt and source."));
    messages.appendChild(answer);
    status.textContent = "No sufficiently relevant passage was found in the demo pack. Try a suggested topic; the live application has a separate, broader workflow.";
  }

  function renderAnswer(question, matches) {
    const answer = makeElement("article", "sc-demo-answer");
    const head = makeElement("div", "sc-demo-answer-head");
    head.appendChild(makeElement("span", "sc-empty-mark", "H"));
    head.appendChild(makeElement("strong", "", "HR Mind AI · Sample answer"));
    head.appendChild(makeElement("span", "", `${matches.length} source${matches.length === 1 ? "" : "s"}`));
    answer.appendChild(head);

    const answerText = matches.map((item) => item.answer).join(" ");
    answer.appendChild(makeElement("p", "", answerText));

    const citations = makeElement("div", "sc-demo-citations");
    matches.forEach((item, index) => {
      const card = makeElement("div", "sc-demo-citation");
      card.appendChild(makeElement("span", "sc-demo-citation-number", `[${index + 1}]`));
      const copy = makeElement("div", "");
      copy.appendChild(makeElement("strong", "", item.file));
      copy.appendChild(makeElement("small", "", item.section));
      copy.appendChild(makeElement("p", "", item.excerpt));
      card.appendChild(copy);
      citations.appendChild(card);
    });
    answer.appendChild(citations);

    const trace = makeElement("div", "sc-demo-trace");
    ["Matched sample policy", "Selected supporting passage", "Displayed source citation"].forEach((label) => {
      const step = makeElement("span", "");
      step.appendChild(makeElement("i", ""));
      step.appendChild(document.createTextNode(label));
      trace.appendChild(step);
    });
    answer.appendChild(trace);
    messages.appendChild(answer);
    status.textContent = "Guided preview: answers are selected from fictional sample policy passages in this browser, not from a live account or the production knowledge base.";
  }

  function submitQuestion(question) {
    const cleaned = String(question || "").trim();
    if (!cleaned) {
      input.focus();
      return;
    }

    renderQuestion(cleaned);
    input.value = "";
    submitButton.disabled = true;
    submitButton.textContent = "Finding…";

    // Defer one paint so the interaction feels responsive while keeping all demo data local.
    window.requestAnimationFrame(() => {
      const matches = retrieve(cleaned);
      if (matches.length) renderAnswer(cleaned, matches);
      else renderNotFound();
      submitButton.disabled = false;
      submitButton.innerHTML = 'Ask <span aria-hidden="true">↗</span>';
      messages.scrollTop = messages.scrollHeight;
    });
  }

  if (form && input && messages && submitButton) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submitQuestion(input.value);
    });

    document.querySelectorAll("[data-demo-question]").forEach((button) => {
      button.addEventListener("click", () => {
        const question = button.getAttribute("data-demo-question") || "";
        input.value = question;
        submitQuestion(question);
      });
    });
  }
})();
