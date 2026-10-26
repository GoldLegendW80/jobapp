"""The protected profile field policy is code, including near neighbours and French labels."""

import pytest

from app.apply import profile_refusals as policy


@pytest.mark.parametrize(
    "label",
    [
        "Email",
        "E-mail",
        "Email address",
        "Adresse e-mail",
        "Courriel",
        "Change email",
        "Work email",
        "Password",
        "Confirm password",
        "Mot de passe",
        "Security settings",
        "Two factor authentication",
        "Phone verification",
        "Code de vérification",
        "Verification code",
        "Account recovery",
        "Privacy",
        "Visibility",
        "Profile visibility",
        "Confidentialité",
        "Open to work",
        "Recruiter contact",
        "Contact des recruteurs",
        "Notifications",
        "Email notifications",
        "Marketing",
        "Newsletter",
        "Talent community",
        "Talent pool",
        "Consent",
        "I agree to the terms",
        "Conditions générales",
        "Accept terms",
        "Delete profile",
        "Withdraw",
        "Supprimer mon compte",
        "Background check consent",
        "Reference check consent",
        "Paramètres de sécurité",
        "Public profile",
        "Disponibilité aux recruteurs",
    ],
)
@pytest.mark.parametrize(
    "decorate", [str, str.upper, lambda s: f"  {s} *  ", lambda s: f"{s} (required)"]
)
def test_protected_fields_never_accept_values(label, decorate):
    assert policy.refusal(decorate(label), "text") is not None


@pytest.mark.parametrize(
    "label",
    [
        "Full name",
        "First name",
        "Last name",
        "Phone",
        "City",
        "Country",
        "Website",
        "LinkedIn profile",
        "Summary",
        "Headline",
        "Employer",
        "Company",
        "Job title",
        "Start date",
        "End date",
        "School",
        "Degree",
        "Skills",
        "Languages",
        "Language",
        "Resume",
        "CV",
        "Organisation",
        "Poste",
        "Ville",
        "Pays",
        "Compétences",
        "Formation",
        "Diplôme",
        "Téléphone",
        "Nom complet",
        "Prénom",
        "Nom",
        "Professional experience",
        "Privacy engineer title",
        "Security analyst title",
        "Marketing job title",
    ],
)
def test_safe_neighbours_are_not_classified_as_protected(label):
    assert policy.refusal(label, "text") is None


@pytest.mark.parametrize("kind", ["checkbox", "password", "hidden"])
def test_unconditional_protected_control_kinds(kind):
    assert policy.refusal("Full name", kind)
