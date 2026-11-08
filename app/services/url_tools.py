from urllib.parse import parse_qs, quote, unquote, urlparse


def parse_url(url):
    parsed = urlparse(url)
    return {
        "scheme": parsed.scheme,
        "host": parsed.netloc,
        "path": parsed.path,
        "query": {key: values for key, values in parse_qs(parsed.query, keep_blank_values=True).items()},
        "fragment": parsed.fragment,
    }


def encode_url(text):
    return quote(text, safe="/:?=&%#@+$,;[]")


def decode_url(text):
    return unquote(text)
