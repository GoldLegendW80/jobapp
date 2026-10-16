"""The CV constants of an invented person, read with ast by the importer: the source of
tests/profile/fixtures/owner_export.json with ../profile/facts.md."""

NAME = "LUCIEN-AMBROISE QUILLARD"
CONTACT_LEFT = [
    (
        "https://www.linkedin.com/in/e2e-lucien-quillard/",
        "linkedin.com/in/e2e-lucien-quillard",
    ),
    ("https://noxtechsolutions.example/", "noxtechsolutions.example"),
    ("https://github.com/e2e-lquillard", "github.com/e2e-lquillard"),
]
CONTACT_RIGHT = ["+33 6 39 98 42 17", "e2e-lucien.quillard@example.org", "Nantes, France"]
EDUCATION = [
    ("Master of Engineering", "ORBITECH Institute of Technology 2020 – 2024. Major: Data Science"),
    ("Bachelor of Mathematics", "Université de Kervan Haute-Loire 2017 – 2020"),
    ("Baccalauréat Général", "Lycée Clément Arnaud, Angers. Mathematics and physics, Mention Bien"),
]
LANGUAGES = [
    ("French", "Mother tongue"),
    ("English", "Fluent (C1)"),
    ("German", "Intermediate (B1)"),
]
EMPLOYERS = {
    "lumetra": ("Lumetra Group", "March 2025 – Present"),
    "noxtech": ("NoxTechSolutions", "May 2023 – Present"),
    "olmea": ("OLMEA", "September 2021 – June 2024"),
    "velmor": ("Velmor", "June 2021 – August 2021"),
    "studio17": ("Studio Marée 17", "February 2021 – May 2021"),
}
