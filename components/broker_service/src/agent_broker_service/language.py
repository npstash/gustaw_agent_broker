"""The language of a reply, for a provider whose agent does not give it (the provider opencode)."""
from __future__ import annotations

from collections.abc import Callable

from lingua import Language, LanguageDetectorBuilder

LINGUA: dict[str, Language] = {"en": Language.ENGLISH, "pl": Language.POLISH}


def detector(languages: tuple[str, ...], threshold: float) -> Callable[[str], str | None]:
    """A function that gives the language of a text, or None below the threshold or for an empty text."""
    known = [LINGUA[code] for code in languages if code in LINGUA]
    built = LanguageDetectorBuilder.from_languages(*known).build() if len(known) >= 2 else None
    codes = {language: code for code, language in LINGUA.items()}

    def detect(text: str) -> str | None:
        if not text.strip():
            return None
        if built is None:
            return languages[0] if languages else None
        values = built.compute_language_confidence_values(text)
        if not values or values[0].value < threshold:
            return None
        return codes.get(values[0].language)

    return detect
