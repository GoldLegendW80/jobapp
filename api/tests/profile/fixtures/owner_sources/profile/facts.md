# VERIFIED FACT BASE — Lucien-Ambroise Quillard

This is the **only** permitted source of claims. Every line of every tailored CV must
trace to something in this file. Sources: the LinkedIn PDF export (authoritative for dates,
titles, employers, education), the existing English CV, `profile.md` and
`projects-deep-dive.md` (machine-derived audit of his actual repositories).
Everything in this file is invented: the person, the employers, the schools and the projects.

## HARD RULES FOR ANY CV YOU WRITE
1. **Never invent.** No metric, percentage, team size, user count, revenue figure,
   award, certification, publication, or client name that is not below. If a job spec
   begs for a number you do not have, write the achievement without a number.
2. **Never change dates, employers, or job titles.** They are legally checkable.
3. **Reframing is allowed and expected. Fabrication is not.** You may re-describe real
   work in the target role's vocabulary, reorder it, and drop what is irrelevant —
   but the underlying fact must exist below.
4. **Lumetra work is under NDA-ish constraints**: repositories are private. Describe
   *what* was built and *how*, never internal metrics or unreleased product names
   beyond what the profile already records ("claims desk", "triage", "matching").
5. **Do not claim LLM fine-tuning.** He has LoRA training on a rented GPU (`lora_lab`)
   and OCR model benchmarking. That is adjacent, real, and must be described as what it is.
6. **Do not claim a PhD, publications, or open-source maintainership.** His public
   GitHub (`github.com/e2e-lquillard`) holds the form-reader projects.
7. English is written as **Advanced (C1)** on his own CV; LinkedIn says "Professional
   Working". Use the CV's wording. Spanish: CV says Intermediate, LinkedIn "Limited
   Working" — use **Intermediate**.

## IDENTITY
- **Lucien-Ambroise Quillard** — Nantes, Pays de la Loire, France
- +33 6 39 98 42 17 · e2e-lucien.quillard@example.org
- linkedin.com/in/e2e-lucien-quillard · noxtechsolutions.example
- Current title: **GenAI Engineer**

## EMPLOYMENT (LinkedIn — authoritative, do not alter)
| Employer | Role | Dates | Location |
|---|---|---|---|
| **Lumetra Group** | GenAI Engineer | September 2025 – Present | Nantes |
| **NoxTechSolutions** | Auto-entrepreneur (self-employed) | August 2024 – Present | Nantes |
| **SANTEVA** | Full-stack Developer (apprenticeship) | January 2023 – August 2025 | Nantes |
| Corelis | Technicien informatique | June 2022 – August 2022 (3 mo) | Nantes |
| Studio Marée 17 | Développeur applications | April 2022 – June 2022 (3 mo) | Royan |
| LUDIKA | Game master | July 2021 – August 2021 (2 mo) | Angers |
| PIXEL SURFSHOP | Développement mobile | April 2021 – June 2021 (3 mo) | Angers |
| LA FOURNÉE | Vendeur caissier (student job, ~8h/week) | April 2019 – March 2021 | Angers |

**Experience arithmetic (state honestly if asked):** first paid development work
April 2021; continuous professional software engineering since **January 2023**
(SANTEVA apprenticeship, 2 yrs 8 mo) overlapping self-employment from August 2024;
GenAI engineering as a full-time specialism since **September 2025**.
That is ~3.5 years professional software engineering. **Do not round these up.**

Short pre-2023 roles worth keeping only where relevant:
- **Corelis** — wrote training material for a **real-time scheduling platform**.
- **Studio Marée 17** — shipped a **React Native** web + mobile app for a sailing school.
- PIXEL SURFSHOP — mobile development.
- LUDIKA / LA FOURNÉE — non-technical, omit from engineering CVs.

## EDUCATION (LinkedIn — authoritative)
- **ORBITECH – Open Research Institute of Technology**, Nantes — *MSC Pro, Transformation
  Digitale et Innovation Technologique (Informatique)*, **September 2022 – July 2025**.
  ⚠️ **COMPLETED.** The old CV says "2022 – 2025 (In progress)" and
  "Specialization: AI" — the correct status is completed July 2025.
- **Université de Kervan Haute-Loire** — *Licence (Bachelor), Informatique*, 2018 – 2022.
- **Lycée Clément Arnaud, Angers** — *Baccalauréat Scientifique, option ISN*, 2016 – 2018.
  (The old CV adds "Mention AB"; both may be stated.)

## LANGUAGES
French — mother tongue · English — Advanced (C1) · Spanish — Intermediate

## WHAT HE ACTUALLY BUILT (from the repository audit — this is the gold)
### Lumetra Group — GenAI Engineer (Sept 2025 – present)

**`claims-desk` — flagship, production. Lead developer.**
An agentic back-office for the insurance claims desk: a handler describes a claim in
natural language, the agent finds the policy, proposes the next step and waits for a
human sign-off before anything is written.
- `agent/` — Python 3.12, **LangGraph**, one agent with typed state
- `desk/` — **React 19 + Vite + TypeScript**, TanStack Router/Query
- Deployed on **AWS ECS Fargate + Aurora Serverless v2 + Redis** behind an ALB

**The agent engineering that makes it notable:**
- **Human-in-the-loop** via LangGraph `interrupt()` + checkpointing.
- **ID safety**: IDs read from the state, never from model output.
- **Evaluation suites**: tool arguments, trajectories, guardrails, adversarial personas.

**`fraud-notes` (his).** A batch pipeline over invented claim notes that extracts
suspicious patterns with evidence, to draft detection rules.

### NoxTechSolutions — Auto-entrepreneur (Aug 2024 – present)

Generative-AI consulting and custom web development. Public GitHub
(`github.com/e2e-lquillard`):
- `form-reader-api` — FastAPI + PostgreSQL + Alembic, **vision model** extraction of
  scanned forms into a structured schema.
- `form-reader-front` — React 19 + Vite + shadcn, full CRUD, Excel export.

### SANTEVA — Full-stack Developer, apprenticeship (Jan 2023 – Aug 2025)

Private clinic network. Design and development of internal scheduling applications.
(**Regulated sector, patient data.**)

## TOOLCHAIN (evidenced by what is installed and used daily)
Python (`uv`), TypeScript, C#/.NET, JavaScript ·
LangGraph, LangChain, LangSmith, **MCP**, LiteLLM, Ollama (local inference) ·
React 19, Vite, TanStack, shadcn, React Native ·
FastAPI, gRPC, PostgreSQL, MongoDB, Redis ·
AWS (ECS Fargate, Aurora), Terraform, Docker, CI/CD (GitHub Actions), pre-commit ·
Playwright, pytest, Vitest

## POSITIONING (added 29 Sept 2026, framing confirmed by Lucien)
Lead with the big picture, then prove it: the engineer who put agents to work on the
claims desk of a regulated insurer, from the business problem to daily production use.

### APPROVED WORDING (29 Sept 2026, reviewed by Lucien; reuse verbatim where it fits)

- **Claims bullet:** "Core engineer of the claims desk agent: natural-language intent,
  human sign-off, one action per claim."
- **Summary opener:** "Puts AI to work across a regulated business."

## PRACTICALITIES FOR APPLICATION FORMS (confirmed by Lucien, Sept 2026)
- **Citizenship and work authorization:** French citizen. Authorized to work in France and the EU;
  no visa sponsorship needed in France. For Switzerland and the UK answer honestly: EU citizen,
  permit needed but routine for EU nationals (Switzerland), sponsorship would be needed (UK).
- **Location stance:** fully remote, hybrid in France, or hybrid abroad are all acceptable ("it
  doesn't matter"). Preferred office: Paris. Also acceptable: Zürich, London, Singapore. Based in
  Nantes; willing to relocate or commute to Paris; three office days a week in Paris is fine.
- **Notice and start:** standard French notice period, 1 to 3 months. Earliest start given on forms
  in September 2026: December 2026 (date fields: 2026-12-01). Monthly travel is fine; 25 to 50%
  travel was accepted on an earlier form.
- **Salary expectations:** never invent a number. Leave the field for Lucien, or write "open to
  discussion against the band for the role" only if he says so.
- **Demographic and voluntary self-identification questions:** leave blank or "prefer not to answer";
  Lucien fills them himself if he wants to.
- **Motivation, in Lucien's own words (earlier form, 1 Sept 2026, may be adapted):** the company is
  a market leader whose products he has used for several years; he wants to bring his knowledge to
  help the company move forward. Coding agents are central to how he builds every day.
- **How did you hear about us:** the company's own careers page or job board, found while
  researching forward deployed and applied AI roles; LinkedIn when the posting came from there.
- **Languages:** French native, English C1, Spanish intermediate.
- **Who submits:** Lucien clicks Submit himself unless he explicitly says otherwise for a given run.

### WORDING LEVEL (added 6 October 2026, stated by Lucien)

Outward material speaks in skills, never in inventories of what he produced. "Designs and runs LLM
evaluations across products" is right; "implemented three evaluation suites" is wrong.
