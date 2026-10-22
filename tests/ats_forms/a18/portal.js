const query = new URLSearchParams(location.search);
const variant = query.get("variant") || "review";
const signedIn = sessionStorage.getItem("example-profile-signed-in");
if (variant === "signin" && !signedIn) { document.querySelector("#signin").style.display = "block"; document.querySelector("#profile").hidden = true; }
document.querySelector("#login").addEventListener("click", () => {sessionStorage.setItem("example-profile-signed-in", "yes"); document.querySelector("#signin").style.display = "none"; document.querySelector("#profile").hidden = false;});
for (const form of document.querySelectorAll("form")) {
 form.noValidate = true;
 const name = form.getAttribute("aria-label");
 const store = `example-profile:${name}`;
 const saved = JSON.parse(sessionStorage.getItem(store) || "{}");
 for (const input of form.elements) if (input.name && input.type !== "file" && input.name in saved) input.value = saved[input.name];
 const save = async () => {
  const values = Object.fromEntries([...new FormData(form)].map(([key, value]) => [key, value instanceof File ? value.name : value]));
  sessionStorage.setItem(store, JSON.stringify(values));
  const body = new FormData(form);
  body.set("section", name);
  await fetch("/_stub/submit/site-profile", {method:"POST", body});
  form.querySelector('[role="status"]').textContent = "Profile section saved.";
 };
 form.addEventListener("submit", (event) => {event.preventDefault(); void save();});
 if (variant === "autosave") {
  form.querySelector('button[type="submit"]').remove();
  form.addEventListener("change", () => {void save();});
  const notice = document.createElement("p"); notice.textContent = "Changes are saved automatically"; form.prepend(notice);
 }
}
