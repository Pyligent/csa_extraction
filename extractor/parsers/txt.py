from typing import Dict


def parse_txt(text: str) -> Dict:
    # Reuse the HTML parser's robust anchor and table logic on plain text.
    # BeautifulSoup in parse_html will treat this as plain text and extract
    # anchors/sections accordingly; haircut tables will simply abstain.
    from .html import parse_html

    return parse_html(text)


