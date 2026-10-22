const query = new URLSearchParams(location.search);
const variant = query.get("variant") || "review";
const signedIn = document.cookie.includes("example_profile_signed_in=yes");
if (variant === "signin" && !signedIn) { document.querySelector("#signin").style.display = "block"; document.querySelector("#profile").hidden = true; document.querySelector("#password").focus(); }
document.querySelector("#login").addEventListener("click", () => {document.cookie = "example_profile_signed_in=yes; Path=/; SameSite=Lax"; location.reload();});
for (const form of document.querySelectorAll("form")) {
 form.noValidate = true;
 const name = form.getAttribute("aria-label");
 const store = `example-profile:${name}`;
 const saved = JSON.parse(localStorage.getItem(store) || "{}");
 for (const input of form.elements) if (input.name && input.type !== "file" && input.name in saved) input.value = saved[input.name];
 const save = async () => {
  const values = Object.fromEntries([...new FormData(form)].map(([key, value]) => [key, value instanceof File ? value.name : value]));
  localStorage.setItem(store, JSON.stringify(values));
  const body = new FormData(form);
  body.set("section", name);
  await fetch("/_stub/submit/site-profile", {method:"POST", body});
  form.querySelector('[role="status"]').textContent = "Profile section saved.";
 };
 form.addEventListener("submit", (event) => {event.preventDefault(); void save();});
 if (variant.startsWith("autosave")) {
  form.querySelector('button[type="submit"]').remove();
  form.addEventListener("change", () => {void save();});
  const notice = document.createElement("p"); notice.textContent = "Changes are saved automatically"; form.prepend(notice);
 }
}

document.addEventListener("keydown", event => {if(event.key === "Enter" && document.querySelector("#signin").style.display === "block") document.querySelector("#login").click();});

if(variant === "autosave-race") document.querySelector('[name="employer"]').addEventListener("pointermove", event => {
 event.target.value="Site added this value";
 void fetch("/_stub/submit/site-profile-race", {method:"POST", body:new URLSearchParams({field:"employer",value:event.target.value})});
}, {once:true});
