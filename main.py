import requests
import json
import gzip
from collections import defaultdict, Counter
import re
import argparse

def get_new_files(headers, type):
    """Return the JSONL download URI for a Scryfall bulk-data set.

    Args:
        headers: Headers passed to ``requests.get``, e.g. a User-Agent and
            Accept pair as required by the Scryfall API.
        type: Bulk data type to look up, such as ``"oracle_cards"``,
            ``"default_cards"``, or ``"rulings"``.

    Returns:
        str: A time-limited URI pointing at the gzipped JSONL export.

    Raises:
        StopIteration: If no bulk-data entry matches ``type``.
    """
    response = requests.get("https://api.scryfall.com/bulk-data",headers=headers)
    uri = next(d['jsonl_download_uri'] for d in response.json()['data'] if d['type'] == type)
    return uri

def build_name_to_oracle_id_map(uri,headers):
    """Map card names to oracle IDs from a bulk-data export.

    Records missing either key are reported to stdout and skipped. Names are
    not unique across printings, so later records overwrite earlier ones.

    Args:
        uri: URI of a gzipped JSONL bulk-data file.
        headers: Headers passed through to the HTTP request.

    Returns:
        dict[str, str]: Card name to oracle ID.
    """
    name_to_oracle_id = {}
    for record in stream_jsonl_gz_url(uri, headers = headers):
        try:
            name_to_oracle_id[record['name']] = record['oracle_id']
        except KeyError:
            print(f"  (skipping record with missing name or oracle_id: {record})")
    return name_to_oracle_id

def build_oracle_index(tag_jsons):
    """Invert tag records into a lookup keyed by oracle ID.

    Args:
        tag_jsons: Iterable of tag objects, each with ``label``, ``id``, and a
            ``taggings`` list whose entries carry an ``oracle_id``.

    Returns:
        defaultdict[str, list[dict]]: Oracle ID to a list of
        ``{"label", "tag_id"}`` dicts. Cards with no tags are absent.
    """
    index = defaultdict(list)
    for tag in tag_jsons:
        for tagging in tag["taggings"]:
            index[tagging["oracle_id"]].append({
                "label": tag["label"],
                "tag_id": tag["id"]
            })
    return index

def stream_jsonl_gz_url(url, headers):
    """Yield objects from a gzipped JSONL file one line at a time.

    Decompression is handled by :mod:`gzip` against the raw socket stream, so
    the file is never held in memory in full.

    Args:
        url: URL of the gzipped JSONL resource.
        headers: Headers passed through to the HTTP request.

    Yields:
        dict: One decoded JSON object per non-empty line.

    Raises:
        requests.HTTPError: If the response status is 4xx or 5xx.
    """
    with requests.get(url, stream=True, headers=headers) as resp:
        resp.raise_for_status()
        resp.raw.decode_content = False  # let gzip.GzipFile do the decompression, not requests
        with gzip.GzipFile(fileobj=resp.raw) as gz:
            for line in gz:
                line = line.strip()
                if line:
                    yield json.loads(line)
                    

def extract_card_names(decklist_text: str):
    """Parse card names and quantities out of a decklist.

    Handles the Moxfield export format and falls back to plain
    ``<qty> <name>`` lines::

        1 Inalla, Archmage Ritualist (C17) 38 *F*
        3 Island (ELD) 255

    Unparseable lines are reported to stdout and skipped.

    Args:
        decklist_text: Raw decklist, one card per line.

    Returns:
        tuple[list[str], dict[str, int]]: Every card name in file order, and a
        mapping of name to quantity for entries with a quantity above one.
    """
    moxfield_pattern = re.compile(r'^(\d+)\s+(.+?)\s+\([A-Za-z0-9]{2,6}\)\s+\S+(?:\s*\*[EF]\*)?\s*$')
    plaintext_pattern = re.compile(r'^(\d+)\s+(.+?)$')
    names = []
    multiples = {}
    for line in decklist_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        m = moxfield_pattern.match(line)
        fail_m = plaintext_pattern.match(line)
        if m:
            names.append(m.group(2).strip())
            if int(m.group(1))>1:
                multiples[m.group(2).strip()] = int(m.group(1))
        elif fail_m:
            names.append(fail_m.group(2).strip())
            if int(fail_m.group(1))>1:
                multiples[fail_m.group(2).strip()] = int(fail_m.group(1))
        else:
            print(f"  (unmatched line, skipped: {line!r})")
    return names, multiples

def cache_oracle_index(oracle_index,name_to_oracle_id, filename='cache.json'):
    """Write the oracle index and name map to a JSON file.

    Args:
        oracle_index: Oracle ID to tag list, from :func:`build_oracle_index`.
        name_to_oracle_id: Card name to oracle ID.
        filename: Destination path. Overwritten if it already exists.
    """
    data = {
        "oracle_index": oracle_index,
        "name_to_oracle_id": name_to_oracle_id
    }
    with open(filename, 'w') as f:
        json.dump(data, f)

def count_tags(cards, oracle_index):
    """Tally how many cards carry each tag label.

    Args:
        cards: Iterable of oracle IDs. Cards absent from the index contribute
            nothing.
        oracle_index: Oracle ID to tag list, from :func:`build_oracle_index`.

    Returns:
        Counter[str]: Tag label to the number of cards carrying it.
    """
    counts = Counter()
    for card in cards:
        for tagging in oracle_index.get(card, []):
            counts[tagging["label"]] += 1
    return counts

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-f","--file",required=True, help="Use the file you would like to query")
    parser.add_argument("-c","--cache",required=False, help="Use the cache file you would like to query")
    parser.add_argument("--outfile",required=False, help="output file to save the results (default: print to stdout)")
    parser.add_argument("--cacheout",required=False, help="Where to save the cache file")
    args = parser.parse_args()

    with open(args.file, 'r') as f:
        decklist_text = f.read()    

    headers = requests.utils.default_headers()
    headers.update(
        {
            'User-Agent': 'Scryfall Decklist Tag Counter',
        }
    )
    if args.cache:
        with open(args.cache, 'r') as f:
            cache = json.load(f)
        oracle_index = cache["oracle_index"]
        name_to_oracle_id = cache["name_to_oracle_id"]
    else:
        cards_uri = get_new_files(headers, "oracle_cards")
        tags_uri = get_new_files(headers, "oracle_tags")
        oracle_data =[]
        for record in stream_jsonl_gz_url(tags_uri, headers = headers):
            oracle_data.append(record)
        name_to_oracle_id = build_name_to_oracle_id_map(cards_uri, headers)
        oracle_index = build_oracle_index(oracle_data)

    decklist, multiples = extract_card_names(decklist_text)

    decklist_oracle = [name_to_oracle_id.get(name) for name in decklist if name in name_to_oracle_id]
    for name in multiples.keys():
        for _ in range(multiples[name]-1):
            decklist_oracle.append(name_to_oracle_id.get(name))
    counted_data = count_tags(decklist_oracle,oracle_index)
    if args.cacheout:
        cache_oracle_index(oracle_index,name_to_oracle_id, filename=args.cacheout)
    elif args.cache == None:
        cache_oracle_index(oracle_index,name_to_oracle_id)

    if args.outfile:
        with open(args.outfile, 'w') as f:
            json.dump(counted_data, f)
    else:
        print(counted_data)