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
    ("MSc Pro", "ORBITECH – Open Research Institute of Technology 2022 – 2025. Specialization: AI"),
    ("Bachelor of Computer Science", "Université de Kervan Haute-Loire 2018 – 2022"),
    ("Scientific Baccalaureate", "Lycée Clément Arnaud, Angers. Option ISN, Mention AB"),
]
LANGUAGES = [("French", "Mother tongue"), ("English", "Advanced (C1)"), ("Spanish", "Intermediate")]
EMPLOYERS = {
    "lumetra": ("Lumetra Group", "September 2025 – Present"),
    "noxtech": ("NoxTechSolutions", "August 2024 – Present"),
    "santeva": ("SANTEVA", "January 2023 – August 2025"),
    "corelis": ("Corelis", "June 2022 – August 2022"),
    "studio17": ("Studio Marée 17", "April 2022 – June 2022"),
}
