// The behaviour of the fake application forms (tests/ats_forms_stub.py): written from what the
// public forms of Ashby and Greenhouse do, not copied from their scripts. It never calls a real
// host, and its "invisible check" is only the signals it sends to the stub: the milliseconds
// between the last input and the submit, and navigator.webdriver.
(() => {
  const loadedAt = Date.now();
  let lastInput = null;
  const touch = () => {
    lastInput = Date.now();
  };
  document.addEventListener("input", touch, true);
  document.addEventListener("change", touch, true);
  document.addEventListener("keydown", touch, true);
  document.addEventListener(
    "click",
    (e) => {
      if (e.target.closest("button[data-option], [role=option], .captcha-tile")) touch();
    },
    true,
  );

  const form = document.querySelector("form[data-stub-form]");
  if (!form) return;

  // Ashby yes or no groups: button[data-option=yes|no] in div.ashby-application-form-input-yesno.
  form.querySelectorAll(".ashby-application-form-input-yesno").forEach((group) => {
    const hidden = group.querySelector("input[type=hidden]");
    group.querySelectorAll("button[data-option]").forEach((button) => {
      button.addEventListener("click", () => {
        hidden.value = button.dataset.option === "yes" ? "Yes" : "No";
        group.querySelectorAll("button[data-option]").forEach((other) => {
          other.setAttribute("aria-pressed", String(other === button));
          other.classList.toggle("_active", other === button);
        });
        hidden.dispatchEvent(new Event("input", { bubbles: true }));
      });
    });
  });

  // The location typeahead: the options come from the stub, a free text is not a choice.
  form.querySelectorAll("input[data-typeahead]").forEach((input) => {
    const list = document.createElement("ul");
    list.setAttribute("role", "listbox");
    list.hidden = true;
    input.insertAdjacentElement("afterend", list);
    input.addEventListener("input", async () => {
      if (input.value.trim().length < 2) {
        list.hidden = true;
        return;
      }
      const r = await fetch(`${input.dataset.typeahead}?q=${encodeURIComponent(input.value)}`);
      const options = await r.json();
      list.replaceChildren(
        ...options.map((text) => {
          const li = document.createElement("li");
          li.setAttribute("role", "option");
          li.textContent = text;
          li.addEventListener("mousedown", (e) => {
            e.preventDefault();
            input.value = text;
            list.hidden = true;
            input.dispatchEvent(new Event("change", { bubbles: true }));
          });
          return li;
        }),
      );
      list.hidden = options.length === 0;
    });
  });

  // Greenhouse selects are react-select comboboxes: the menu exists only while it is open, type
  // to filter, then click the option.
  form.querySelectorAll(".select").forEach((select) => {
    const control = select.querySelector(".select__control");
    const input = select.querySelector(".select__input");
    const hidden = select.querySelector("input[type=hidden]");
    const container = select.querySelector(".select__value-container");
    const options = JSON.parse(select.dataset.options);
    let menu = null;
    const close = () => {
      if (menu) menu.remove();
      menu = null;
      input.setAttribute("aria-expanded", "false");
    };
    const choose = (text) => {
      hidden.value = text;
      let single = container.querySelector(".select__single-value");
      if (!single) {
        single = document.createElement("div");
        single.className = "select__single-value";
        container.prepend(single);
      }
      single.textContent = text;
      const placeholder = container.querySelector(".select__placeholder");
      if (placeholder) placeholder.hidden = true;
      input.value = "";
      close();
      hidden.dispatchEvent(new Event("input", { bubbles: true }));
    };
    const open = () => {
      const typed = input.value.trim().toLowerCase();
      const shown = options.filter((text) => text.toLowerCase().includes(typed));
      close();
      menu = document.createElement("div");
      menu.className = "select__menu";
      shown.forEach((text, i) => {
        const option = document.createElement("div");
        option.className = "select__option";
        option.setAttribute("role", "option");
        option.id = `${input.id}-option-${i}`;
        option.textContent = text;
        option.addEventListener("mousedown", (e) => e.preventDefault());
        option.addEventListener("click", () => choose(text));
        menu.append(option);
      });
      if (shown.length === 0) menu.textContent = "No options";
      select.append(menu);
      input.setAttribute("aria-expanded", "true");
    };
    control.addEventListener("mousedown", (e) => {
      if (e.target !== input) e.preventDefault();
      input.focus();
      if (menu) close();
      else open();
    });
    input.addEventListener("input", open);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && menu && menu.firstElementChild?.className === "select__option") {
        e.preventDefault();
        choose(menu.firstElementChild.textContent);
      }
      if (e.key === "ArrowDown" && !menu) open();
      if (e.key === "Escape") close();
    });
    input.addEventListener("blur", close);
  });

  form.querySelectorAll("input[type=file]").forEach((input) => {
    input.addEventListener("change", () => {
      const out = input.parentElement.querySelector(".file-name");
      if (out) out.textContent = Array.from(input.files, (f) => f.name).join(", ");
    });
  });

  // The visible captcha stand-in: a box, then a grid, solved by the three right tiles. It never
  // loads a script from Google or anyone else.
  const captcha = form.querySelector(".captcha-challenge");
  if (captcha) {
    const token = captcha.querySelector("input[type=hidden]");
    const grid = captcha.querySelector(".captcha-grid");
    captcha.querySelector(".captcha-box").addEventListener("click", () => {
      grid.hidden = false;
    });
    grid.querySelectorAll(".captcha-tile").forEach((tile) =>
      tile.addEventListener("click", () => {
        tile.classList.toggle("_picked");
        tile.setAttribute("aria-pressed", String(tile.classList.contains("_picked")));
      }),
    );
    grid.querySelector(".captcha-verify").addEventListener("click", () => {
      const picked = Array.from(grid.querySelectorAll("._picked"), (t) => t.dataset.n).join(",");
      const solved = picked === "2,5,7";
      token.value = solved ? "solved" : "";
      captcha.querySelector(".captcha-state").textContent = solved ? "Verified" : "Try again";
    });
  }

  // A form that changes after load: the first input adds a required question.
  if (form.dataset.morph) {
    form.addEventListener(
      "input",
      () => {
        if (form.dataset.morphed) return;
        form.dataset.morphed = "1";
        const extra = document.createElement("div");
        extra.className = "field";
        extra.dataset.field = "start_date";
        extra.innerHTML =
          '<label for="start_date">Earliest start date <span class="req">*</span></label>' +
          '<input id="start_date" name="start_date" type="text" aria-required="true">' +
          '<div class="field-error" data-error-for="start_date" hidden></div>';
        form.querySelector("[data-submit]").before(extra);
      },
      true,
    );
  }

  const banner = form.querySelector(".form-error");
  const clear = () => {
    banner.hidden = true;
    form.querySelectorAll("[data-error-for]").forEach((e) => {
      e.hidden = true;
      e.textContent = "";
    });
    form.querySelectorAll("[aria-invalid]").forEach((e) => e.removeAttribute("aria-invalid"));
  };
  const send = async (data) => {
    clear();
    data.set("_signal_dwell_ms", String(Date.now() - (lastInput ?? loadedAt)));
    data.set("_signal_webdriver", navigator.webdriver ? "1" : "0");
    const r = await fetch(form.dataset.action, { method: "POST", body: data });
    const answer = await r.json();
    if (answer.ok) {
      location.assign(answer.redirect);
      return;
    }
    if (answer.step === "code") {
      form.dataset.submission = answer.submission;
      document.getElementById("code-step").hidden = false;
      return;
    }
    Object.entries(answer.errors || {}).forEach(([name, message]) => {
      const slot = form.querySelector(`[data-error-for="${name}"]`);
      if (slot) {
        slot.textContent = message;
        slot.hidden = false;
      }
      const control = form.querySelector(`[name="${name}"]`);
      if (control) control.setAttribute("aria-invalid", "true");
    });
    banner.textContent = answer.message || "Fix the highlighted fields.";
    banner.hidden = false;
  };
  form.querySelector("[data-submit]").addEventListener("click", () => send(new FormData(form)));
  const codeButton = document.querySelector("[data-submit-code]");
  if (codeButton) {
    codeButton.addEventListener("click", () => {
      const data = new FormData();
      data.set("_step", "code");
      data.set("_submission", form.dataset.submission);
      data.set("security_code", document.getElementById("security_code").value);
      return send(data);
    });
  }
})();
