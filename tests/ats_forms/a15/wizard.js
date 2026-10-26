/* Invented ATS mechanics only. No credentials, company script or external request. */
const kind = document.body.dataset.kind;
const query = new URLSearchParams(location.search);
const variant = query.get("variant") || (kind === "workday" ? "signin" : "");
const job = query.get("job") || "1001";
const mount = document.querySelector("#application");
const storage = `a15:${kind}:${job}`;
const values = JSON.parse(localStorage.getItem(storage) || "{}");
const uploads = {};
let step = Number(query.get("step") || 0);
const signed = () => document.cookie.includes(`a15_${kind}=signed`);
const manual = query.get("apply") === "manual";
if (kind === "workday") {
  window.fixtureMemory = new Uint8Array(48 * 1024 * 1024);
  window.fixtureMemory.fill(17);
}
function persist() { localStorage.setItem(storage, JSON.stringify(values)); }
function label(text, input) {
  const group = document.createElement("div"); group.className = "field";
  const label = document.createElement("label"); label.htmlFor = input.id; label.textContent = text;
  group.append(label, input); return group;
}
function textField(form, name, caption, required = false) {
  const input = document.createElement("input"); input.id = `${kind}_${step}_${name}`;
  input.name = name; input.required = required; input.value = values[name] || "";
  input.addEventListener("input", () => {values[name] = input.value; persist();});
  form.append(label(caption + (required ? " *" : ""), input));
}
function fileField(form, name, caption, required = false) {
  const input = document.createElement("input"); input.type = "file";
  input.id = `${kind}_${step}_${name}`; input.name = name; input.accept = "application/pdf";
  input.required = required;
  const group = label(caption + (required ? " *" : ""), input);
  const shown = document.createElement("p"); shown.textContent = values[name] || "";
  input.addEventListener("change", () => {
    const chosen = input.files[0]; if (!chosen) return;
    uploads[name] = chosen; values[name] = chosen.name; shown.textContent = chosen.name; persist();
    if (kind === "google_careers" && name === "resume") {
      values.first_name = "Parser guessed this";
      const first = form.elements.namedItem("first_name"); if (first) first.value = values.first_name;
    }
  });
  if (kind === "google_careers") {
    input.hidden = true;
    const choose = document.createElement("button"); choose.type = "button";
    choose.textContent = "Browse"; choose.onclick = () => input.click(); group.append(choose);
  }
  group.append(shown); form.append(group);
}
function login() {
  mount.innerHTML = '<h2>Sign in</h2><p>The person creates this invented tenant account here.</p><form id="signin"><label>Password <input type="password" autocomplete="off" required autofocus></label><button type="submit">Create account</button></form>';
  const form = mount.querySelector("form");
  form.querySelector("input").focus();
  form.onsubmit = event => {
    event.preventDefault(); form.querySelector("input").value = "";
    document.cookie = `a15_${kind}=signed; Path=/; SameSite=Lax`;
    render();
  };
}
function render() {
  mount.replaceChildren();
  if ((kind === "workday" || kind === "google_careers") && !manual) {
    const apply = document.createElement("a"); apply.textContent = "Apply";
    const target = new URL(location.href); target.searchParams.set("apply", "manual");
    apply.href = target.href; mount.append(apply); return;
  }
  if ((variant === "signin" || variant === "signedout") && !signed()) { login(); return; }
  if (variant === "incomplete") {
    const wall = document.createElement("h2");
    wall.textContent = kind === "google_careers" ? "Complete your Careers Profile" : "Complete your candidate profile";
    mount.append(wall); return;
  }
  if (kind === "vie" && variant === "external") {
    const link = document.createElement("a"); link.textContent = "Apply on company site";
    link.href = "/greenhouse/northwind-labs/jobs/4012348"; mount.append(link); return;
  }
  // A later-step challenge is cleared only by the person's live-view input. The solved flag
  // survives the approved replay; neither the code nor a captcha response enters form answers.
  if (kind === "workday" && step === 1 && ["captcha", "email_code"].includes(variant)
      && !localStorage.getItem(storage + ":person-cleared")) {
    const challenge = document.createElement("section");
    challenge.setAttribute("aria-label", "Person verification");
    if (variant === "captcha") {
      challenge.innerHTML = '<label><input id="person-challenge" type="checkbox">I am not a robot</label>';
    } else {
      challenge.innerHTML = '<label for="person-challenge">Verification code</label><input id="person-challenge" autocomplete="off">';
    }
    const proceed = document.createElement("button"); proceed.type = "button";
    proceed.id = "person-continue"; proceed.textContent = "Verify and continue";
    proceed.onclick = () => {
      const field = challenge.querySelector("input");
      if (variant === "captcha" ? field.checked : field.value === "482615") {
        field.value = ""; localStorage.setItem(storage + ":person-cleared", "yes"); render();
      }
    };
    challenge.append(proceed); mount.append(challenge);
    return;
  }
  const form = document.createElement("form"); form.setAttribute("aria-label", "Application");
  form.noValidate = true; mount.append(form);
  if (kind !== "workday" || step === 0) {
    textField(form, "first_name", "First name", true);
    textField(form, "last_name", "Last name", true);
    textField(form, "email", "Email", true);
    if (variant === "required") textField(form, "codename", "Your project codename", true);
  }
  if (kind !== "workday" || step === 1) {
    fileField(form, "resume", "Resume", true); fileField(form, "letter", "Cover letter");
    if (kind === "vie") textField(form, "motivation", "Motivation message", true);
  }
  if (kind === "workday" && step === 2) {
    const summary = document.createElement("section"); summary.setAttribute("aria-label", "Application summary");
    for (const [name, caption] of [["first_name", "First name"], ["last_name", "Last name"], ["email", "Email"], ["codename", "Your project codename"], ["resume", "Resume"], ["letter", "Cover letter"]]) {
      if (!values[name]) continue;
      const group = document.createElement("div"); group.setAttribute("role", "group");
      group.setAttribute("aria-label", caption); group.textContent = values[name]; summary.append(group);
    }
    form.append(summary);
  }
  const next = kind === "workday" && step < 2;
  const button = document.createElement("button"); button.type = next ? "button" : "submit";
  button.textContent = next ? "Save and Continue" : "Submit application"; form.append(button);
  if (variant === "required" && step === 0) {
    const check = () => {button.disabled = !form.elements.namedItem("codename").value.trim();};
    form.addEventListener("input", check); check();
  }
  if (next) button.onclick = () => {
    if (!form.reportValidity()) return;
    step += 1; const address = new URL(location.href); address.searchParams.set("step", String(step));
    history.replaceState(null, "", address);
    if (variant === "expire" && step === 1 && !localStorage.getItem(storage + ":expired")) {
      localStorage.setItem(storage + ":expired", "yes");
      document.cookie = `a15_${kind}=; Path=/; Max-Age=0`; login(); return;
    }
    render();
  };
  form.onsubmit = async event => {
    event.preventDefault();
    const body = new FormData(); for (const [name, value] of Object.entries(values)) body.append(name, value);
    for (const [name, file] of Object.entries(uploads)) body.set(name, file);
    body.set("_gate_target", kind); body.set("_job", job);
    const result = await fetch(`/_stub/submit/a15-${kind}`, {method: "POST", body});
    if (result.ok) mount.innerHTML = "<h2>Thank you for applying</h2>";
  };
}
render();
