// A fake Lever application form (apply A8), written from what Lever's public forms show: the
// resume first, whose upload is "parsed" into the name, e-mail, phone and current company with
// values that are not the person's; native selects; "Additional information" named only by its
// placeholder; an hCaptcha frame in the captcha variant (`<job>--captcha`). Invented company.
// It posts to the fake server's /_stub/submit/<name>, which records it, then shows Lever's
// confirmation. No copy of Lever's scripts.
(() => {
  const [, , , , org, job] = location.pathname.split("/");
  const variant = (job || "").split("--")[1] || "";
  const esc = (s) => s.replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);
  const star = ' <span class="req">*</span>';
  const field = (id, label, control, required) =>
    `<div class="field"><label for="${id}">${esc(label)}${required ? star : ""}</label>${control}</div>`;
  const text = (id, name, label, required) =>
    field(id, label, `<input id="${id}" name="${name}" type="text"${required ? ' aria-required="true"' : ""}>`, required);
  const select = (id, name, label, options, required) =>
    field(
      id,
      label,
      `<select id="${id}" name="${name}"${required ? ' aria-required="true"' : ""}><option value="">Select...</option>` +
        options.map((o) => `<option>${esc(o)}</option>`).join("") +
        "</select>",
      required,
    );
  const captcha =
    variant === "captcha"
      ? '<iframe title="Widget containing checkbox for hCaptcha security challenge" src="/forms/a8/hcaptcha.html" width="320" height="80"></iframe>'
      : "";
  document.getElementById("lever").innerHTML =
    "<h1>Applied AI Engineer</h1><p>Example Robotics, Paris. Invented posting.</p>" +
    '<form id="application-form" enctype="multipart/form-data" novalidate>' +
    "<h4>Submit your application</h4>" +
    field("resume", "Resume/CV", '<input id="resume" name="resume" type="file" aria-required="true"><span class="file-name"></span>', true) +
    text("name", "name", "Full name", true) +
    text("email", "email", "Email", true) +
    text("phone", "phone", "Phone", false) +
    text("location", "location", "Current location", false) +
    text("org", "org", "Current company", false) +
    "<h4>Links</h4>" +
    text("linkedin", "urls[LinkedIn]", "LinkedIn URL", false) +
    text("github", "urls[GitHub]", "GitHub URL", false) +
    text("portfolio", "urls[Portfolio]", "Portfolio URL", false) +
    "<h4>Additional questions</h4>" +
    select("sponsorship", "cards[sponsorship]", "Will you now or in the future require sponsorship?", ["Yes", "No"], true) +
    select("source", "cards[source]", "How did you hear about this job?", ["LinkedIn", "Company website", "Referral", "Other"], false) +
    "<h4>Additional information</h4>" +
    '<div class="field"><textarea name="comments" rows="6" placeholder="Add a cover letter or anything else you want to share."></textarea></div>' +
    "<h4>Equal employment opportunity</h4>" +
    select("gender", "eeo[gender]", "Gender", ["Male", "Female", "Decline to self-identify"], false) +
    captcha +
    '<div class="form-error" role="alert" hidden></div>' +
    '<button type="submit" id="btn-submit">Submit application</button></form>';

  const form = document.getElementById("application-form");
  const set = (id, value) => {
    const box = document.getElementById(id);
    box.value = value;
    box.dispatchEvent(new Event("input", { bubbles: true }));
  };
  document.getElementById("resume").addEventListener("change", (e) => {
    const file = e.target.files[0];
    e.target.nextElementSibling.textContent = file ? file.name : "";
    if (!file) return;
    // Lever's parse: what it read from the file, never the person's own values.
    setTimeout(() => {
      set("name", "Alex Exampel");
      set("email", "alex.exampel@parsed.example");
      set("phone", "+33 1 00 00 00 00");
      set("org", "Northwind Labs");
    }, 400);
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const banner = form.querySelector(".form-error");
    if (variant === "captcha") {
      banner.textContent = "Please complete the hCaptcha.";
      banner.hidden = false;
      return;
    }
    const r = await fetch(`/_stub/submit/lever-${org}-${job}`, { method: "POST", body: new FormData(form) });
    const answer = await r.json();
    if (answer.ok) {
      document.getElementById("lever").innerHTML =
        "<h2>Application submitted!</h2><p>Thanks for applying to Example Robotics.</p>";
    } else {
      banner.textContent = "Could not send the application.";
      banner.hidden = false;
    }
  });
})();
