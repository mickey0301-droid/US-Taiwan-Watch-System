from __future__ import annotations

from urllib.parse import quote_plus


def build_google_official_search_url(full_name: str, office_name: str | None = None) -> str:
    pieces = [f'"{full_name}"']
    if office_name:
        pieces.append(f'"{office_name}"')
    pieces.append("site:.gov OR site:senate.gov OR site:house.gov OR site:whitehouse.gov")
    query = " ".join(piece for piece in pieces if piece)
    return f"https://www.google.com/search?q={quote_plus(query)}"


def build_google_official_bio_search_url(full_name: str, office_name: str | None = None) -> str:
    pieces = [f'"{full_name}"']
    if office_name:
        pieces.append(f'"{office_name}"')
    pieces.append("official biography site:.gov")
    query = " ".join(piece for piece in pieces if piece)
    return f"https://www.google.com/search?q={quote_plus(query)}"


def build_x_search_url(full_name: str, office_name: str | None = None) -> str:
    pieces = [f'"{full_name}"']
    if office_name:
        pieces.append(f'"{office_name}"')
    pieces.append("(senator OR representative OR governor OR secretary OR official)")
    pieces.append("(site:x.com OR site:twitter.com)")
    query = " ".join(piece for piece in pieces if piece)
    return f"https://www.google.com/search?q={quote_plus(query)}"


def build_google_federal_executive_search_urls(full_name: str, office_name: str | None = None, department_name: str | None = None) -> dict[str, str]:
    office_fragment = office_name or ""
    whitehouse_query = f'"{full_name}" "{office_fragment}" site:whitehouse.gov'
    queries: dict[str, str] = {
        "official_search": build_google_official_search_url(full_name, office_name),
        "official_bio_search": build_google_official_bio_search_url(full_name, office_name),
        "whitehouse_search": f"https://www.google.com/search?q={quote_plus(whitehouse_query)}",
    }
    department_domain = _department_domain(department_name or office_name)
    if department_domain:
        department_query = f'"{full_name}" "{office_fragment}" site:{department_domain}'
        queries["department_search"] = f"https://www.google.com/search?q={quote_plus(department_query)}"
    return queries


def _department_domain(department_name: str | None) -> str | None:
    if not department_name:
        return None
    lowered = department_name.lower()
    mapping = {
        "secretary of state": "state.gov",
        "secretary of the treasury": "treasury.gov",
        "secretary of defense": "defense.gov",
        "secretary of agriculture": "usda.gov",
        "secretary of commerce": "commerce.gov",
        "secretary of labor": "dol.gov",
        "secretary of health and human services": "hhs.gov",
        "secretary of transportation": "transportation.gov",
        "secretary of energy": "energy.gov",
        "secretary of veterans affairs": "va.gov",
        "secretary of the interior": "doi.gov",
        "secretary of education": "ed.gov",
        "secretary of homeland security": "dhs.gov",
        "secretary of housing and urban development": "hud.gov",
        "department of state": "state.gov",
        "department of the treasury": "treasury.gov",
        "department of defense": "defense.gov",
        "department of justice": "justice.gov",
        "department of agriculture": "usda.gov",
        "department of commerce": "commerce.gov",
        "department of labor": "dol.gov",
        "department of health and human services": "hhs.gov",
        "department of transportation": "transportation.gov",
        "department of energy": "energy.gov",
        "department of veterans affairs": "va.gov",
        "department of the interior": "doi.gov",
        "department of education": "ed.gov",
        "department of homeland security": "dhs.gov",
        "department of housing and urban development": "hud.gov",
        "environmental protection agency": "epa.gov",
        "small business administration": "sba.gov",
        "office of management and budget": "whitehouse.gov",
        "office of the director of national intelligence": "dni.gov",
        "central intelligence agency": "cia.gov",
        "united states mission to the united nations": "usun.usmission.gov",
        "office of the united states trade representative": "ustr.gov",
    }
    for key, domain in mapping.items():
        if key in lowered:
            return domain
    return None
