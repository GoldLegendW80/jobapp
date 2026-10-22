"""Workday candidate navigation, bounded to the known profile pages."""

from .known import Entry

KIND = "workday"
ENTRIES = (
    Entry(
        "contact",
        "Contact",
        ("My Information", "Mes informations", "Edit contact information"),
        ("My Information", "Contact Information", "Mes informations"),
        True,
    ),
    Entry(
        "experience",
        "Experience 1",
        ("My Experience", "Work Experience", "Edit work experience", "Mon expérience"),
        ("My Experience", "Work Experience", "Mon expérience", "Edit work experience"),
        True,
    ),
    Entry(
        "education",
        "Education 1",
        ("Education", "Edit education", "Formation"),
        ("Education", "Edit education", "Formation"),
        True,
    ),
    Entry(
        "skills",
        "Skills",
        ("Skills", "Edit skills", "Compétences"),
        ("Skills", "Edit skills", "Compétences"),
        True,
    ),
    Entry(
        "languages",
        "Languages",
        ("Languages", "Edit languages", "Langues"),
        ("Languages", "Edit languages", "Langues"),
        True,
    ),
)
