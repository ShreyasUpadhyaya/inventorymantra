"""Help-mode example questions per role: verbatim from docs/PROMPTS.md §9."""

from app.agent.prompts.core import Lang

EXAMPLES = {
    "inventory_manager": [
        "Which items are at zero stock in 132-1?",
        "What was issued to C84 FM assembly last week?",
        "कौन से आइटम मिनिमम लेवल से नीचे हैं?",
        "Add 3 AC Contactor to 132-2",
    ],
    "procurement_manager": [
        "Which purchase orders are overdue?",
        "Show spend by vendor this month",
        "What should I reorder this week?",
        "What did inventory request today?",
    ],
    "owner": [
        "What were our profit margins for the last 3 quarters?",
        "Show monthly procurement spend trend",
        "Which orders are waiting for my approval?",
        "Explain the purchase order lifecycle",
    ],
}

HELP_INTRO: dict[Lang, str] = {
    "en": "Here are some things you can ask me:",
    "hi": "आप मुझसे ये सवाल पूछ सकते हैं:",
    "hinglish": "Aap mujhse ye sawaal pooch sakte hain:",
}

GREETING: dict[Lang, str] = {
    "en": "Hello! I'm InventoryMantra.",
    "hi": "नमस्ते! मैं InventoryMantra हूँ।",
    "hinglish": "Namaste! Main InventoryMantra hoon.",
}
