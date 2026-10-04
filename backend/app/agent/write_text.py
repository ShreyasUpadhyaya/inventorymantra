"""User-facing text for chat writes (C6.3): the one-at-a-time questions for missing details,
the confirm card and the outcomes. Fixed text, no model involved."""

from app.agent.prompts import Lang

L = dict[Lang, str]

# The question for each missing field ({item} is filled in when the item is known).
FIELD_QUESTIONS: dict[str, L] = {
    "kind": {
        "en": "Is this stock coming in, going out, or being issued to a team?",
        "hi": "क्या यह स्टॉक आ रहा है, जा रहा है, या किसी टीम को दिया जा रहा है?",
        "hinglish": "Kya yeh stock aa raha hai, ja raha hai, ya kisi team ko diya ja raha hai?",
    },
    "item": {"en": "Which item?", "hi": "कौन सा आइटम?", "hinglish": "Kaun sa item?"},
    "quantity": {
        "en": "How many{of_item}?",
        "hi": "कितने{of_item}?",
        "hinglish": "Kitne{of_item}?",
    },
    "location": {
        "en": "Which warehouse?",
        "hi": "कौन सा वेयरहाउस?",
        "hinglish": "Kaun sa warehouse?",
    },
    "from_location": {
        "en": "From which warehouse?",
        "hi": "किस वेयरहाउस से?",
        "hinglish": "Kis warehouse se?",
    },
    "to_location": {
        "en": "To which warehouse?",
        "hi": "किस वेयरहाउस में?",
        "hinglish": "Kis warehouse mein?",
    },
    "issued_to": {
        "en": "Which shop floor or team is it issued to?",
        "hi": "यह किस शॉप फ्लोर या टीम को दिया जा रहा है?",
        "hinglish": "Yeh kis shop floor ya team ko diya ja raha hai?",
    },
    "po": {"en": "Which PO?", "hi": "कौन सा PO?", "hinglish": "Kaun sa PO?"},
    "vendor": {"en": "Which vendor?", "hi": "कौन सा वेंडर?", "hinglish": "Kaun sa vendor?"},
    "unit_price": {
        "en": "What is the unit price{of_item}, in ₹?",
        "hi": "{of_item_hi} प्रति यूनिट कीमत कितनी है (₹ में)?",
        "hinglish": "{of_item_hi} ki unit price kitni hai (₹ mein)?",
    },
    "expected_delivery_date": {
        "en": "What is the expected delivery date?",
        "hi": "डिलीवरी की अपेक्षित तारीख क्या है?",
        "hinglish": "Expected delivery date kya hai?",
    },
    "min_level": {
        "en": "At what stock level should it be reordered?",
        "hi": "कितने स्टॉक पर दोबारा मंगवाना है?",
        "hinglish": "Kitne stock par dobara mangwana hai?",
    },
    "reorder_qty": {
        "en": "How many should be ordered each time?",
        "hi": "हर बार कितने मंगवाने हैं?",
        "hinglish": "Har baar kitne mangwane hain?",
    },
    "lead_time_days": {
        "en": "How many days does delivery usually take?",
        "hi": "डिलीवरी में आमतौर पर कितने दिन लगते हैं?",
        "hinglish": "Delivery mein aam taur par kitne din lagte hain?",
    },
    "name": {
        "en": "What is the vendor's name?",
        "hi": "वेंडर का नाम क्या है?",
        "hinglish": "Vendor ka naam kya hai?",
    },
}
GENERIC_QUESTION: L = {
    "en": "Please tell me the {field}.",
    "hi": "कृपया {field} बताइए।",
    "hinglish": "Kripya {field} batayein.",
}
KIND_OPTIONS: dict[Lang, list[tuple[str, str]]] = {
    "en": [("Stock in", "inbound"), ("Stock out", "outbound"), ("Issue to a team", "issue")],
    "hi": [("स्टॉक आया", "inbound"), ("स्टॉक गया", "outbound"), ("टीम को दिया", "issue")],
    "hinglish": [("Stock aaya", "inbound"), ("Stock gaya", "outbound"),
                 ("Team ko diya", "issue")],
}  # fmt: skip
DIDNT_CATCH: dict[str, L] = {
    "number": {
        "en": "I didn't catch a number.",
        "hi": "मुझे कोई संख्या समझ नहीं आई।",
        "hinglish": "Mujhe koi number samajh nahi aaya.",
    },
    "date": {
        "en": "I didn't catch a date.",
        "hi": "मुझे कोई तारीख समझ नहीं आई।",
        "hinglish": "Mujhe koi date samajh nahi aayi.",
    },
    "choice": {
        "en": "Please pick one of the options.",
        "hi": "कृपया कोई एक विकल्प चुनिए।",
        "hinglish": "Kripya koi ek option chuniye.",
    },
}
GAVE_UP: L = {
    "en": "I still couldn't work out the {field}, so nothing was changed. "
    "Please ask again with the details.",
    "hi": "मुझे अब भी {field} समझ नहीं आया, इसलिए कुछ नहीं बदला गया। "
    "कृपया पूरी जानकारी के साथ दोबारा पूछिए।",
    "hinglish": "Mujhe ab bhi {field} samajh nahi aaya, isliye kuch nahi badla gaya. "
    "Kripya poori jaankari ke saath dobara poochiye.",
}
CONFIRM_QUESTION: L = {
    "en": "Please check and confirm. Nothing is saved until you do.",
    "hi": "कृपया जाँचकर पुष्टि कीजिए। पुष्टि से पहले कुछ भी सेव नहीं होगा।",
    "hinglish": "Kripya check karke confirm kijiye. Confirm se pehle kuch bhi save nahi hoga.",
}
CONFIRM_OPTIONS: dict[Lang, tuple[str, str]] = {
    "en": ("Confirm", "Cancel"),
    "hi": ("पुष्टि करें", "रद्द करें"),
    "hinglish": ("Confirm", "Cancel"),
}
CANCELLED: L = {
    "en": "Cancelled. Nothing was changed.",
    "hi": "रद्द कर दिया गया। कुछ भी नहीं बदला।",
    "hinglish": "Cancel kar diya. Kuch bhi nahi badla.",
}
NOT_A_CHANGE: L = {
    "en": "I couldn't match that to a change I can make for you.",
    "hi": "मैं इसे किसी ऐसे बदलाव से नहीं जोड़ सका जो मैं आपके लिए कर सकूँ।",
    "hinglish": "Main ise kisi aise badlaav se nahi jod saka jo main aapke liye kar sakun.",
}
OFFLINE_WRITE: L = {
    "en": "Offline demo mode (LLM_MODE=fake) can only make simple stock changes, such as "
    "“Add 3 AC Contactor to 132-2”. With the real model this request would be understood.",
    "hi": "ऑफ़लाइन डेमो मोड (LLM_MODE=fake) में केवल सरल स्टॉक बदलाव हो सकते हैं, जैसे "
    "“Add 3 AC Contactor to 132-2”।",
    "hinglish": "Offline demo mode (LLM_MODE=fake) mein sirf simple stock changes ho sakte "
    "hain, jaise “Add 3 AC Contactor to 132-2”.",
}
# Field names as said in questions and messages.
FIELD_NAMES: dict[str, L] = {
    "item": {"en": "item", "hi": "आइटम", "hinglish": "item"},
    "location": {"en": "warehouse", "hi": "वेयरहाउस", "hinglish": "warehouse"},
    "from_location": {"en": "warehouse", "hi": "वेयरहाउस", "hinglish": "warehouse"},
    "to_location": {"en": "warehouse", "hi": "वेयरहाउस", "hinglish": "warehouse"},
    "quantity": {"en": "quantity", "hi": "मात्रा", "hinglish": "quantity"},
    "vendor": {"en": "vendor", "hi": "वेंडर", "hinglish": "vendor"},
    "issued_to": {"en": "team", "hi": "टीम", "hinglish": "team"},
}

# Answers that confirm the card; anything else cancels it (never save on a guess).
CONFIRM_WORDS = {
    "confirm", "yes", "y", "ok", "okay", "go ahead", "do it", "haan", "han", "ha", "haa", "ji",
    "theek hai", "thik hai", "kar do", "हाँ", "हां", "जी", "ठीक है", "पुष्टि करें", "पुष्टि",
}  # fmt: skip


def field_name(field: str, lang: Lang) -> str:
    base = field.rsplit(".", 1)[-1]
    if base in FIELD_NAMES:
        return FIELD_NAMES[base][lang]
    return base.replace("_", " ")
