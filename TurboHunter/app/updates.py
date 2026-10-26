"""Read the public Nexus version; updates are installed by the player."""
from html.parser import HTMLParser
import re
import urllib.request

NEXUS_URL = 'https://www.nexusmods.com/thehuntercallofthewild/mods/1049'
NEXUS_FILES_URL = NEXUS_URL + '?tab=files'
GITHUB_URL = 'https://github.com/MarcosDavid23/Turbo-Hunter'


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', str(value).strip())
    return tuple(map(int, match.groups())) if match else None


class _NexusVersions(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.div_depth = 0
        self.main_depth = None
        self.file_depth = None
        self.main_versions = []
        self.header_text = None
        self.header_versions = []
        self.meta_versions = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'div':
            self.div_depth += 1
            if attrs.get('id', '').startswith('file-container-'):
                self.file_depth = self.div_depth
            if attrs.get('id') == 'file-container-main-files':
                self.main_depth = self.div_depth
        if tag == 'dt' and self.main_depth is not None:
            # Optional/old files and unrelated page/software versions cannot
            # override the current Turbo Hunter main download.
            name = re.sub(r'[^a-z0-9]', '', attrs.get('data-name', '').lower())
            value = attrs.get('data-version', '')
            if name.startswith('turbohunter') and version_tuple(value):
                self.main_versions.append(value.strip().lstrip('v'))
        if tag == 'li' and self.file_depth is None and 'stat-version' in attrs.get('class', '').split():
            self.header_text = []
        if tag == 'meta' and attrs.get('property', '').lower() == 'twitter:data1':
            value = attrs.get('content', '')
            if version_tuple(value):
                self.meta_versions.append(value.strip().lstrip('v'))

    def handle_endtag(self, tag):
        if tag == 'div':
            if self.div_depth == self.file_depth:
                self.file_depth = None
            if self.div_depth == self.main_depth:
                self.main_depth = None
            self.div_depth = max(0, self.div_depth - 1)
        if tag == 'li' and self.header_text is not None:
            match = re.search(r'\b\d+\.\d+\.\d+\b', ' '.join(self.header_text))
            if match:
                self.header_versions.append(match.group())
            self.header_text = None

    def handle_data(self, data):
        if self.header_text is not None:
            self.header_text.append(data)


def nexus_version(document):
    parser = _NexusVersions()
    parser.feed(document)
    # Prefer the main file over a stale page header. A visible mod version
    # remains useful even when Nexus temporarily hides the download buttons.
    for candidates in (parser.main_versions, parser.header_versions[:1], parser.meta_versions[:1]):
        if candidates:
            return max(candidates, key=version_tuple)
    return None


def _read(url):
    request = urllib.request.Request(url, headers={
        'User-Agent': 'TurboHunter/0.6.0 (+https://github.com/MarcosDavid23/Turbo-Hunter)',
        'Accept': 'text/html',
    })
    with urllib.request.urlopen(request, timeout=6) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('Response too large')
        return raw.decode('utf-8', errors='replace')


def check_updates(current):
    local = version_tuple(current)
    if local is None:
        return {'status': 'unavailable', 'url': NEXUS_FILES_URL}
    try:
        latest = nexus_version(_read(NEXUS_FILES_URL))
    except (OSError, ValueError):
        latest = None
    if latest:
        return {'status': 'new' if version_tuple(latest) > local else 'current',
                'version': latest, 'source': 'Nexus Mods', 'url': NEXUS_FILES_URL}
    return {'status': 'unavailable', 'url': NEXUS_FILES_URL}
