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

# The same examples for Hindi and Hinglish users (PROMPTS.md §9: "in the user's language";
# the probe found English chips under a Hindi intro). Item names and codes stay as stored.
EXAMPLES_TRANSLATED: dict[Lang, dict[str, list[str]]] = {
    "hi": {
        "inventory_manager": [
            "132-1 में कौन से आइटम का स्टॉक शून्य है?",
            "पिछले हफ़्ते C84 FM assembly को क्या जारी हुआ?",
            "कौन से आइटम मिनिमम लेवल से नीचे हैं?",
            "132-2 में 3 AC Contactor जोड़ें",
        ],
        "procurement_manager": [
            "कौन से पर्चेज ऑर्डर देर से चल रहे हैं?",
            "इस महीने वेंडर के हिसाब से खर्च दिखाइए",
            "इस हफ़्ते मुझे क्या दोबारा ऑर्डर करना चाहिए?",
            "आज इन्वेंटरी ने क्या माँगा?",
        ],
        "owner": [
            "पिछली 3 तिमाहियों का मुनाफ़ा मार्जिन क्या था?",
            "हर महीने का खरीद खर्च दिखाइए",
            "कौन से ऑर्डर मेरी मंज़ूरी का इंतज़ार कर रहे हैं?",
            "पर्चेज ऑर्डर की प्रक्रिया समझाइए",
        ],
    },
    "hinglish": {
        "inventory_manager": [
            "132-1 mein kaun se items ka stock zero hai?",
            "Pichle hafte C84 FM assembly ko kya issue hua?",
            "Kaun se items minimum level se neeche hain?",
            "132-2 mein 3 AC Contactor add kar dijiye",
        ],
        "procurement_manager": [
            "Kaun se purchase orders late hain?",
            "Is mahine vendor wise kharcha dikhaiye",
            "Is hafte mujhe kya reorder karna chahiye?",
            "Aaj inventory ne kya request kiya?",
        ],
        "owner": [
            "Pichle 3 quarters ka profit margin kya tha?",
            "Mahine wise procurement kharcha dikhaiye",
            "Kaun se orders meri approval ka intezaar kar rahe hain?",
            "Purchase order ka process samjhaiye",
        ],
    },
}


def examples(role: str, lang: Lang) -> list[str]:
    return EXAMPLES[role] if lang == "en" else EXAMPLES_TRANSLATED[lang][role]


WELCOME: dict[Lang, str] = {
    "en": "You're welcome!",
    "hi": "आपका स्वागत है!",
    "hinglish": "Aapka swagat hai!",
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
