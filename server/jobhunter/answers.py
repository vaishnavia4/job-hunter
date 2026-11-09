"""Answer bank: deterministic answers for application questions, learned from the user.

Resolution order for a question label:
  1. LEARNED answers in <workspace>/profile/answer_bank.json (source user_correction / user_filled) whose patterns match
  2. the canonical question bank (templates/question_bank.json, first matching entry wins)
       -> the workspace answer_bank entry with that id (source setup), else profile.json[profile_key]
  3. unknown -> ASK_USER  (then record_answer so it's never asked again)

Options matching is subgroup-aware: options_hint (most specific first) is tried before anything generic, and a
generic substring that hits several options (e.g. 'Asian' in 'East Asian'/'South Asian') is AMBIGUOUS, never a
silent first match.
"""
import json
import re
from datetime import date

from .workspace import TEMPLATES, Workspace, load_json, save_json

STATUS_OK, ASK, TICK, ESSAY, AMBIG = "ok", "ASK_USER", "USER_MUST_TICK", "GENERATE_ESSAY", "AMBIGUOUS_OPTIONS"
HAND = "HAND_TO_USER"
# Questions that ask whether an AI agent / automated tool is filling or submitting the application. These are never
# answered from the bank: the agent fills everything else, stops before Submit and hands the form to the user, who
# either submits it themselves (then "a human submitted it" is true) or lets the agent answer truthfully ("Yes").
AI_AGENT_RX = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|automated|autonomous|llm|gpt|chatbot|bot)\b[^.?]{0,60}\b(agent|assistant|tool|system|software|bot)s?\b"
    r"[^.?]{0,120}\b(apply|applying|application|submit|submitting|complet|fill|on behalf)"
    r"|\b(complet|fill|submit|prepar)\w*[^.?]{0,40}\b(using|with|by|via)\b[^.?]{0,30}\b(ai|a\.i\.|automated|autonomous|bot|llm|gpt|artificial intelligence)\b"
    r"|\bon behalf of (a|the|this) candidate\b|\bif you are a human\b|\bhuman submitting\b",
    re.I)
AI_AGENT_NOTE = ("AI-agent disclosure question: never answer from the answer bank. Fill everything else, stop before "
                 "Submit and notify the user. If the user finishes and submits the form themselves, their own answer "
                 "is truthful; if the agent submits, the only truthful answer is that an AI agent is applying.")


# Employer notices about THEIR use of AI to screen applications ("We use Greenhouse's AI-powered Talent Matching tool
# to compare your application ...") are not questions about whether an AI agent is applying.
EMPLOYER_AI_NOTICE_RX = re.compile(
    r"\b(we|our (team|company|hiring team|recruiters?)|this (company|employer)|[A-Z][\w&.-]* (uses|leverages))\b[^.?]{0,40}"
    r"\b(use|uses|using|leverage|leverages|may use)\b[^.?]{0,80}"
    r"\b(compare|review|evaluate|screen|assess|match|rank|score|process|analy[sz]e)\b[^.?]{0,40}"
    r"\b(your|candidates?'?|applicants?'?|applications?)\b",
    re.I)


def is_ai_agent_question(text):
    """True when the text asks whether an AI agent / automated tool is applying or submitting.
    Sentences that only announce the EMPLOYER's own use of AI for screening are ignored."""
    t = text or ""
    for sent in re.split(r"(?<=[.?!])\s+|\n+", t):
        if AI_AGENT_RX.search(sent) and not EMPLOYER_AI_NOTICE_RX.search(sent):
            return True
    return False
STOP = set("a an the and or of to in for on at by with your you are is do does did have has will would be been being this that "
           "our we us any please select one which what how if as from it its not no yes can may currently ever".split())
NEG_RX = re.compile(r"\b(no|not|never|n't|haven't|don't|won't)\b")
DECLINE_RX = re.compile(r"decline|prefer not|do not wish|don't wish|not wish to|choose not|rather not")


def norm(s):
    s = (s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s.rstrip("*").strip()


def pnorm(s):
    """Punctuation-insensitive normal form: lowercase words only, single spaces."""
    return re.sub(r"[^a-z0-9]+", " ", norm(s)).strip()


CAREERS_RX = re.compile(r"^(?:the )?(?:[a-z0-9&.'-]+ )*?careers? (?:website|site|page)$")


def _canon(s):
    """Canonical form used to compare a stored answer with what is on the page."""
    n = norm(s)
    # "Campus Career Site" / "University career site" are student-recruiting channels, not the company's own site
    if CAREERS_RX.match(n) and not re.search(r"\b(campus|university|college|job board)\b", n):
        return "company careers website"
    return n


def _amount(s):
    """'$140,000+' / '140000' / '140k' -> 140000.0 ; anything else -> None."""
    m = re.fullmatch(r"\$?\s*([\d,]+(?:\.\d+)?)\s*(k)?\s*\+?(?:\s*(?:usd|per year|/yr|annually))?", s)
    if not m or not re.search(r"\d", m[1]):
        return None
    return float(m[1].replace(",", "")) * (1000 if m[2] else 1)


def equivalent(found, expected):
    """True when the page value and the expected answer mean the same thing (verify_form)."""
    nv, ne = norm(found), norm(expected)
    if not nv or not ne:
        return False
    if nv == ne or _canon(nv) == _canon(ne):
        return True
    # decline variants: "I don't wish to answer" = "Decline to self-identify" = "Prefer not to say"
    if DECLINE_RX.search(nv) and DECLINE_RX.search(ne):
        return True
    # non-veteran variants: "I am not a protected veteran" = "I am not a veteran" = "No military service"
    # ("I identify as a veteran, just not a protected veteran" is NOT a match: it starts with "I identify as")
    nonvet = re.compile(r"^(i am )?not a (protected )?veteran|^no military service|^i have not served|^i do not identify as a (protected )?veteran")
    if nonvet.search(nv) and (nonvet.search(ne) or ne == "no"):
        return True
    # "<company>.com/careers" / "<Company> Careers Page" = "Company careers website"
    if re.search(r"careers? (website|site|page)", ne) and re.search(r"\.com/careers|careers? (website|site|page)|^(corporate|company) (web ?site|careers?)$|^[a-z0-9&.\- ]{2,30} web ?site$|^careers? at [a-z0-9&.\- ]{2,40}$", nv) \
            and not re.search(r"\b(job|indeed|linkedin|glassdoor|board|social|search|campus|university|college)\b", nv):
        return True
    # affirmation wordings: "I Agree" = "I acknowledge" = "I accept" (never matches a No/decline)
    affirm = re.compile(r"^(yes,? )?i (agree|acknowledge|accept|consent)\b|^(agree|acknowledged?|accept(ed)?)$")
    if affirm.search(nv) and affirm.search(ne):
        return True
    # degree pick lists with abbreviations only (GED/AA/BA/BS/MS...): "BS" satisfies a rule that allows Bachelor of Science
    if re.fullmatch(r"b\.? ?s\.?(c\.?)?", nv) and "bachelor of science" in ne:
        return True
    # "Bachelor of Science (B.S.)" satisfies a rule whose fallback is Bachelor of Science
    if re.match(r"bachelor of science\b", nv) and "bachelor of science" in ne:
        return True
    # generic bachelor's wording ("Bachelors level degree", "Bachelor degree") satisfies "Bachelor's Degree" rules
    if re.fullmatch(r"bachelor'?s?( level)?( degree)?", nv) and re.search(r"bachelor'?s degree", ne):
        return True
    # Workday's standard field-of-study option for Computer Science
    if re.fullmatch(r"computer (and|&) information sciences?", nv) and re.match(r"computer science", ne):
        return True
    # race lists without subgroups: expected "South Asian ..." is satisfied by plain "Asian" (never "American Indian")
    if ne.startswith("south asian") and re.match(r"asian\b", nv):
        return True
    # Bachelor of Engineering abbreviations: "Bachelor of Engineering (BEng)" = "Bachelor of Engineering (B.E.) ..."
    beng = re.compile(r"^bachelor of engineering\b|^b\.? ?e(ng)?\.?$")
    if beng.search(nv) and beng.search(ne):
        return True
    # clearance: a "No" on the page satisfies a stored "None" (no clearance held)
    if {nv, ne} == {"no", "none"}:
        return True
    # relocation: stored "Anywhere"/"Yes" is satisfied by an option that says the candidate is open/willing to relocate
    if ne in ("anywhere", "yes") and re.match(r"(yes\W+)?(i am |i'm )?(open|willing) to relocat", nv):
        return True
    # yes/no prefixes: "No, I do not have a disability..." = "No"
    for a, b in ((nv, ne), (ne, nv)):
        if b in ("yes", "no") and re.match(re.escape(b) + r"(\W|$)", a):
            return True
    # containment, whole words only and never for tiny values ("BA" must not match inside "Bachelor")
    short, long_ = sorted((nv, ne), key=len)
    if len(short) > 3 and re.search(r"(?<!\w)" + re.escape(short) + r"(?!\w)", long_):
        return True
    rv, re_ = str(found).strip().lower(), str(expected).strip().lower()
    # amounts: "$140,000+" = "140000" = "140k"
    a, b = _amount(rv), _amount(re_)
    if a is not None and a == b:
        return True
    # range options: page "5-10" / "5 to 10 years" / "10+" contains the expected number "6"
    if re.fullmatch(r"\d+(\.\d+)?", re_):
        x = float(re_)
        r = re.fullmatch(r"(\d+)\s*(?:-|–|to)\s*(\d+)(?:\s*years?)?", rv)
        if r and float(r[1]) <= x <= float(r[2]):
            return True
        p = re.fullmatch(r"(\d+)\s*\+(?:\s*years?)?", rv)
        if p and x >= float(p[1]):
            return True
    dv, de = re.sub(r"\D", "", nv), re.sub(r"\D", "", ne)
    return len(dv) >= 7 and dv[-10:] == de[-10:]


def _bank():
    return load_json(TEMPLATES / "question_bank.json", {"questions": []})


def _ws_bank_path(ws):
    return ws.profile_dir / "answer_bank.json"


def load_answers(ws):
    return load_json(_ws_bank_path(ws), {"answers": []})


def _dig(d, path):
    cur = d
    for part in (path or "").split("."):
        if not part:
            return None
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _match_any(patterns, text):
    alt = pnorm(text)
    for p in patterns or []:
        try:
            if re.search(p, text, re.I) or re.search(p, alt, re.I):
                return p
        except re.error:
            if p.lower() in text or p.lower() in alt:
                return p
    return None


def match_question(text):
    """Return the first canonical question whose patterns match the normalized label (or None)."""
    t = norm(text)
    for q in _bank()["questions"]:
        if _match_any(q["patterns"], t):
            return q
    return None


# ---------------------------------------------------------------- options
def _num(s):
    m = re.findall(r"\$?\s*(\d[\d,]*)(\s*k)?", s or "", re.I)
    out = []
    for n, k in m:
        v = int(n.replace(",", ""))
        if k:
            v *= 1000
        out.append(v)
    return out


# polarity: an option asserting the opposite of the answer must never win
POS_START_RX = re.compile(r"^(yes\b|i identify as\b|i am an? |i have an? |i do have\b|i am (hispanic|latino|latina|latinx)\b)")
NEG_START_RX = re.compile(r"^(no\b|not\b|never\b|none\b|i (am|do|have|will|did) (not|never)\b|i (don't|haven't|won't|didn't)\b)")
DEGREE_FAMILIES = (("bachelor", "bachelor"), ("master", "master"), ("associate", "associate"), ("doctor", r"doctor|phd|ph d"), ("mba", "mba"))
DEGREE_FILLER = set("of in the degree degrees s and a an".split())


def _polarity(text):
    """-1 for a 'no / not a veteran' style answer, +1 for 'yes / I identify as ...', 0 neutral/decline."""
    n = norm(text)
    if not n or DECLINE_RX.search(n):
        return 0
    if NEG_START_RX.match(n):
        return -1
    if POS_START_RX.match(n):
        return 1
    return 0


def _conflicts(option, pol):
    """True when `option` asserts the opposite of an answer with polarity `pol`."""
    n = norm(option)
    if pol < 0:
        return bool(POS_START_RX.match(n))
    if pol > 0:
        return bool(NEG_START_RX.match(n)) or bool(DECLINE_RX.search(n))
    return False


def _degree_words(text):
    fam = "|".join(rx for _, rx in DEGREE_FAMILIES)
    t = re.sub(fam, " ", pnorm(text))
    return {w for w in t.split() if w not in DEGREE_FILLER}


def _degree_pick(answer, opts):
    """Map 'Bachelor of Engineering' onto the offered degree list: same field > generic "Bachelor's Degree" >
    Science. Never Arts (or another unrelated field) unless the answer itself says so. Returns (option|None, how|None)."""
    na = pnorm(answer)
    for name, rx in DEGREE_FAMILIES:
        if re.search(rx, na):
            break
    else:
        return None, None
    fam = [o for o in opts if re.search(rx, pnorm(o))]
    if not fam:
        return None, None
    want = _degree_words(answer)
    if want:
        scored = [(len(want & _degree_words(o)), o) for o in fam]
        best = max(sc for sc, _ in scored)
        if best:
            top = [o for sc, o in scored if sc == best]
            return sorted(top, key=len)[0], "degree-field"
    generic = [o for o in fam if not _degree_words(o)]
    if generic:
        return sorted(generic, key=len)[0], "degree-generic"
    sci = [o for o in fam if "science" in pnorm(o)]
    if sci and not re.search(r"\barts?\b", na):
        return sorted(sci, key=len)[0], "degree-science"
    return None, "degree-no-safe-match"


def choose_option(answer, options, hints=None, strategy=""):
    """Pick the option that matches `answer`. Returns (option|None, how)."""
    if not options:
        return None, "no-options"
    opts = [o for o in options if norm(o) not in ("", "select one", "select...", "--")]
    na = norm(str(answer))
    # 1. bracket dropdowns (salary)
    if strategy == "bracket":
        vals = _num(str(answer))
        if vals:
            v = vals[0]
            ranges = []
            for o in opts:
                ns = _num(o)
                if len(ns) >= 2:
                    ranges.append((ns[0], ns[1], o))
                elif len(ns) == 1 and re.search(r"greater|more than|over|\+", o, re.I):
                    ranges.append((ns[0], 10**9, o))
            for lo, hi, o in ranges:
                if lo == v:
                    return o, "bracket-start"
            for lo, hi, o in ranges:
                if lo <= v < hi:
                    return o, "bracket-contains"
    # 2. exact / normalized-equal
    for o in opts:
        if norm(o) == na:
            return o, "exact"
    for o in opts:
        if pnorm(o) == pnorm(str(answer)) and pnorm(o):
            return o, "exact-normalized"
    # never consider an option that asserts the opposite of the answer (identify AS a veteran vs not a veteran)
    pol = _polarity(str(answer))
    opts = [o for o in opts if not _conflicts(o, pol)] if pol else opts
    # 2b. options that START with the answer (unique only)
    if na:
        starts = [o for o in opts if re.match(re.escape(na) + r"(\W|$)", norm(o))]
        if len(starts) == 1:
            return starts[0], "starts-with"
    # 2c. degree mapping
    dopt, dhow = _degree_pick(str(answer), opts)
    if dopt:
        return dopt, dhow
    if dhow == "degree-no-safe-match":
        return None, dhow
    # 3. hints, most specific first
    for h in hints or []:
        hits = [o for o in opts if re.search(h, norm(o), re.I)]
        if len(hits) == 1:
            return hits[0], f"hint:{h}"
        if len(hits) > 1:
            # prefer one that also contains the stored answer text, else the shortest
            both = [o for o in hits if na and na in norm(o)]
            if len(both) == 1:
                return both[0], f"hint+answer:{h}"
            starts = [o for o in hits if norm(o).startswith(na)] if na else []
            if len(starts) == 1:
                return starts[0], f"hint+start:{h}"
            return sorted(hits, key=len)[0], f"hint-multi:{h}"
    # 4. yes / no polarity
    bank = _bank()
    if na in ("yes", "no"):
        rx = bank.get("yes_hints" if na == "yes" else "no_hints", [])
        cands = []
        for o in opts:
            no_ = norm(o)
            if DECLINE_RX.search(no_):
                continue
            if any(re.search(r, no_) for r in rx):
                if na == "yes" and NEG_RX.search(no_) and not no_.startswith("yes"):
                    continue
                cands.append(o)
        if len(cands) == 1:
            return cands[0], "polarity"
        if len(cands) > 1:
            starts = [o for o in cands if norm(o).startswith(na)]
            if len(starts) == 1:
                return starts[0], "polarity-start"
    # 5. answer as a whole word inside exactly one option (never a silent first partial match)
    if na:
        hits = [o for o in opts if re.search(r"(^|\W)" + re.escape(na) + r"($|\W)", norm(o))]
        if len(hits) == 1:
            return hits[0], "contains"
        if len(hits) > 1:
            return None, "ambiguous:" + " | ".join(hits)
        # option text inside the answer (answer is more detailed than the option)
        inside = [o for o in opts if len(norm(o)) > 3 and norm(o) in na]
        if len(inside) == 1:
            return inside[0], "option-in-answer"
    if pol < 0:  # 'No ...' answer: a single negatively-phrased option ("Not Hispanic or Latino", "I am not ...")
        negs = [o for o in opts if NEG_START_RX.match(norm(o)) and not DECLINE_RX.search(norm(o))]
        if len(negs) == 1:
            return negs[0], "negative-option"
    return None, "no-match"


# ---------------------------------------------------------------- lookup
def _skill_years(ws, text, prof):
    m = re.search(r"(with|in|using|of) ([a-z0-9 .#+/-]{2,40})", norm(text))
    if not m:
        return None
    skill = re.sub(r"\b(experience|do you have|years?)\b", "", m.group(2)).strip(" ?.")
    exp = prof.get("experience", {}) or {}
    table = dict(exp.get("skill_years", {}) or {})
    for k, v in exp.items():
        if k.startswith("years_") and v not in (None, ""):
            table.setdefault(k[6:], v)
    for k, v in table.items():
        if k.lower() and (k.lower() in skill or skill in k.lower()):
            return v
    return None


def lookup(ws, question_text, options=None):
    """Best answer for a question label. Returns a dict with status/answer/option/confidence/source."""
    ws = ws if isinstance(ws, Workspace) else Workspace(ws)
    t = norm(question_text)
    bank_ws = load_answers(ws)
    prof = ws.profile() or {}
    res = {"question": question_text, "question_id": None, "status": ASK, "answer": None, "option": None,
           "confidence": 0.0, "source": None, "note": ""}
    if is_ai_agent_question(question_text):
        res.update(status=HAND, question_id="ai_agent_disclosure", note=AI_AGENT_NOTE)
        return res

    # 1. learned answers (user corrections / user-filled) win
    learned = [a for a in bank_ws.get("answers", []) if a.get("source") in ("user_correction", "user_filled") and a.get("patterns")]
    for a in reversed(learned):  # newest first
        if _match_any(a["patterns"], t):
            q = match_question(question_text) or {}
            res.update(question_id=a.get("id") or q.get("id"), answer=a["answer"], source=a["source"], confidence=0.98,
                       status=TICK if a["answer"] == TICK else STATUS_OK, note=a.get("note", ""))
            hints = a.get("options_hint") or q.get("options_hint")
            return _with_option(res, options, hints, q.get("option_strategy", ""))

    q = match_question(question_text)
    if not q:
        res["note"] = "Unknown question - ask the user, then record_answer()."
        return res
    res["question_id"] = q["id"]
    if q["type"] == "user_must_tick":
        # a user-given standing answer may exist (e.g. they said 'tick certify boxes for me')
        own = next((a for a in bank_ws.get("answers", []) if a.get("id") == q["id"] and a.get("answer") not in (None, "", TICK)), None)
        if own:
            res.update(answer=own["answer"], source=own.get("source", "setup"), confidence=0.9, status=STATUS_OK, note=own.get("note", ""))
            return _with_option(res, options, own.get("options_hint"), "")
        # standing consent recorded in profile.json consent_policy (default "ask")
        if str(((prof.get("consent_policy") or {}).get("legal_attestations", "ask"))).startswith("preauthorized"):
            res.update(answer="Yes", source="consent_policy", confidence=0.9, status=STATUS_OK,
                       note="Ticked under the user's standing consent (profile consent_policy.legal_attestations); list it in the report.")
            return _with_option(res, options, None, "")
        res.update(status=TICK, note=q.get("notes", "") or "Legal attestation - the user ticks this.")
        return res
    if q["type"] == "per_job_essay":
        res.update(status=ESSAY, note="Write a truthful, job-specific answer from master_resume.json; save it in notes.md.")
        return res

    # 2. workspace answer for this id (setup)
    own = next((a for a in bank_ws.get("answers", []) if a.get("id") == q["id"]), None)
    hints = (own or {}).get("options_hint") or q.get("options_hint")
    if own and own.get("answer") not in (None, ""):
        ans = own["answer"]
        res.update(answer=ans, source=own.get("source", "setup"), confidence=0.95, note=own.get("note", ""),
                   status=ASK if ans == ASK else (TICK if ans == TICK else STATUS_OK))
        return _with_option(res, options, hints, q.get("option_strategy", ""))
    # 3. profile fallback
    val = _skill_years(ws, question_text, prof) if q["id"] == "years_skill" else _dig(prof, q.get("profile_key"))
    if isinstance(val, (list, dict)):
        val = None
    if val not in (None, ""):
        res.update(answer=str(val), source="profile:" + q["profile_key"], confidence=0.75, status=STATUS_OK)
        return _with_option(res, options, hints, q.get("option_strategy", ""))
    res["note"] = f"No stored answer for '{q['id']}' - ask the user ({q['prompt']}), then record_answer()."
    return res


DISQUALIFY_RX = re.compile(
    r"withdraws? (me|my application|my candidacy)? ?from consideration|\bi do not agree\b|\bnot agreeing\b"
    r"|\bi do not (accept|consent|acknowledge)\b|\bdecline the (terms|agreement)\b")


def _with_option(res, options, hints, strategy):
    if res["status"] != STATUS_OK or not options:
        return res
    opt, how = choose_option(res["answer"], options, hints, strategy)
    res["option_match"] = how
    if opt is None:
        res["status"] = AMBIG if how.startswith("ambiguous") else ASK
        res["confidence"] = 0.3
        res["note"] = (res.get("note") or "") + f" | could not map answer '{res['answer']}' to options ({how}) - ask the user, then record_answer() with options_hint."
    elif DISQUALIFY_RX.search(norm(opt)):
        # an option that refuses the employer's terms or withdraws the application is never chosen automatically
        res["status"] = ASK
        res["confidence"] = 0.2
        res["note"] = (res.get("note") or "") + f" | matched option '{opt[:80]}' would refuse/withdraw - ask the user."
    else:
        res["option"] = opt
        res["confidence"] = round(min(res["confidence"], 0.99 if how in ("exact", "bracket-start") else 0.9), 2)
    return res


# ---------------------------------------------------------------- record
def auto_pattern(question_text):
    words = [w for w in pnorm(question_text).split() if w not in STOP and len(w) > 2]
    words = words[:6] or [re.escape(norm(question_text))]
    return "".join(rf"(?=.*\b{re.escape(w)})" for w in words)


def _same_question(q, t, answer):
    """Is a recorded label really the bank question it pattern-matched? Guards against reusing an id for a
    different question (a Yes/No answer stored under a text question, or a long label that merely mentions it)."""
    yn = bool(re.match(r"^(yes|no)\b", norm(str(answer))))
    qtype = q.get("type")
    if yn and qtype not in ("yes_no", "user_must_tick", "choice", None):
        return False
    if qtype == "yes_no" and not yn:
        return False
    prompt = norm(q.get("prompt", ""))
    return not prompt or len(t) <= 3 * max(len(prompt), 20)


def record(ws, question_text, answer, source="user_correction", note="", options_hint=None, qid=None):
    """Store/overwrite an answer. Learned entries (user_correction/user_filled) override everything for matching labels."""
    ws = ws if isinstance(ws, Workspace) else Workspace(ws)
    if is_ai_agent_question(question_text):
        return {"id": "ai_agent_disclosure", "stored": False, "note": AI_AGENT_NOTE}
    data = load_answers(ws)
    answers = data.setdefault("answers", [])
    q = match_question(question_text)
    t = norm(question_text)
    if q and source != "setup" and not _same_question(q, t, answer):
        q = None  # e.g. "Home Address (... postal code)" is not the ZIP question; keep a distinct id
    qid = qid or (q or {}).get("id") or "custom_" + re.sub(r"[^a-z0-9]+", "_", norm(question_text))[:40].strip("_")
    pat = auto_pattern(question_text)
    entry = None
    for a in answers:
        # reuse an existing learned entry only when it already holds the SAME answer; a different answer becomes a new
        # entry (lookup prefers the newest), so a look-alike question can never silently overwrite another answer
        if a.get("source") == source and a.get("patterns") and _match_any(a["patterns"], t)                 and norm(str(a.get("answer", ""))) == norm(str(answer)):
            entry = a
            break
    if entry is None and source == "setup":
        entry = next((a for a in answers if a.get("id") == qid and a.get("source") == "setup"), None)
    if entry is None:
        entry = {"id": qid, "patterns": [], "source": source}
        answers.append(entry)
    if source != "setup" and pat not in entry["patterns"]:
        entry["patterns"].append(pat)
    entry.update(answer=answer, date=date.today().isoformat(), note=note or entry.get("note", ""),
                 example=question_text[:200])
    if options_hint:
        entry["options_hint"] = options_hint
    save_json(_ws_bank_path(ws), data)
    return entry


# ---------------------------------------------------------------- verify
def verify_form(ws, fields):
    """fields: [{label, value, options?, required?, kind?('file'|...)}] read back from the page.
    Returns ok / mismatches / missing_required / unknown / user_must_tick / file_problems."""
    ws = ws if isinstance(ws, Workspace) else Workspace(ws)
    out = {"ok": [], "mismatches": [], "missing_required": [], "unknown": [], "user_must_tick": [], "essays": [], "file_problems": [], "hand_to_user": []}
    for f in fields or []:
        label, value = f.get("label", ""), (f.get("value") or "")
        nl, nv = norm(label), norm(str(value))
        # file uploads go in the right box
        if f.get("kind") == "file" or re.search(r"resume|\bcv\b|cover letter|attach", nl):
            if "cover letter" in nl and re.match(r"resume", nv):
                out["file_problems"].append({"label": label, "value": value, "problem": "Resume uploaded into the Cover Letter field"})
                continue
            if re.search(r"resume|\bcv\b", nl) and "cover" not in nl and re.match(r"cover", nv):
                out["file_problems"].append({"label": label, "value": value, "problem": "Cover letter uploaded into the Resume field"})
                continue
            if f.get("required") and not nv:
                out["missing_required"].append(label)
            else:
                out["ok"].append(label)
            continue
        if f.get("required") and not nv:
            out["missing_required"].append(label)
            continue
        r = lookup(ws, label, f.get("options"))
        if r["status"] == HAND:
            out["hand_to_user"].append(label)
            continue
        if r["status"] == TICK:
            out["user_must_tick"].append(label)
            continue
        if r["status"] == ESSAY:
            out["essays"].append(label)
            continue
        if r["status"] in (ASK, AMBIG):
            if nv:
                out["unknown"].append({"label": label, "value": value, "note": r["note"]})
            continue
        expected = r.get("option") or r["answer"]
        if not nv:
            out["mismatches"].append({"label": label, "expected": expected, "found": value, "problem": "empty"})
        elif equivalent(value, expected):
            out["ok"].append(label)
        else:
            out["mismatches"].append({"label": label, "expected": expected, "found": value, "source": r["source"]})
    out["pass"] = not (out["mismatches"] or out["missing_required"] or out["file_problems"] or out["unknown"])
    return out


# ---------------------------------------------------------------- final pre-submit review
PLACEHOLDER_RX = re.compile(r"^(select one|select\.*|choose\.*|choose one|please select\.*|--+|-select-|select an option)$")
MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
DATE_RANGE_RX = re.compile(r"(?P<a>\d{1,2}/\d{4}|[A-Za-z]{3,9}\.? \d{4})\s*(?:-|to)\s*(?P<b>\d{1,2}/\d{4}|[A-Za-z]{3,9}\.? \d{4}|present|current)", re.I)


def _ym(s):
    m = re.match(r"(\d{1,2})/(\d{4})", s)
    if m:
        return int(m.group(2)), int(m.group(1))
    m = re.match(r"([A-Za-z]{3})[A-Za-z]*\.? (\d{4})", s)
    if m and m.group(1).lower() in MONTHS:
        return int(m.group(2)), MONTHS.index(m.group(1).lower()) + 1
    return None


def _flag_rules(prof):
    """(regex, message) red flags that must never appear on a review page; personal ones come from the profile."""
    eeo = prof.get("eeo_voluntary", {}) or {}
    edu = json.dumps(prof.get("education", {}) or {}).lower()
    rules = [(r"\bi identify as (a|one or more)\b", "Veteran option 'I identify as a veteran...' is selected (should be a not-a-veteran / decline option)"),
             (r"\byes,? i have a disability\b", "Disability answered 'Yes, I have a disability'"),
             (r"\berror\b|\bmust not be empty\b|\binvalid\b", "The page shows an error / validation message")]
    if "arts" not in edu:
        rules.append((r"\bbachelor of arts\b", "Degree reads 'Bachelor of Arts'"))
    for pat, msg, key in ((r"\beast asian\b", "Race shows 'East Asian'", "race"), (r"\bamerican indian\b", "Race shows 'American Indian'", "race"),
                          (r"(?<![a-z])male\b", "Gender shows 'Male'", "gender")):
        if not re.search(pat, norm(str(eeo.get(key, "")))):
            rules.append((pat, msg))
    return rules


def _page_flags(prof, text):
    out = []
    text = text or ""
    for rx, msg in _flag_rules(prof):
        m = re.search(rx, text, re.I)
        if m:
            out.append(f"{msg} (found '{m.group(0)}')")
    # a current job must not carry an end date in the current/future month
    now = (date.today().year, date.today().month)
    for line in text.splitlines():
        if re.search(r"present|currently", line, re.I):
            continue
        for m in DATE_RANGE_RX.finditer(line):
            ym = _ym(m.group("b"))
            if ym and ym >= now:
                out.append(f"End date {m.group('b')} is in the current/future month - the current job should have no end date ('{line.strip()[:80]}')")
    # salary shown on the page must match the stored answer
    jp = prof.get("job_preferences", {}) or {}
    want = _num(str(jp.get("desired_salary_answer") or prof.get("desired_salary_answer") or ""))
    if want:
        for line in text.splitlines():
            if re.search(r"salary|compensation|pay expectation", line, re.I):
                got = _num(line)
                if got and not any(g == want[0] for g in got) and not (len(got) >= 2 and got[0] <= want[0] < got[1]):
                    out.append(f"Salary on page ({line.strip()[:80]}) does not match the stored answer ({want[0]})")
    return out


def final_review(ws, review):
    """Last check before submitting. review = {company, title, fields:[{label,value,required,options?,kind?}],
    files:[names], page_text: optional full review-page text}. Returns {ok, problems, summary_table}."""
    ws = ws if isinstance(ws, Workspace) else Workspace(ws)
    review = review or {}
    fields = review.get("fields") or []
    files = [str(f) for f in (review.get("files") or [])]
    company = review.get("company", "")
    prof = ws.profile() or {}
    problems = []
    # daily per-company cap (config/search.json max_per_company_per_day): count today's applied rows, any submitter
    cap = (load_json(ws.config / "search.json", {}) or {}).get("max_per_company_per_day", 2)
    ckey = lambda s: re.sub(r"\b(inc|llc|corp|corporation|co|company|ltd|enterprise|the)\b|[^a-z0-9]", "", norm(s or ""))
    if company and cap:
        today = date.today().isoformat()
        same = [r for r in ws.load_tracker() if r.get("status") == "applied" and (r.get("date_applied") or "")[:10] == today
                and ckey(r.get("company")) and (ckey(r.get("company")) in ckey(company) or ckey(company) in ckey(r.get("company")))
                and not (review.get("job_id") and r.get("job_id") == review.get("job_id"))]
        if len(same) >= cap:
            problems.append(f"Daily cap reached: already {len(same)} application(s) to {company} today "
                            f"({', '.join(r.get('title', '')[:40] for r in same)}); limit is {cap} - do not submit, ask the user")
    v = verify_form(ws, fields)
    ai_q = list(v.get("hand_to_user", []))
    if not ai_q and is_ai_agent_question(review.get("page_text", "")):
        ai_q = [AI_AGENT_RX.search(review.get("page_text", "")).group(0)[:120]]
    for lab in ai_q:
        problems.append(f"STOP - AI-agent disclosure question on the form: '{lab[:120]}'. Do not submit; notify the user "
                        "(they submit it themselves, or the agent answers truthfully that an AI agent is applying).")
    # "How did you hear" = "Other" is correct when the list has no company-site option and the form's own
    # "if Other, where?" follow-up names the careers site (e.g. "LogicGate careers website").
    other_detail = [f for f in fields if re.search(r"(choose|chose|select(ed)?) other|if other|please specify|where .{0,40}hear",
                                                       str(f.get("label", "")), re.I)
                    and re.search(r"careers? (web ?site|site|page)|company (web ?site|site)", str(f.get("value", "")), re.I)]
    for m in v["mismatches"]:
        if (other_detail and norm(str(m.get("found", ""))) == "other"
                and _canon(norm(str(m.get("expected", "")))) == "company careers website"):
            continue
        problems.append(f"Mismatch '{m['label']}': page has {m.get('found')!r}, expected {m.get('expected')!r}")
    for lab in v["missing_required"]:
        problems.append(f"Required field empty: {lab}")
    for f in v["file_problems"]:
        problems.append(f"File problem '{f['label']}': {f['problem']}")
    for u in v["unknown"]:
        problems.append(f"No stored answer for '{u['label']}' (value {u['value']!r}) - confirm with the user, then record_answer")
    for f in fields:
        val = str(f.get("value") or "").strip()
        if f.get("required") and val and PLACEHOLDER_RX.match(norm(val)):
            problems.append(f"Required field still a placeholder: {f.get('label')} = {val!r}")
    # files: resume carries the company slug; cover-letter slot holds a CoverLetter file
    cslug = re.sub(r"[^a-z0-9]", "", norm(company))[:30]
    flat = lambda n: re.sub(r"[^a-z0-9]", "", n.lower())
    is_cover = lambda n: bool(re.search(r"cover", n.replace("_", " "), re.I))
    slots = [(f, str(f.get("value") or "")) for f in fields
             if f.get("kind") == "file" or re.search(r"resume|\bcv\b|cover letter", norm(f.get("label", "")))]
    resume_names = [val for f, val in slots if re.search(r"resume|\bcv\b", norm(f.get("label", ""))) and "cover" not in norm(f.get("label", "")) and val]
    resume_names = resume_names or [n for n in files if re.search(r"resume|\bcv\b", n, re.I) and not is_cover(n)]
    cover_slots = [(f, val) for f, val in slots if "cover" in norm(f.get("label", ""))]
    folder = None
    if cslug and ws.apps.exists():
        folder = next((d for d in sorted(ws.apps.iterdir()) if d.is_dir() and flat(d.name.split("_")[0])[:12] == cslug[:12]), None)
    folder_resumes = {p.name.lower() for p in folder.glob("Resume*")} if folder else set()
    if not resume_names:
        problems.append("No resume file is attached")
    for n in resume_names:
        if is_cover(n):
            problems.append(f"Cover letter file '{n}' is in the resume slot")
        elif cslug and cslug not in flat(n) and n.lower() not in folder_resumes:
            problems.append(f"Resume file '{n}' does not contain the company name '{company}' - wrong tailored resume?")
    for f, val in cover_slots:
        if not val:
            if f.get("required"):
                problems.append("Cover-letter slot is empty")
        elif not is_cover(val) or "resume" in val.lower():
            problems.append(f"Cover-letter slot holds '{val}', not a CoverLetter file")
        elif cslug and cslug not in flat(val):
            problems.append(f"Cover letter '{val}' does not contain the company name '{company}'")
    problems += _page_flags(prof, review.get("page_text") or "")
    seen = set()
    problems = [p for p in problems if not (p in seen or seen.add(p))]
    bad = {m["label"] for m in v["mismatches"]} | set(v["missing_required"]) | {f["label"] for f in v["file_problems"]}
    table = [{"field": f.get("label", ""), "value": f.get("value", ""), "required": bool(f.get("required")),
              "status": "PROBLEM" if f.get("label") in bad else ("TICK-BY-USER" if f.get("label") in v["user_must_tick"] else "ok")}
             for f in fields]
    table += [{"field": "file", "value": n, "required": False, "status": "ok"} for n in files]
    return {"ok": not problems, "company": company, "title": review.get("title", ""), "problems": problems,
            "user_must_tick": v["user_must_tick"], "essays": v["essays"], "hand_to_user": ai_q, "summary_table": table}
