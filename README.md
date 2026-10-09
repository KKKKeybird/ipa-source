# IPA Source

A small, dependency-free PlayCover subscription service for your own IPA files.
Supports local IPA files, remote URLs and OpenList storage. OpenList URLs are
resolved on each download request and returned as HTTP redirects: no large-file
proxying through this service, and no stale signed URLs cached in the subscription.

## Run

Use Python 3.10+ and an HTTPS reverse proxy. Copy `config.example.json` to
`data/config.json`, set your paths, public base URL and OpenList API token. Generate
two separate random tokens using `secrets.token_urlsafe(32)` and protect the config
with mode 600. Never commit your real configuration or IPA binaries.

```sh
mkdir -p data
cp config.example.json data/config.json
chmod 600 data/config.json
# Edit configuration and make data writable by UID 1000.
docker compose up -d --build
```

Without Docker, set actual local paths and run:

```sh
python3 ipa_source.py --config data/config.json serve
```

If the proxy uses `/ipa/`, strip that prefix before forwarding and include it in
`public_base`. Disable proxy access logging for capability URLs if needed.

## Import

```sh
# Read actual bundle ID, display name and version; copy to content-addressed storage.
python3 ipa_source.py --config data/config.json add --file /path/to/App.ipa
# Remote URL: download to temporary storage to read IPA metadata.
python3 ipa_source.py --config data/config.json add --url https://example.com/App.ipa
# Supply known metadata to avoid downloading a large remote file.
python3 ipa_source.py --config data/config.json add \
  --url 'https://example.com/download?id=123' \
  --bundle-id com.example.app --name App --version 1.2
# Metadata from a verified local IPA; downloads from the uploaded OpenList object.
python3 ipa_source.py --config data/config.json add \
  --openlist-path /115/ipa/App-1.2.ipa --inspect-from /path/to/App-1.2.ipa
python3 ipa_source.py --config data/config.json feed-url
```

Import the printed URL into PlayCover → Settings → IPA Sources. Each bundle ID has
one current entry; reimporting updates its version and locator. The JSON follows
PlayCover's `bundleID`, `name`, `version`, `itunesLookup`, `link` schema. Download
links always end in `.ipa`, including arbitrary upstream download URLs. Management
is local CLI only; serialize imports if running more than one CLI writer.

This service manages files and metadata; it cannot make a non-IPA file runnable,
decrypt an app, or guarantee app compatibility with PlayCover.

## OpenList links

Every download request retrieves a fresh direct URL. The download client's
User-Agent is forwarded to OpenList because some storage providers bind direct
links to it. The admin OpenList token is never forwarded to a download host.
Providers requiring extra cookies/headers cannot use a simple redirect: use
OpenList's proxy download endpoint for those providers.

## Private feed and optional CI artifacts

The feed URL contains a read capability. Keep it private; changing `feed_token`
and restarting revokes the old link. No backend locators or credentials appear in
the JSON. Request logging intentionally omits paths.

The optional `/artifacts/{key}` gateway requires the separate CI Bearer token and
serves only explicitly registered files inside `artifact_root`. It supports HEAD
and byte ranges and does not expose Dagu's UI or control APIs. Registry format:

```json
{"run-id/decrypted/App.ipa":{"path":"/dagu-data/data/artifacts/.../App.ipa"}}
```

Keep the configuration, catalog, IPA binaries and credentials outside Git.

## Verify

```sh
python3 -m unittest discover -s tests -v
```

Tests cover private-feed access, metadata import, byte ranges, HEAD, artifact
path confinement and live OpenList redirect resolution. MIT licensed. No IPA
binaries or decryption tooling are distributed in this repository.
