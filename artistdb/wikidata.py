"""Pull X handles of visual artists from Wikidata to start a crawl."""

import json
import re
import urllib.parse
import urllib.request

ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "artistdb/0.1 (research crawler)"

# occupation items on Wikidata
OCCUPATIONS = {
    "Q644687": "illustrator",
    "Q11079476": "digital artist",
    "Q28107590": "concept artist",
    "Q1062952": "character designer",
    "Q7199710": "pixel artist",
    "Q266569": "animator",
    "Q191633": "mangaka",
    "Q715301": "comics artist",
    "Q1114448": "cartoonist",
    "Q1925963": "graphic artist",
    "Q15296811": "draftsperson",
    "Q5762300": "storyboard artist",
    "Q1028181": "painter",
}
HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def artist_handles():
    """Return the distinct X usernames of people with an artist occupation."""
    values = " ".join(f"wd:{q}" for q in OCCUPATIONS)
    query = (
        "SELECT DISTINCT ?h WHERE { VALUES ?occ { " + values + " } "
        "?p wdt:P106 ?occ; wdt:P2002 ?h. }"
    )
    url = ENDPOINT + "?" + urllib.parse.urlencode({"query": query})
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/sparql-results+json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)
    handles = {b["h"]["value"].strip().lstrip("@") for b in data["results"]["bindings"]}
    return sorted(h for h in handles if HANDLE.match(h))
