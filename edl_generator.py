#!/usr/bin/env python3
import argparse
import ipaddress
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from urllib.request import urlopen

# Microsoft "Azure IP Ranges and Service Tags – Public Cloud" download page
DETAILS_URL = "https://www.microsoft.com/en-us/download/details.aspx?id=56519"
CONFIRM_URL = "https://www.microsoft.com/en-us/download/confirmation.aspx?id=56519"

# Look for the direct JSON download link, e.g.
# https://download.microsoft.com/download/.../ServiceTags_Public_20251117.json
JSON_URL_PATTERN = (
    r"https://download\.microsoft\.com/download/[^\"]*ServiceTags_Public_[0-9]+\.json"
)
DEFAULT_URL_BASE = os.environ.get("EDL_URL_BASE")  # Optional override via env for CI


def compute_default_url_base() -> str:
    """
    Build a raw.githubusercontent.com base URL using CI context when available,
    otherwise fall back to the upstream repo path.
    """
    repo = os.environ.get("GITHUB_REPOSITORY")
    ref = os.environ.get("GITHUB_REF_NAME")
    if repo and ref:
        return f"https://raw.githubusercontent.com/{repo}/{ref}"
    return (
        "https://raw.githubusercontent.com/"
        "netaryx/palo-alto-azure-servicetag-edl/main"
    )


def fetch_url(url: str) -> str:
    """Fetch a URL and return its content as text."""
    with urlopen(url) as resp:
        content_bytes = resp.read()
    return content_bytes.decode("utf-8", errors="ignore")


def find_json_url(html: str) -> Optional[str]:
    """Extract the ServiceTags_Public_*.json URL from the HTML."""
    match = re.search(JSON_URL_PATTERN, html)
    if match:
        return match.group(0)
    return None


def download_servicetags_json(save_path: Optional[Path] = None) -> dict:
    """
    Download the current ServiceTags_Public_*.json file by:
      1. Fetching the Download Center details page
      2. Falling back to the confirmation page (if needed)
      3. Grabbing the first matching ServiceTags_Public_*.json download link
    """
    # Try details page first
    html = fetch_url(DETAILS_URL)
    json_url = find_json_url(html)

    if not json_url:
        # Fallback to confirmation page if needed
        html = fetch_url(CONFIRM_URL)
        json_url = find_json_url(html)

    if not json_url:
        raise RuntimeError(
            "Could not find ServiceTags_Public JSON URL on Microsoft download pages."
        )

    print(f"Found ServiceTags JSON URL: {json_url}", file=sys.stderr)

    with urlopen(json_url) as resp:
        data_bytes = resp.read()

    if save_path is not None:
        save_path.write_bytes(data_bytes)
        print(f"Saved raw ServiceTags JSON to {save_path}", file=sys.stderr)

    data = json.loads(data_bytes.decode("utf-8"))
    return data


def extract_values(root: dict) -> List[dict]:
    """
    Extract the list of service tag entries from the JSON.
    The weekly file uses 'values'; some APIs use 'value', so be tolerant.
    """
    values = root.get("values") or root.get("value")
    if not isinstance(values, list):
        raise ValueError(
            "ServiceTags JSON does not contain a 'values' or 'value' list."
        )
    return values


def normalise_filename(tag_name: str) -> str:
    # Keep tag name largely as-is; just avoid spaces.
    return tag_name.replace(" ", "_")


def split_prefixes_by_ip_version(prefixes: Iterable[str]) -> Tuple[List[str], List[str]]:
    ipv4: List[str] = []
    ipv6: List[str] = []

    for pfx in prefixes:
        try:
            network = ipaddress.ip_network(pfx, strict=False)
        except ValueError:
            # Skip unknown formats while still writing them to the combined file.
            print(f"Skipping unrecognised address prefix: {pfx}", file=sys.stderr)
            continue

        if network.version == 4:
            ipv4.append(pfx)
        else:
            ipv6.append(pfx)

    return ipv4, ipv6


def write_prefix_file(path: Path, prefixes: Iterable[str]) -> int:
    prefixes_list = list(prefixes)
    with path.open("w", encoding="utf-8") as f:
        for pfx in prefixes_list:
            f.write(f"{pfx}\n")
    return len(prefixes_list)


def build_edls(
    values: Iterable[dict],
    output_dir: Path,
    include_tags: Optional[List[str]] = None,
    exclude_tags: Optional[List[str]] = None,
) -> List[Tuple[str, str]]:
    """
    Write one .txt file per service tag, with one address prefix per line.
    Returns a list of (tag_name, base_filename) entries.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    written_entries: List[Tuple[str, str]] = []

    for tag in values:
        name = tag.get("name")
        props = tag.get("properties", {})
        prefixes = props.get("addressPrefixes", [])

        if not name or not prefixes:
            continue

        if include_tags and name not in include_tags:
            continue
        if exclude_tags and name in exclude_tags:
            continue

        base_name = normalise_filename(name)
        base_path = output_dir / f"{base_name}.txt"
        ipv4_path = output_dir / f"{base_name}-v4.txt"
        ipv6_path = output_dir / f"{base_name}-v6.txt"

        total_count = write_prefix_file(base_path, prefixes)
        ipv4_prefixes, ipv6_prefixes = split_prefixes_by_ip_version(prefixes)
        ipv4_count = write_prefix_file(ipv4_path, ipv4_prefixes)
        ipv6_count = write_prefix_file(ipv6_path, ipv6_prefixes)

        written_entries.append((name, base_name))

        print(
            f"Wrote {base_path} (total={total_count}, v4={ipv4_count}, v6={ipv6_count})",
            file=sys.stderr,
        )

    return written_entries


def write_url_index(
    entries: Iterable[Tuple[str, str]],
    output_dir: Path,
    base_url: str,
    index_path: Path,
) -> None:
    """
    Write a simple CSV (tag,url,type) for each generated EDL file.
    """
    base_url = base_url.rstrip("/")
    relative_dir = output_dir.as_posix().strip("/")
    url_prefix = f"{base_url}/{relative_dir}" if relative_dir else base_url

    lines: List[str] = []
    for name, base_name in sorted(entries, key=lambda item: item[0].lower()):
        for label, suffix in (("all", ""), ("ipv4", "-v4"), ("ipv6", "-v6")):
            filename = f"{base_name}{suffix}.txt"
            url = f"{url_prefix}/{filename}"
            lines.append(f"{name},{url},{label}")

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote URL index to {index_path}", file=sys.stderr)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download Azure ServiceTags_Public JSON and build "
            "PAN-EDL-compatible IP list files."
        )
    )
    default_url_base = DEFAULT_URL_BASE or compute_default_url_base()
    parser.add_argument(
        "output_dir",
        help="Directory where EDL .txt files will be written (one file per service tag).",
    )
    parser.add_argument(
        "--include-tags",
        nargs="+",
        help="Optional list of service tag names to include (default: all tags).",
    )
    parser.add_argument(
        "--exclude-tags",
        nargs="+",
        help="Optional list of service tag names to exclude.",
    )
    parser.add_argument(
        "--save-json",
        metavar="PATH",
        help="Optional path to also save the raw ServiceTags_Public JSON file.",
    )
    parser.add_argument(
        "--url-index",
        metavar="PATH",
        help="Optional path to also write a comma-separated index of EDL URLs.",
    )
    parser.add_argument(
        "--url-base",
        default=default_url_base,
        help=(
            "Base URL for constructing links in the URL index "
            f"(default: env EDL_URL_BASE/GITHUB context or {default_url_base})."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    output_dir = Path(args.output_dir)
    save_json_path = Path(args.save_json) if args.save_json else None

    root = download_servicetags_json(save_path=save_json_path)
    values = extract_values(root)

    entries = build_edls(
        values,
        output_dir=output_dir,
        include_tags=args.include_tags,
        exclude_tags=args.exclude_tags,
    )

    if args.url_index:
        index_path = Path(args.url_index)
        write_url_index(
            entries,
            output_dir=output_dir,
            base_url=args.url_base,
            index_path=index_path,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
