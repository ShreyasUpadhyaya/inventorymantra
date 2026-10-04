"""The "which measure?" question for "how are we doing?": fixed per role, not model-written.

The conversation probe (2026-10-04) showed the model offering measures the role can't see or
the data doesn't hold ("Stock value" to the Inventory Manager, "Sales" that then came back
"not recorded"). So the options come from this table: only measures the role's views answer,
each sent to the SQL step as one concrete question.
"""

from app.agent.prompts.core import Lang

# key: (labels per language, the question the SQL step answers)
MEASURES: dict[str, tuple[dict[Lang, str], str]] = {
    "sales": (
        {"en": "Sales and margin", "hi": "बिक्री और मार्जिन", "hinglish": "Sales aur margin"},
        "Revenue, gross profit and margin % by quarter this year",
    ),
    "spend": (
        {"en": "Spend", "hi": "खर्च", "hinglish": "Kharcha"},
        "Procurement spend by month this year",
    ),
    "overdue": (
        {"en": "Overdue orders", "hi": "देर से चल रहे ऑर्डर", "hinglish": "Late orders"},
        "Which purchase orders are overdue?",
    ),
    "low_stock": (
        {"en": "Low stock", "hi": "कम स्टॉक", "hinglish": "Kam stock"},
        "Which items are low on stock?",
    ),
    "stock": (
        {
            "en": "Stock by warehouse",
            "hi": "वेयरहाउस के हिसाब से स्टॉक",
            "hinglish": "Warehouse wise stock",
        },
        "How many items and how much quantity are in stock in each warehouse?",
    ),  # fmt: skip
    "issues": (
        {"en": "Issues this month", "hi": "इस महीने जारी माल", "hinglish": "Is mahine ke issues"},
        "How much was issued to each shop floor or team this month?",
    ),
}

ROLE_MEASURES: dict[str, list[str]] = {
    "inventory_manager": ["low_stock", "stock", "issues"],
    "procurement_manager": ["spend", "overdue", "low_stock"],
    "owner": ["sales", "spend", "overdue", "low_stock"],
}

MEASURE_QUESTION: dict[Lang, str] = {
    "en": "Which measure would you like to see?",
    "hi": "आप कौन सा माप देखना चाहेंगे?",
    "hinglish": "Aap kaun sa measure dekhna chahenge?",
}


def measure_options(role: str, lang: Lang) -> list[str]:
    return [MEASURES[key][0][lang] for key in ROLE_MEASURES[role]]


def measure_question(answer: str) -> str:
    """The concrete question for a chosen option (any language); free text stays as typed."""
    for labels, question in MEASURES.values():
        if any(answer.strip().casefold() == label.casefold() for label in labels.values()):
            return question
    return answer
