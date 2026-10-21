(() => {
  const authScreen = document.getElementById("authScreen");
  const appShell = document.getElementById("appShell");
  const authHeading = document.getElementById("authHeading");
  const authIntro = document.getElementById("authIntro");
  const authMessage = document.getElementById("authMessage");
  const devAuthLinkWrap = document.getElementById("devAuthLinkWrap");
  const devAuthLink = document.getElementById("devAuthLink");

  const loginForm = document.getElementById("loginForm");
  const registerForm = document.getElementById("registerForm");
  const resetRequestForm = document.getElementById("resetRequestForm");
  const resetPasswordForm = document.getElementById("resetPasswordForm");
  const authModeSwitch = document.getElementById("authModeSwitch");

  const loginEmail = document.getElementById("loginEmail");
  const loginPassword = document.getElementById("loginPassword");
  const uploadScope = document.getElementById("uploadScope");

  let resetToken = null;
  let busy = false;

  function showMessage(message = "", isError = false) {
    authMessage.textContent = message;
    authMessage.classList.toggle("error", isError);
    devAuthLinkWrap.classList.add("hidden");
  }

  function showDevelopmentLink(url) {
    if (!url) return;

    const parsed = new URL(url, window.location.origin);

    if (!["http:", "https:"].includes(parsed.protocol)) {
      return;
    }

    devAuthLink.href = parsed.href;
    devAuthLink.textContent = parsed.href;
    devAuthLinkWrap.classList.remove("hidden");
  }

  function setBusy(value) {
    busy = value;

    document
      .querySelectorAll("#authScreen button, #authScreen input")
      .forEach((element) => {
        element.disabled = value;
      });
  }

  function setMode(mode, clearStatus = true) {
    if (busy) return;

    loginForm.classList.toggle("hidden", mode !== "login");
    registerForm.classList.toggle("hidden", mode !== "register");
    resetRequestForm.classList.toggle("hidden", mode !== "reset-request");
    resetPasswordForm.classList.toggle("hidden", mode !== "reset-password");

    authModeSwitch.classList.toggle(
      "hidden",
      !["login", "register"].includes(mode),
    );

    const headings = {
      login: [
        "Welcome to HR Mind AI",
        "Sign in to access your HR assistant and saved conversations.",
      ],
      register: [
        "Create your account",
        "Verify your email to activate your HR Mind AI account.",
      ],
      "reset-request": [
        "Reset your password",
        "We'll send a password reset link if a verified account exists.",
      ],
      "reset-password": [
        "Choose a new password",
        "Use a strong password with at least 12 characters.",
      ],
    };

    [authHeading.textContent, authIntro.textContent] =
      headings[mode] || headings.login;

    document.querySelectorAll("[data-auth-mode]").forEach((button) => {
      button.classList.toggle("active", button.dataset.authMode === mode);
    });

    if (clearStatus) showMessage("");
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
      // The response may have no JSON body.
    }

    if (!response.ok) {
      throw new Error(data.detail || `Request failed (${response.status}).`);
    }

    return data;
  }

  async function offerToSavePassword(email, password, name = "") {
    // Never store passwords in localStorage or sessionStorage.
    // Ask the browser's password manager instead, when supported.
    if (
      !email ||
      !password ||
      !window.isSecureContext ||
      !("PasswordCredential" in window) ||
      !navigator.credentials ||
      typeof navigator.credentials.store !== "function"
    ) {
      return;
    }

    try {
      const credential = new window.PasswordCredential({
        id: email,
        password,
        name: name || email,
      });

      await navigator.credentials.store(credential);
    } catch (error) {
      // Password-manager support varies by browser and settings.
      // Failure to save a password must not fail registration or login.
      console.debug("Browser password saving was unavailable.");
    }
  }

  function enterApp(user) {
    window.hrmindCurrentUser = user;

    authScreen.classList.add("hidden");
    appShell.classList.remove("hidden");

    const name = user.display_name || user.email;

    document.getElementById("sidebarUserName").textContent = name;
    document.getElementById("sidebarUserRole").textContent =
      user.role === "admin" ? "Administrator" : "Employee account";

    document.getElementById("sidebarUserInitial").textContent =
      name.trim().charAt(0).toUpperCase() || "U";

    if (uploadScope) {
      const companyOption = uploadScope.querySelector(
        'option[value="company"]',
      );

      if (companyOption) {
        companyOption.hidden = user.role !== "admin";
        companyOption.disabled = user.role !== "admin";
      }

      uploadScope.value = user.role === "admin" ? "company" : "personal";
    }

    const isGuest = user.role === "guest";
    const isAdmin = user.role === "admin";

    document.getElementById("openUpload").classList.toggle("hidden", isGuest);
    document.getElementById("openUploadMobile").classList.toggle("hidden", isGuest);

    document
      .getElementById("historyToggle")
      .classList.toggle("hidden", isGuest);

    document.getElementById("newChatBtn").classList.toggle("hidden", isGuest);

    document
      .getElementById("manageUsersHeaderBtn")
      .classList.toggle("hidden", !isAdmin);

    if (uploadScope) {
      const companyOption = uploadScope.querySelector(
        'option[value="company"]',
      );

      if (companyOption) {
        companyOption.hidden = !isAdmin;
        companyOption.disabled = !isAdmin;
      }

      uploadScope.value = isAdmin ? "company" : "personal";
    }
    window.dispatchEvent(
      new CustomEvent(isGuest ? "hrmind:guest" : "hrmind:authenticated", {
        detail: { user },
      }),
    );
  }

  function showLoginScreen() {
    window.hrmindCurrentUser = null;

    appShell.classList.add("hidden");
    authScreen.classList.remove("hidden");

    // Clear passwords whenever the login screen is shown.
    loginPassword.value = "";
    document.getElementById("registerPassword").value = "";
    document.getElementById("resetNewPassword").value = "";

    // Also reset partially completed registration or reset forms.
    registerForm.reset();
    resetPasswordForm.reset();

    setMode("login", false);
    showMessage("");
  }

  async function handleRegister(event) {
    event.preventDefault();

    if (busy) return;

    const form = new FormData(registerForm);

    setBusy(true);
    showMessage("Creating your account…");

    try {
      const result = await api("/api/auth/register", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          display_name: String(form.get("display_name") || "").trim(),
          email: String(form.get("email") || "").trim(),
          password: String(form.get("password") || ""),
        }),
      });

      const registeredEmail = String(form.get("email") || "").trim();
      const registeredPassword = String(form.get("password") || "");
      const registeredName = String(form.get("display_name") || "").trim();

      // Return to sign-in immediately after the server confirms registration.
      loginEmail.value = registeredEmail;
      setBusy(false);
      setMode("login", false);
      showMessage(
        result.message ||
          "Account created. Check your email for the verification link, then sign in.",
      );

      if (result.development_verification_url) {
        showDevelopmentLink(result.development_verification_url);
      }

      // Clear the hidden form while preserving values for the browser password manager.
      registerForm.reset();
      void offerToSavePassword(
        registeredEmail,
        registeredPassword,
        registeredName,
      );
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      setBusy(false);

      registerForm.querySelectorAll("input").forEach((input) => {
        input.disabled = false;
      });

      registerForm.querySelector('button[type="submit"]').disabled = false;
    }
  }

  async function handleLogin(event) {
    event.preventDefault();

    if (busy) return;

    const form = new FormData(loginForm);
    const submittedEmail = String(form.get("email") || "").trim();

    const submittedPassword = String(form.get("password") || "");

    setBusy(true);
    showMessage("Signing you in…");

    try {
      const result = await api("/api/auth/login", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email: String(form.get("email") || "").trim(),
          password: String(form.get("password") || ""),
        }),
      });

      await offerToSavePassword(
        submittedEmail,
        submittedPassword,
        result.user?.display_name || submittedEmail,
      );

      enterApp(result.user);
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function handleResetRequest(event) {
    event.preventDefault();

    if (busy) return;

    const email = String(
      new FormData(resetRequestForm).get("email") || "",
    ).trim();

    setBusy(true);
    showMessage("Requesting password reset…");

    try {
      const result = await api("/api/auth/request-password-reset", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email }),
      });

      showMessage(
        result.message ||
          "If a verified account exists, a reset link has been sent.",
      );

      if (result.development_reset_url) {
        showDevelopmentLink(result.development_reset_url);
      }
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function handleResetPassword(event) {
    event.preventDefault();

    if (busy || !resetToken) return;

    const password = String(
      new FormData(resetPasswordForm).get("password") || "",
    );

    setBusy(true);
    showMessage("Updating password…");

    try {
      const result = await api("/api/auth/reset-password", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          token: resetToken,
          password,
        }),
      });

      resetToken = null;

      history.replaceState({}, "", window.location.pathname);

      setBusy(false);
      setMode("login", false);

      showMessage(
        result.message || "Password updated. Sign in with your new password.",
      );
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      setBusy(false);
    }
  }

  document.querySelectorAll("[data-auth-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      setMode(button.dataset.authMode);
    });
  });

  loginForm.addEventListener("submit", handleLogin);
  registerForm.addEventListener("submit", handleRegister);
  resetRequestForm.addEventListener("submit", handleResetRequest);
  resetPasswordForm.addEventListener("submit", handleResetPassword);

  document
    .getElementById("forgotPasswordBtn")
    .addEventListener("click", () => setMode("reset-request"));

  document
    .getElementById("resendVerificationBtn")
    .addEventListener("click", async () => {
      const email = loginEmail.value.trim();

      if (!email) {
        showMessage("Enter your email address first.", true);
        loginEmail.focus();
        return;
      }

      setBusy(true);

      try {
        const result = await api("/api/auth/resend-verification", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ email }),
        });

        showMessage(
          result.message ||
            "If an unverified account exists, a verification email has been sent.",
        );

        if (result.development_verification_url) {
          showDevelopmentLink(result.development_verification_url);
        }
      } catch (error) {
        showMessage(error.message, true);
      } finally {
        setBusy(false);
      }
    });

  function openLogoutConfirmation() {
    const modal = document.getElementById("logoutConfirmModal");

    modal.classList.remove("hidden");

    document.getElementById("cancelLogout").focus();
  }

  function closeLogoutConfirmation() {
    document.getElementById("logoutConfirmModal").classList.add("hidden");
  }

  async function performLogout() {
    const confirmButton = document.getElementById("confirmLogout");
    const cancelButton = document.getElementById("cancelLogout");
    const closeButton = document.getElementById("closeLogoutConfirm");

    confirmButton.disabled = true;
    cancelButton.disabled = true;
    closeButton.disabled = true;
    confirmButton.textContent = "Signing out…";

    try {
      await api("/api/auth/logout", {
        method: "POST",
      });
    } catch (error) {
      // The server may be unavailable. The server-side session
      // expiration still limits how long an abandoned session survives.
      console.warn("Sign-out request failed:", error.message);
    }

    closeLogoutConfirmation();
    showLoginScreen();

    window.dispatchEvent(new Event("hrmind:loggedout"));

    confirmButton.disabled = false;
    cancelButton.disabled = false;
    closeButton.disabled = false;
    confirmButton.textContent = "Sign out";
  }

  document
    .getElementById("logoutBtn")
    .addEventListener("click", openLogoutConfirmation);

  document
    .getElementById("logoutHeaderBtn")
    .addEventListener("click", openLogoutConfirmation);

  document
    .getElementById("cancelLogout")
    .addEventListener("click", closeLogoutConfirmation);

  document
    .getElementById("closeLogoutConfirm")
    .addEventListener("click", closeLogoutConfirmation);

  document
    .getElementById("confirmLogout")
    .addEventListener("click", performLogout);

  document
    .getElementById("logoutConfirmModal")
    .addEventListener("click", (event) => {
      if (event.target.id === "logoutConfirmModal") {
        closeLogoutConfirmation();
      }
    });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeLogoutConfirmation();
    }
  });
  function enterGuestMode() {
    enterApp({
      id: null,
      display_name: "Guest",
      email: "",
      role: "guest",
      is_verified: false,
    });

    document.getElementById("sidebarUserName").textContent = "Guest session";

    document.getElementById("sidebarUserRole").textContent =
      "Temporary · Not saved";
  }

  document
    .getElementById("guestModeBtn")
    .addEventListener("click", enterGuestMode);
  async function initializeAuth() {
    const params = new URLSearchParams(window.location.search);
    const verificationToken = params.get("verify");
    const queryResetToken = params.get("reset");

    if (queryResetToken) {
      resetToken = queryResetToken;

      history.replaceState({}, "", window.location.pathname);

      setMode("reset-password", false);

      showMessage("Choose a new password for your account.");

      return;
    }

    if (verificationToken) {
      history.replaceState({}, "", window.location.pathname);

      try {
        const result = await api("/api/auth/verify-email", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            token: verificationToken,
          }),
        });

        setMode("login", false);

        showMessage(
          `${result.message || "Email verified."} Sign in to continue.`,
        );

        if (result.user?.role === "admin") {
          showMessage(
            "Email verified. Administrator access is enabled for this account. Please sign in.",
          );
        }
      } catch (error) {
        showMessage(error.message, true);
      }
    }

    try {
      const result = await api("/api/auth/me");

      if (result.user) {
        enterApp(result.user);
      } else {
        showLoginScreen();
      }
    } catch (error) {
      showLoginScreen();

      if (error.message && !error.message.toLowerCase().includes("sign in")) {
        showMessage(error.message, true);
      }
    }
  }

  initializeAuth();
})();
