(() => {
  const openButton = document.getElementById("manageUsersHeaderBtn");
  const modal = document.getElementById("userManagementModal");
  const closeButton = document.getElementById("closeUserManagement");
  const status = document.getElementById("userManagementStatus");
  const list = document.getElementById("userManagementList");

  if (!openButton || !modal || !closeButton || !status || !list) {
    return;
  }

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

  async function api(url, options = {}) {
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
      // Use the fallback error message if the response is not JSON.
    }

    if (!response.ok) {
      throw new Error(data.detail || `Request failed (${response.status}).`);
    }

    return data;
  }

  function displayStatus(message, isError = false) {
    status.textContent = message;
    status.classList.toggle("error", isError);
  }

  // Shared custom confirmation modal for all user-management actions.
  function confirmAction({
    title = "Confirm action",
    message = "Are you sure you want to continue?",
    detail = "",
    confirmText = "Confirm",
    danger = false,
  } = {}) {
    return new Promise((resolve) => {
      const overlay = document.createElement("div");

      Object.assign(overlay.style, {
        position: "fixed",
        inset: "0",
        zIndex: "10000",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "20px",
        background: "rgba(0, 0, 0, 0.76)",
        backdropFilter: "blur(5px)",
      });

      const dialog = document.createElement("div");

      Object.assign(dialog.style, {
        width: "100%",
        maxWidth: "440px",
        padding: "26px",
        borderRadius: "18px",
        border: "1px solid #34404e",
        background: "#101720",
        color: "#edf3f8",
        boxShadow: "0 24px 80px rgba(0, 0, 0, 0.5)",
      });

      dialog.setAttribute("role", "alertdialog");
      dialog.setAttribute("aria-modal", "true");
      dialog.setAttribute("aria-labelledby", "adminConfirmTitle");
      dialog.setAttribute("aria-describedby", "adminConfirmMessage");

      const heading = document.createElement("h2");
      heading.id = "adminConfirmTitle";
      heading.textContent = title;

      Object.assign(heading.style, {
        margin: "0 0 14px",
        fontSize: "21px",
        fontWeight: "750",
        color: "#ffffff",
      });

      const messageElement = document.createElement("p");
      messageElement.id = "adminConfirmMessage";
      messageElement.textContent = message;

      Object.assign(messageElement.style, {
        margin: "0 0 14px",
        lineHeight: "1.6",
        fontSize: "15px",
        fontWeight: "600",
        overflowWrap: "anywhere",
        color: "#dce5ee",
      });

      dialog.append(heading, messageElement);

      if (detail) {
        const detailElement = document.createElement("p");
        detailElement.textContent = detail;

        Object.assign(detailElement.style, {
          margin: "0 0 23px",
          lineHeight: "1.65",
          fontSize: "13px",
          color: danger ? "#ff9999" : "#aebdca",
        });

        dialog.appendChild(detailElement);
      } else {
        messageElement.style.marginBottom = "23px";
      }

      const buttons = document.createElement("div");

      Object.assign(buttons.style, {
        display: "flex",
        justifyContent: "flex-end",
        flexWrap: "wrap",
        gap: "10px",
      });

      function makeButton(label, background, color, borderColor) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = label;

        Object.assign(button.style, {
          padding: "10px 16px",
          border: `1px solid ${borderColor}`,
          borderRadius: "9px",
          background,
          color,
          cursor: "pointer",
          fontWeight: "650",
          fontSize: "13px",
        });

        button.addEventListener("mouseenter", () => {
          button.style.filter = "brightness(1.12)";
        });

        button.addEventListener("mouseleave", () => {
          button.style.filter = "none";
        });

        return button;
      }

      const cancelButton = makeButton(
        "Cancel",
        "#18212c",
        "#e1e8ef",
        "#34404e",
      );

      const confirmButton = danger
        ? makeButton(confirmText, "#7f242b", "#ffffff", "#9a3c45")
        : makeButton(confirmText, "#245b43", "#ffffff", "#347b5d");

      let settled = false;

      function finish(confirmed) {
        if (settled) return;
        settled = true;

        document.removeEventListener("keydown", handleKeydown);
        overlay.remove();

        resolve(confirmed);
      }

      function handleKeydown(event) {
        if (event.key === "Escape") {
          finish(false);
        } else if (event.key === "Tab") {
          const buttonsToFocus = [cancelButton, confirmButton];
          const first = buttonsToFocus[0];
          const last = buttonsToFocus[buttonsToFocus.length - 1];

          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }
      }

      cancelButton.addEventListener("click", () => finish(false));
      confirmButton.addEventListener("click", () => finish(true));

      overlay.addEventListener("click", (event) => {
        if (event.target === overlay) {
          finish(false);
        }
      });

      buttons.append(cancelButton, confirmButton);
      dialog.appendChild(buttons);
      overlay.appendChild(dialog);
      document.body.appendChild(overlay);

      document.addEventListener("keydown", handleKeydown);
      cancelButton.focus();
    });
  }

  async function loadUsers() {
    displayStatus("Loading users…");
    list.innerHTML = '<div class="empty">Loading users…</div>';

    try {
      const result = await api("/api/auth/admin/users");
      const users = Array.isArray(result.users) ? result.users : [];
      const currentUserId = String(window.hrmindCurrentUser?.id || "");

      if (!users.length) {
        list.innerHTML = '<div class="empty">No accounts found.</div>';
        displayStatus("No registered accounts.");
        return true;
      }

      const verifiedAdminCount = users.filter(
        (user) => user.role === "admin" && user.is_verified,
      ).length;

      list.innerHTML = users
        .map((user) => {
          const id = escapeHtml(user.id);
          const name = escapeHtml(user.display_name || "Unnamed user");
          const email = escapeHtml(user.email || "");
          const role = user.role === "admin" ? "admin" : "employee";

          const verified = user.is_verified
            ? "Verified"
            : "Verification pending";

          const created = user.created_at
            ? new Date(user.created_at).toLocaleDateString()
            : "Unknown";

          const isSelf = String(user.id) === currentUserId;
          const newRole = role === "admin" ? "employee" : "admin";

          const roleDisabled = !user.is_verified || isSelf;
          const roleTitle = isSelf
            ? "You cannot change your own role"
            : !user.is_verified
              ? "User must verify their email first"
              : role === "admin"
                ? "Demote to employee"
                : "Promote to administrator";

          const deleteDisabled = isSelf;
          const deleteTitle = isSelf
            ? "You cannot delete your own account"
            : "Delete this user and their private data";

          return `
            <div class="admin-user-item">
              <div class="admin-user-info">
                <strong>${name}</strong>
                <span>${email}</span>

                <div class="admin-user-meta">
                  <span class="admin-role-pill ${role}">
                    ${role === "admin" ? "Administrator" : "Employee"}
                  </span>
                  <span>${verified}</span>
                  <span>Joined ${escapeHtml(created)}</span>
                </div>
              </div>

              <div class="admin-user-actions">
                <button
                  type="button"
                  class="history-action admin-role-action"
                  data-action="role"
                  data-user-id="${id}"
                  data-new-role="${newRole}"
                  ${roleDisabled ? "disabled" : ""}
                  title="${escapeHtml(roleTitle)}"
                >${role === "admin" ? "Demote" : "Promote"}</button>

                <button
                  type="button"
                  class="history-action admin-delete-action"
                  data-action="delete"
                  data-user-id="${id}"
                  ${deleteDisabled ? "disabled" : ""}
                  title="${escapeHtml(deleteTitle)}"
                  style="border-color:#71343a;color:#ff9a9a;"
                >Delete</button>
              </div>
            </div>
          `;
        })
        .join("");

      displayStatus(
        `${users.length} account${users.length === 1 ? "" : "s"} · ` +
          `${verifiedAdminCount} verified administrator` +
          `${verifiedAdminCount === 1 ? "" : "s"}`,
      );

      return true;
    } catch (error) {
      list.innerHTML = '<div class="empty">Could not load the user list.</div>';

      displayStatus(error.message, true);
      return false;
    }
  }

  openButton.addEventListener("click", async () => {
    if (window.hrmindCurrentUser?.role !== "admin") {
      displayStatus("Administrator access is required.", true);
      return;
    }

    modal.classList.remove("hidden");
    await loadUsers();
  });

  closeButton.addEventListener("click", () => {
    modal.classList.add("hidden");
  });

  modal.addEventListener("click", (event) => {
    if (event.target === modal) {
      modal.classList.add("hidden");
    }
  });

  list.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");

    if (!button || button.disabled) return;

    const action = button.dataset.action;
    const userId = button.dataset.userId;

    if (!userId) return;

    if (action === "role") {
      const newRole = button.dataset.newRole;

      if (!["admin", "employee"].includes(newRole)) {
        displayStatus("Invalid role selected.", true);
        return;
      }

      const targetRole = newRole === "admin" ? "Administrator" : "Employee";

      const confirmed = await confirmAction({
        title: `${newRole === "admin" ? "Promote" : "Demote"} user`,
        message: `Change this user's role to ${targetRole}?`,
        detail:
          newRole === "admin"
            ? "This will grant administrator permissions, including access to user management and shared company documents."
            : "This will remove administrator permissions and restrict the account to employee access.",
        confirmText: newRole === "admin" ? "Promote user" : "Demote user",
        danger: newRole !== "admin",
      });

      if (!confirmed) return;

      button.disabled = true;
      displayStatus("Updating role…");

      try {
        await api(`/api/auth/admin/users/${encodeURIComponent(userId)}/role`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ role: newRole }),
        });

        await loadUsers();
      } catch (error) {
        displayStatus(error.message, true);
        button.disabled = false;
      }

      return;
    }

    if (action === "delete") {
      const userElement = button.closest(".admin-user-item");

      if (!userElement) return;

      const user = {
        display_name:
          userElement.querySelector(".admin-user-info strong")?.textContent ||
          "Unnamed user",
        email:
          userElement.querySelector(".admin-user-info > span")?.textContent ||
          "",
      };

      const confirmed = await confirmAction({
        title: "Delete user account",
        message: `${user.display_name} (${user.email})`,
        detail:
          "This permanently deletes the user's account, saved conversation history, personal document records, and uploaded personal files. Active sessions will be revoked. Shared company documents will be retained. This action cannot be undone.",
        confirmText: "Delete user",
        danger: true,
      });

      if (!confirmed) return;

      button.disabled = true;
      displayStatus("Deleting user and personal data…");

      try {
        const result = await api(
          `/api/auth/admin/users/${encodeURIComponent(userId)}`,
          { method: "DELETE" },
        );

        const loaded = await loadUsers();

        if (loaded) {
          displayStatus(result.message || "User account deleted successfully.");
        }
      } catch (error) {
        displayStatus(error.message, true);
        button.disabled = false;
      }
    }
  });
})();
