"""Small lookup tables shared by the mock provider and the output checks."""
import re

COUNTRY_ALIASES = {
    "NG": ["nigeria", "nigerian"],
    "GH": ["ghana", "ghanaian"],
    "KE": ["kenya", "kenyan"],
    "ZA": ["south africa", "south african"],
    "GB": ["united kingdom", "uk", "britain", "british"],
    "US": ["united states", "usa", "america", "american"],
    "CA": ["canada", "canadian"],
    "DE": ["germany", "german"],
    "IN": ["india", "indian"],
}
DEMONYMS = {
    "nigerian": "NG", "ghanaian": "GH", "kenyan": "KE", "south african": "ZA", "british": "GB",
    "american": "US", "canadian": "CA", "german": "DE", "indian": "IN",
}
LEVEL_ORDER = ["secondary", "diploma", "bachelor", "master", "doctorate"]
LEVEL_KEYWORDS = {
    "secondary": ["secondary school", "high school", "waec", "jamb", "ssce", "a-level", "a level"],
    "diploma": ["diploma", "hnd"],
    "bachelor": ["bachelor", "bachelors", "bachelor's", "undergraduate", "bsc", "b.sc"],
    "master": ["master", "masters", "master's", "msc", "m.sc", "postgraduate"],
    "doctorate": ["phd", "doctorate", "doctoral"],
}
KNOWN_SKILLS = [
    "python", "java", "javascript", "typescript", "sql", "git", "docker", "kubernetes", "aws", "azure",
    "excel", "power bi", "tableau", "figma", "react", "node.js", "django", "flask", "fastapi", "html",
    "css", "c++", "c#", "rust", "machine learning", "data analysis", "data science", "statistics",
    "communication", "writing", "project management", "research", "teaching", "accounting",
    "marketing", "seo", "social media", "photoshop",
]
REMOTE_KEYWORDS = {
    "remote": ["remote", "work from home"],
    "hybrid": ["hybrid"],
    "onsite": ["on-site", "onsite", "in-person", "in person"],
    "any": ["any work arrangement", "flexible about where"],
}
AID_YES = ["need financial aid", "need financial support", "need funding", "need a scholarship",
           "cannot afford", "can't afford", "financial aid"]
AID_NO = ["do not need financial", "don't need financial", "self-funded", "self funded",
          "do not need funding", "don't need funding"]


def contains_term(text: str, term: str) -> bool:
    """Whole-word, case-insensitive search ('c' does not match 'cat'; 'c++' works)."""
    return re.search(rf"(?<![\w+#]){re.escape(term.lower())}(?![\w+#])", text.lower()) is not None