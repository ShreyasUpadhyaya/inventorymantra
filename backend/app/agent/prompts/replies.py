"""Fixed reply texts (no model involved). Formal "aap" in Hindi/Hinglish (D-020).

These are UI strings, not model instructions, so they are not part of the approved prompt;
failure texts follow docs/QUALITY_AND_EVALS.md §5.
"""

from app.agent.prompts.core import Lang

FAILURE: dict[str, dict[Lang, str]] = {
    "sql_failed": {
        "en": "I couldn't answer that reliably. Please try rephrasing your question.",
        "hi": "मैं इसका भरोसेमंद उत्तर नहीं दे सका। कृपया अपना सवाल दूसरे शब्दों में पूछें।",
        "hinglish": "Main iska bharosemand jawab nahi de saka. Kripya apna sawaal doosre shabdon mein poochhiye.",
    },
    "timeout": {
        "en": "That query took too long. Please narrow the date range or the item.",
        "hi": "यह क्वेरी बहुत लंबी चली। कृपया तारीख़ की सीमा या आइटम को सीमित करें।",
        "hinglish": "Ye query bahut lambi chali. Kripya date range ya item ko seemit kijiye.",
    },
    "unanswerable": {
        "en": "That isn't recorded in the data available to you.",
        "hi": "यह जानकारी आपके लिए उपलब्ध डेटा में दर्ज नहीं है।",
        "hinglish": "Ye jaankari aapke liye uplabdh data mein darj nahi hai.",
    },
    "unavailable": {
        "en": "The assistant is temporarily unavailable. Dashboards still work.",
        "hi": "सहायक अभी उपलब्ध नहीं है। डैशबोर्ड काम कर रहे हैं।",
        "hinglish": "Assistant abhi uplabdh nahi hai. Dashboards kaam kar rahe hain.",
    },
}

KIND_WORD: dict[Lang, dict[str, str]] = {
    "en": {"item": "an item", "vendor": "a vendor", "location": "a warehouse", "po": "a purchase order"},
    "hi": {"item": "कोई आइटम", "vendor": "कोई वेंडर", "location": "कोई वेयरहाउस", "po": "कोई पर्चेज ऑर्डर"},
    "hinglish": {"item": "koi item", "vendor": "koi vendor", "location": "koi warehouse", "po": "koi purchase order"},
}  # fmt: skip

NOT_FOUND: dict[Lang, str] = {
    "en": "I couldn't find {kind} matching “{text}”.",
    "hi": "“{text}” से मिलता {kind} नहीं मिला।",
    "hinglish": "“{text}” se milta {kind} nahi mila.",
}
DID_YOU_MEAN: dict[Lang, str] = {
    "en": "Did you mean one of these?",
    "hi": "क्या आपका मतलब इनमें से कोई है?",
    "hinglish": "Kya aapka matlab inmein se koi hai?",
}
WHICH_ONE: dict[Lang, str] = {
    "en": "“{text}” matches more than one record. Which one do you mean?",
    "hi": "“{text}” एक से ज़्यादा रिकॉर्ड से मिलता है। आपका मतलब कौन सा है?",
    "hinglish": "“{text}” ek se zyada record se milta hai. Aapka matlab kaun sa hai?",
}
FOUND_ROWS: dict[Lang, str] = {
    "en": "Found {n} matching records.",
    "hi": "{n} मिलते-जुलते रिकॉर्ड मिले।",
    "hinglish": "{n} matching records mile.",
}
NO_ROWS: dict[Lang, str] = {
    "en": "No matching records were found.",
    "hi": "कोई मिलता-जुलता रिकॉर्ड नहीं मिला।",
    "hinglish": "Koi matching record nahi mila.",
}
