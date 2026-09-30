import re
import difflib
from typing import List, Optional


def clean_story_title(title: Optional[str]) -> str:
    """
    Cleans and standardizes a story title from sheets, files, or user input.
    - Strips Google Drive archive/export timestamps (e.g. -20260720T094718Z-1-001).
    - Normalizes curly quotes, apostrophes, and accents.
    - Converts dashes and underscores used in place of apostrophes (e.g. Don-t -> Don't, Jack-s -> Jack's, Tommy_s -> Tommy's).
    - Removes trailing punctuation and trailing underscores.
    - Replaces underscores between words with spaces.
    - Collapses consecutive whitespace.
    """
    if not title:
        return ""

    s = str(title).strip()

    # 1. Strip Google Drive export/download timestamp suffixes
    # Patterns like: -20260720T094718Z-1-001 or _20260720T094718Z or -20260720T094718Z-3-001
    s = re.sub(r'[-_]\d{8}T\d{6}Z.*$', '', s, flags=re.IGNORECASE).strip()

    # 2. Strip file extensions if present
    s = re.sub(r'\.(docx?|pdf|gdoc)$', '', s, flags=re.IGNORECASE).strip()

    # 3. Normalize unicode apostrophes, single quotes, backticks, acute accents
    s = re.sub(r"[’‘`´ʻʼʹ]", "'", s)

    # 4. Normalize unicode double quotes
    s = re.sub(r'[“”«»″]', '"', s)

    # 5. Normalize unicode dashes/hyphens
    s = re.sub(r'[–—−‒―]', '-', s)

    # 6. Convert dashes or underscores used in place of apostrophes:
    # Common English contractions and possessives: -s, -t, -d, -m, -ll, -ve, -re
    # e.g., Don-t -> Don't, Can-t -> Can't, Jack-s -> Jack's, Tommy_s -> Tommy's, It-s -> It's
    s = re.sub(
        r"(?<=[a-zA-Z])[-_](s|t|d|m|ll|ve|re)\b",
        r"'\1",
        s,
        flags=re.IGNORECASE,
    )

    # 7. Strip balanced outer quotes if the whole title is wrapped in quotes
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()

    # 8. Remove trailing underscores, hyphens, colons, commas, or dangling quotes
    s = re.sub(r'[-_,\.:;!?]+$', '', s).strip()
    if s.endswith('"') and not s.startswith('"'):
        s = s[:-1].strip()
    if s.endswith("'") and not s.startswith("'") and not s.endswith("s'"):
        s = s[:-1].strip()

    # 9. Replace remaining underscores with spaces (e.g. Word_Another -> Word Another)
    s = re.sub(r'_+', ' ', s)

    # 10. Collapse multiple whitespaces
    s = re.sub(r'\s+', ' ', s).strip()

    return s


def canonical_title_key(title: Optional[str]) -> str:
    """
    Generates a canonical matching key by stripping all punctuation,
    spaces, and non-alphanumeric characters and converting to lowercase.
    This guarantees that 'Don't Look Back', 'Don-t Look Back', 'Dont Look Back',
    'Don’t Look Back', and 'Don t Look Back' all produce the exact same key: 'dontlookback'.
    """
    cleaned = clean_story_title(title)
    return "".join(c for c in cleaned.casefold() if c.isalnum())


def calculate_title_similarity(title1: Optional[str], title2: Optional[str]) -> float:
    """
    Computes a similarity score [0.0 - 1.0] between two story titles,
    resilient to punctuation differences, dashes, apostrophes, and subtitles.
    """
    key1 = canonical_title_key(title1)
    key2 = canonical_title_key(title2)

    if not key1 or not key2:
        return 0.0

    if key1 == key2:
        return 1.0

    # Substring / subtitle containment
    if key1 in key2 or key2 in key1:
        shorter_len = min(len(key1), len(key2))
        longer_len = max(len(key1), len(key2))
        if longer_len > 0 and (shorter_len / longer_len) >= 0.5:
            return 0.92

    return difflib.SequenceMatcher(None, key1, key2).ratio()


def titles_match(title1: Optional[str], title2: Optional[str], threshold: float = 0.85) -> bool:
    """Returns True if title1 and title2 refer to the same story."""
    return calculate_title_similarity(title1, title2) >= threshold


def matches_search_query(query: Optional[str], title: Optional[str]) -> bool:
    """
    Checks if a user's search query matches a story title.
    Designed for search bars in GUI and CLI:
    - Case-insensitive
    - Matches across punctuation, dashes, apostrophes, commas
    - Supports multi-word queries where all query tokens appear in title
    """
    if not query or not query.strip():
        return True
    if not title or not title.strip():
        return False

    q_raw = query.strip().casefold()
    t_raw = title.strip().casefold()

    # Fast direct substring
    if q_raw in t_raw:
        return True

    q_clean = clean_story_title(query)
    t_clean = clean_story_title(title)

    if q_clean.casefold() in t_clean.casefold():
        return True

    q_key = canonical_title_key(query)
    t_key = canonical_title_key(title)

    if not q_key:
        return True

    # Canonical substring match
    if q_key in t_key:
        return True

    # Token-based match: all query tokens must match a word in title
    # e.g., 'spider man' matches "Spider-Man's Day"
    q_tokens = [canonical_title_key(w) for w in re.split(r'[\s\-_,\.:;!?\'"\(\)\[\]]+', query) if w.strip()]
    q_tokens = [tok for tok in q_tokens if tok]

    t_tokens = [canonical_title_key(w) for w in re.split(r'[\s\-_,\.:;!?\'"\(\)\[\]]+', title) if w.strip()]
    t_tokens = [tok for tok in t_tokens if tok]

    if q_tokens and all(any(q_tok in t_tok for t_tok in t_tokens) for q_tok in q_tokens):
        return True

    return False


def generate_readora_search_queries(story_name: Optional[str]) -> List[str]:
    """
    Generates a prioritized list of search queries to send to Readora's search bar.
    Tries natural clean title first, followed by variations that handle punctuation,
    apostrophes, dashes, commas, and subtitles.
    """
    if not story_name:
        return []

    clean = clean_story_title(story_name)
    if not clean:
        return []

    queries: List[str] = []

    def _add(cand: str):
        c = re.sub(r'\s+', ' ', cand).strip()
        if c and c.casefold() not in [q.casefold() for q in queries]:
            queries.append(c)

    # 1. Cleaned title with standard apostrophes and stripped timestamps
    _add(clean)

    # 2. If title has a subtitle after colon, dash, or em-dash, try primary title
    # e.g. "The Rock Cycle: Earth's Stones" -> "The Rock Cycle"
    sub_parts = re.split(r'[:–—]|(?:\s+-\s+)', clean)
    if len(sub_parts) > 1 and len(sub_parts[0].strip()) >= 4:
        _add(sub_parts[0].strip())

    # 3. Title with commas, colons, quotes, and brackets stripped
    no_punct = re.sub(r'[,:;!?"\(\)\[\]]+', ' ', clean)
    _add(no_punct)

    # 4. Title with dashes replaced by space (e.g. Spider-Man -> Spider Man)
    no_dash = re.sub(r'[-_]+', ' ', no_punct)
    _add(no_dash)

    # 5. Title with apostrophes removed entirely (e.g. Benny's -> Bennys, Don't -> Dont)
    no_apos = re.sub(r"[']+", '', no_dash)
    _add(no_apos)

    # 6. Title with apostrophes converted to space (e.g. Benny's -> Benny s)
    apos_space = re.sub(r"[']+", ' ', no_dash)
    _add(apos_space)

    # 7. First 3-4 significant words (for long titles where search backend truncates or chokes)
    words = [
        w for w in re.findall(r'[a-zA-Z0-9]+', clean)
        if len(w) > 2 and w.lower() not in {'the', 'and', 'for', 'with', 'that', 'from', 'into'}
    ]
    if len(words) >= 2:
        _add(" ".join(words[:4]))

    return queries
