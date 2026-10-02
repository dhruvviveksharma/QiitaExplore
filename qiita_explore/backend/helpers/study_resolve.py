"""Which study does a piece of free text mean? Pure matching, no database —
helpers/study_tools.py's resolve_study tool feeds it the chat's own studies
(its pins; in a project chat, also every study in the project) and decides what
to do with the answer.

The rule is deliberately cautious: a study is used without asking only when it
is the chat's own and the sole one that matches. Everything else becomes a
"Which study?" picker for the user.

A chat study matches the text when:
  - its id appears in the text;
  - its title's acronym does, or starts with it ("AGP" <-> "American Gut
    Project", and "American Gut Project Australia");
  - two or more identifying words appear in its title / alias / PI surname, or
    every identifying word does ("Hadza" <-> "Hadza hunter-gatherers");
or, when the text names nothing at all ("/samples show me the samples") or says
"pinned" / "this study", the chat has exactly one study.
"""
import re

# Words that say what to show, not which study. Command words, generic nouns,
# question words and the data-type names (those are arguments to the tool).
_FILLER = frozenset("""
a about all an and any are at be by can could do for from get give have how i in into is it its
list me my of on or our please related s see show some tell than that the their them these this
those to using want we what where which with would you
study studies prep preps preparation preparations template templates sample samples metadata
graph graphs processing processed pipeline network files file filepath filepaths path paths
fastq fastqs fasta biom artifact artifacts data type types aggregate aggregation aggregations
cohort add put include
16s 18s its metagenomic metagenomics metatranscriptomic metabolomic metabolomics proteomic
proteomics wgs shotgun amplicon amplicons multiomic genome isolate
""".split())
_ACRONYM_STOP = frozenset("a an and the of in on for to with from by at".split())
_REFERS = re.compile(r"\b(pinned|this study|that study|the study|this one|same study)\b", re.I)
_EXPLICIT = re.compile(r"(?:\bstud(?:y|ies)\s*(?:id\s*)?#?\s*|#)(\d{1,7})\b", re.I)
_WORD = re.compile(r"[a-z0-9]+")


def _words(text):
    return _WORD.findall((text or "").lower().replace("'s", ""))


def identifying_tokens(text):
    """The words of `text` that could name a study (digits kept: they may be ids)."""
    return [w for w in _words(text) if w not in _FILLER and (len(w) >= 2 or w.isdigit())]


def explicit_study_id(text):
    """The id in "study 10317" / "study id 10317" / "#10317", else None."""
    m = _EXPLICIT.search(text or "")
    return int(m.group(1)) if m else None


def refers_to_chat_study(text):
    return bool(_REFERS.search(text or ""))


def _acronyms(title):
    words = _words(title)
    out = set()
    if len(words) >= 2:
        out.add("".join(w[0] for w in words))
        sig = [w for w in words if w not in _ACRONYM_STOP]
        if len(sig) >= 2:
            out.add("".join(w[0] for w in sig))
    return out


def acronym_rank(study, tokens):
    """0 when a token is the acronym of the study's title (or alias), 1 when it
    starts one ("AGP" also names "American Gut Project Australia" — two such pins
    mean asking), else None. Tokens shorter than 3 letters never start one."""
    acronyms = _acronyms(study.get("study_title")) | _acronyms(study.get("study_alias"))
    named = [t for t in tokens if not t.isdigit()]
    if any(t in acronyms for t in named):
        return 0
    if any(len(t) >= 3 and a.startswith(t) for t in named for a in acronyms):
        return 1
    return None


def study_matches(study, tokens):
    """Does chat study `study` ({study_id, study_title, study_alias?, pi_name?})
    match the identifying `tokens`?"""
    if not tokens:
        return False
    if str(study.get("study_id")) in tokens:
        return True
    title, alias = study.get("study_title") or "", study.get("study_alias") or ""
    if acronym_rank(study, tokens) is not None:
        return True
    words = set(_words(title)) | set(_words(alias))
    pi = _words(study.get("pi_name"))
    if pi:
        words.add(pi[-1])
    named = [t for t in tokens if not t.isdigit()]
    hits = [t for t in named if t in words]
    return len(hits) >= 2 or (bool(hits) and len(hits) == len(named))


def resolve_text(text, chat_studies):
    """{"study_id": id or None, "how": str, "matched": [study, ...]}.

    `study_id` is set only when the answer is certain enough to act on without
    asking: an explicit "study 10317" in the text (how="explicit"), or exactly one
    chat study matching (how="chat"). `matched` lists the chat studies that
    matched, for the picker, when it isn't."""
    explicit = explicit_study_id(text)
    if explicit is not None:
        return {"study_id": explicit, "how": "explicit", "matched": []}
    tokens = identifying_tokens(text)
    names_nothing = not [t for t in tokens if not t.isdigit()] and not any(
        str(s.get("study_id")) in tokens for s in chat_studies)
    if (names_nothing or refers_to_chat_study(text)) and len(chat_studies) == 1:
        return {"study_id": int(chat_studies[0]["study_id"]), "how": "chat", "matched": list(chat_studies)}
    matched = [s for s in chat_studies if study_matches(s, tokens)]
    if len(matched) == 1:
        return {"study_id": int(matched[0]["study_id"]), "how": "chat", "matched": matched}
    if not matched and (names_nothing or refers_to_chat_study(text)):
        matched = list(chat_studies)          # "this study" with several pins: offer all of them
    return {"study_id": None, "how": "ambiguous", "matched": matched}
