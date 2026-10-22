"""LinkedIn Edit dialogs. Exact section entry points, never a Save or a setting."""

from .known import Entry

KIND = "linkedin"
ENTRIES = (
    Entry(
        "contact",
        "Contact",
        ("Edit intro", "Modifier l'introduction", "Edit contact info"),
        ("Edit intro", "Edit contact info", "Modifier l'introduction"),
    ),
    Entry(
        "summary",
        "Summary",
        ("Edit about", "Edit summary", "Modifier les infos"),
        ("Edit about", "Edit summary", "Modifier les infos"),
    ),
    Entry(
        "experience",
        "Experience 1",
        ("Edit experience", "Modifier l'expérience"),
        ("Edit experience", "Modifier l'expérience"),
    ),
    Entry(
        "education",
        "Education 1",
        ("Edit education", "Modifier la formation"),
        ("Edit education", "Modifier la formation"),
    ),
    Entry(
        "skills",
        "Skills",
        ("Edit skills", "Modifier les compétences"),
        ("Edit skills", "Modifier les compétences"),
    ),
    Entry(
        "languages",
        "Languages",
        ("Edit languages", "Modifier les langues"),
        ("Edit languages", "Modifier les langues"),
    ),
)
