import pytest

from jobpilot.core.protected import protected_category

PROTECTED = {
    "Are you legally authorized to work in the United States?": "work_authorization",
    "Will you now or in the future require sponsorship?": "sponsorship",
    "Gender": "eeo_demographic",
    "Are you Hispanic/Latino?": "eeo_demographic",
    "Protected veteran status": "veteran",
    "Do you have a disability?": "disability",
    "Desired salary": "salary",
    "What are your compensation expectations?": "salary",
    "Do you consent to a background check?": "background_check",
    "I certify that the information above is true": "legal",
    "work_authorization": "work_authorization",
}

SAFE = ["First name", "Email", "LinkedIn profile", "Why do you want to work here?", "Programming languages", "Design portfolio"]


@pytest.mark.parametrize("label,cat", PROTECTED.items())
def test_protected(label, cat):
    assert protected_category(label) == cat


@pytest.mark.parametrize("label", SAFE)
def test_safe(label):
    assert protected_category(label) is None
