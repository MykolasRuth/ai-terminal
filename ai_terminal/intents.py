import re


PROJECT_NOUNS = {
    "project",
    "projects",
    "repo",
    "repos",
    "repository",
    "repositories",
    "codebase",
    "codebases",
    "app",
    "apps",
}

LIST_WORDS = {
    "list",
    "show",
    "display",
    "see",
    "view",
    "what",
    "which",
    "where",
    "have",
    "available",
    "current",
    "currently",
}

OPEN_WORDS = {
    "open",
    "launch",
    "access",
    "edit",
    "work",
}

FILLER_WORDS = {
    "can",
    "you",
    "please",
    "my",
    "the",
    "a",
    "an",
    "in",
    "with",
    "to",
    "on",
    "for",
    "me",
    "i",
    "do",
    "currently",
    "have",
    "available",
}

EDITOR_WORD_PATTERN = re.compile(r"(?i)\b(?:vs\s*code|vscode|vsc|visual studio code)\b")


def normalize_text(text):
    return re.sub(r"\s+", " ", text.lower()).strip(" .!?")


def words(text):
    return set(re.findall(r"[a-z0-9]+", normalize_text(text)))


def is_project_listing_request(prompt):
    normalized = normalize_text(prompt)
    if re.search(r"\bwhat does .+\bproject\b.*\bdo\b", normalized):
        return False
    if re.search(r"\btell me about .+\bproject\b", normalized):
        return False

    tokens = words(prompt)
    plural_project_words = {"projects", "repos", "repositories", "codebases", "apps"}
    has_plural_target = bool(tokens & plural_project_words)
    has_listing_phrase = bool(
        re.search(
            r"\b(?:what|which|show|list|display|view|see)\b.*\b(?:projects|repos|repositories|codebases|apps)\b",
            normalized,
        )
    )
    return (
        (has_plural_target or has_listing_phrase)
        and bool(tokens & LIST_WORDS)
        and not bool(tokens & OPEN_WORDS)
    )


def is_project_open_request(prompt):
    normalized = normalize_text(prompt)
    if normalized.startswith(("how ", "how can ", "how do ", "how would ")):
        return False

    tokens = words(prompt)
    mentions_project = bool(tokens & PROJECT_NOUNS)
    mentions_editor = bool(EDITOR_WORD_PATTERN.search(prompt))
    return bool(tokens & OPEN_WORDS) and (mentions_project or mentions_editor)


def extract_project_reference(prompt):
    text = normalize_text(prompt)
    text = EDITOR_WORD_PATTERN.sub("", text)

    for word in sorted(OPEN_WORDS | PROJECT_NOUNS | FILLER_WORDS, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(word)}\b", " ", text)

    return re.sub(r"\s+", " ", text).strip(" -_")
