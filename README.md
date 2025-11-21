# Azure Service Tag EDL Generator

Builds Palo Alto Networks External Dynamic Lists (EDLs) from Microsoft's weekly `ServiceTags_Public_*.json` feed.

## Running locally
Requires Python 3.11+

```bash
# Generate all EDLs, save the raw JSON, and produce the URL index
# Set your desired raw GitHub base (defaults to repo/branch when running in GitHub Actions)
URL_BASE="https://raw.githubusercontent.com/<owner>/<repo>/<branch>"

python3 edl_generator.py edl/ \
  --save-json ServiceTags_Public_latest.json \
  --url-index edl-urls.txt \
  --url-base "$URL_BASE"
```

Options:
- `--include-tags TagA TagB` to build only selected tags.
- `--exclude-tags TagA TagB` to omit specific tags.
- `--save-json PATH` to save the downloaded JSON.
- `--url-index PATH` and `--url-base URL` to control where the URL CSV is written and what base URL to use.

Generated artifacts:
- `edl/` — per-tag allowlists with IPv4/IPv6 split variants.
- `ServiceTags_Public_latest.json` — latest raw feed (if `--save-json` used).
- `edl-urls.txt` — CSV index of all public URLs for the generated EDL files.

## Automation
`.github/workflows/build_edl.yml` runs weekly (and on manual dispatch) to refresh the EDLs and commit `edl/`, `edl-urls.txt`, and `ServiceTags_Public_latest.json` back to the repository.
