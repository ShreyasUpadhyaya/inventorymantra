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
    # The Owner sees every table, so "available to you" wrongly sounded like a denial
    # (user test 2026-10-04); other roles keep it, which reveals nothing about hidden data.
    "unanswerable_all": {
        "en": "That isn't recorded in our data.",
        "hi": "यह जानकारी हमारे डेटा में दर्ज नहीं है।",
        "hinglish": "Ye jaankari hamare data mein darj nahi hai.",
    },
    "no_choice": {
        "en": "I couldn't tell which period you meant, so I haven't looked anything up. "
        "Please ask again with a period, for example “last 30 days” or “August 2026”.",
        "hi": "मैं समझ नहीं सका कि आपका मतलब कौन सी अवधि से है, इसलिए मैंने कुछ नहीं खोजा। "
        "कृपया अवधि के साथ दोबारा पूछें, जैसे “पिछले 30 दिन” या “अगस्त 2026”।",
        "hinglish": "Main samajh nahi saka ki aapka matlab kaun si avadhi se hai, isliye maine "
        "kuch nahi dhoondha. Kripya avadhi ke saath dobara poochhiye, jaise “pichle 30 din”.",
    },
    "offline": {
        "en": "Offline demo mode (LLM_MODE=fake) can only answer known test questions and simple "
        "questions about one chosen record. With the real model this question would be answered.",
        "hi": "ऑफ़लाइन डेमो मोड (LLM_MODE=fake) केवल ज्ञात टेस्ट सवालों और किसी एक चुने हुए रिकॉर्ड "
        "के सरल सवालों का उत्तर देता है। असली मॉडल के साथ इस सवाल का उत्तर दिया जाएगा।",
        "hinglish": "Offline demo mode (LLM_MODE=fake) sirf jaane-pehchaane test sawaalon aur ek "
        "chune hue record ke saral sawaalon ka jawab deta hai. Asli model ke saath iska jawab milega.",
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
DIDNT_RECOGNISE: dict[Lang, str] = {
    "en": "I didn't recognise “{answer}”.",
    "hi": "मैं “{answer}” को पहचान नहीं सका।",
    "hinglish": "Main “{answer}” ko pehchaan nahi saka.",
}
NO_CHOICE: dict[Lang, str] = {
    "en": "I couldn't tell which one you meant for “{text}”, so I haven't looked anything up. "
    "You can ask again with one of these:",
    "hi": "मैं समझ नहीं सका कि “{text}” से आपका मतलब कौन सा है, इसलिए मैंने कुछ नहीं खोजा। "
    "आप इनमें से किसी एक के साथ दोबारा पूछ सकते हैं:",
    "hinglish": "Main samajh nahi saka ki “{text}” se aapka matlab kaun sa hai, isliye maine kuch "
    "nahi dhoondha. Aap inmein se kisi ek ke saath dobara pooch sakte hain:",
}
FOUND_ROWS: dict[Lang, str] = {
    "en": "I found {n} matching records.",
    "hi": "मुझे {n} मिलते-जुलते रिकॉर्ड मिले।",
    "hinglish": "Mujhe {n} matching records mile.",
}
NO_ROWS: dict[Lang, str] = {
    "en": "No matching records were found.",
    "hi": "कोई मिलता-जुलता रिकॉर्ड नहीं मिला।",
    "hinglish": "Koi matching record nahi mila.",
}

# Template (fallback) answer pieces: conversational, built from the rows (D-040).
PREDICT_NOTE: dict[Lang, str] = {
    "en": "I can't predict what will happen, but here is what is on record:",
    "hi": "आगे क्या होगा, यह मैं नहीं बता सकता, लेकिन रिकॉर्ड में यह दर्ज है:",
    "hinglish": "Aage kya hoga, yeh main nahi bata sakta, lekin record mein yeh darj hai:",
}
FIRST_ONE: dict[Lang, str] = {
    "en": "The first one:",
    "hi": "पहला रिकॉर्ड:",
    "hinglish": "Pehla record:",
}
