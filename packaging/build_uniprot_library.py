"""Download reference proteomes and build a portable, read-only sequence index.

Data remain outside the source repository. Uses only Python's standard library.
UniProt data: https://www.uniprot.org/help/license (CC BY 4.0).
"""
import argparse
import contextlib
import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import urllib.parse
import urllib.request

DEFAULT_PROTEOMES = ('UP000000625', 'UP000005640', 'UP000000589', 'UP000002311')
ACCESSION = re.compile(r'(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})(?:-[1-9][0-9]*)?')
FORMAT = 'af3-console-uniprot-v1'
HTTP_CLIENT = 'urllib'
_HTTP_LOCAL = threading.local()


@contextlib.contextmanager
def response_stream(url):
    headers = {'User-Agent': 'AF3-Console-offline-library/1.0', 'Accept-Encoding': 'identity'}
    if HTTP_CLIENT == 'requests':
        import requests  # Optional build-time acceleration; not a runtime dependency.
        if not hasattr(_HTTP_LOCAL, 'session'):
            _HTTP_LOCAL.session = requests.Session()
        with _HTTP_LOCAL.session.get(url, headers=headers, timeout=(30, 120), stream=True) as response:
            response.raise_for_status()
            yield response.headers, response.iter_content(128 * 1024)
    else:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=120) as response:
            yield response.headers, iter(lambda: response.read(128 * 1024), b'')


def sha256(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def download(url, path):
    """Cache verified downloads for resumable builds; never reuse partial files."""
    path = Path(path)
    receipt_path = path.with_name(path.name + '.receipt.json')
    if path.exists() and receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if receipt['url'] == url and receipt['sha256'] == sha256(path):
            return receipt
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + '.part')
    for attempt in range(3):
        try:
            with response_stream(url) as (headers, chunks), part.open('wb') as handle:
                for block in chunks:
                    handle.write(block)
                receipt = {'url': url, 'downloaded_utc': datetime.now(timezone.utc).isoformat(),
                           'release': headers.get('X-UniProt-Release'),
                           'release_date': headers.get('X-UniProt-Release-Date'),
                           'total_results': headers.get('X-Total-Results'),
                           'link': headers.get('Link'),
                           'bytes': handle.tell()}
                length = headers.get('Content-Length')
                if length and int(length) != handle.tell():
                    raise ValueError('Incomplete HTTP response: ' + url)
            if path.suffix == '.gz':
                with gzip.open(part, 'rb') as handle:
                    while handle.read(1024 * 1024):
                        pass  # Verify the complete gzip stream, including its CRC.
            receipt['sha256'] = sha256(part)
            part.replace(path)
            receipt_path.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
            return receipt
        except Exception as exc:
            part.unlink(missing_ok=True)
            if attempt == 2:
                raise
            print('Retry download: ' + type(exc).__name__, flush=True)
            time.sleep(2 * (attempt + 1))


def fasta_records(path):
    accession, sequence = None, []
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            if line.startswith('>'):
                if accession is not None:
                    yield accession, ''.join(sequence)
                fields = line[1:].split()[0].split('|')
                if len(fields) < 3 or fields[0] not in ('sp', 'tr') or not ACCESSION.fullmatch(fields[1]):
                    raise ValueError('Not a UniProt accession FASTA header')
                accession, sequence = fields[1], []
            else:
                if accession is None or not re.fullmatch('[A-Z]+', line):
                    raise ValueError('Invalid protein sequence in FASTA')
                sequence.append(line)
    if accession is not None:
        yield accession, ''.join(sequence)


def add_proteome(connection, fasta_path, tsv_path, proteome, expected_count, expected_fasta=None):
    """Check every canonical ID and length against an independent TSV export."""
    canonical = {}
    aliases = []
    with gzip.open(tsv_path, 'rt', encoding='utf-8', newline='') as handle:
        reader = csv.reader(handle, delimiter='\t')
        header = next(reader)
        if header not in (['Entry', 'Secondary accessions', 'Length'], ['Entry', 'Length']):
            raise ValueError('Unexpected UniProt TSV columns: ' + repr(header))
        for row in reader:
            accession, secondary, length = row if len(header) == 3 else (row[0], '', row[1])
            if not ACCESSION.fullmatch(accession) or accession in canonical:
                raise ValueError('Duplicate or invalid canonical accession')
            canonical[accession] = int(length)
            aliases.extend((alias.strip(), accession) for alias in secondary.split(';') if alias.strip())
    if len(canonical) != int(expected_count):
        raise ValueError('Incomplete canonical export: ' + proteome)
    seen, canonical_seen, isoforms = set(), set(), 0
    for accession, sequence in fasta_records(fasta_path):
        if not sequence or accession in seen:
            raise ValueError('Empty sequence or duplicate FASTA accession')
        seen.add(accession)
        if '-' in accession:
            if accession.split('-')[0] not in canonical:
                raise ValueError('Isoform without a canonical record')
            isoforms += 1
        else:
            if canonical.get(accession) != len(sequence):
                raise ValueError('Canonical FASTA/TSV length mismatch')
            canonical_seen.add(accession)
        connection.execute('INSERT INTO sequences VALUES (?, ?, ?)', (accession, sequence, proteome))
    if canonical_seen != set(canonical):
        raise ValueError('Canonical FASTA export is incomplete')
    if expected_fasta is not None and len(seen) != expected_fasta:
        raise ValueError('Canonical/isoform FASTA export count differs from the server')
    for alias, accession in aliases:
        if not ACCESSION.fullmatch(alias):
            raise ValueError('Invalid secondary accession')
        connection.execute('INSERT OR IGNORE INTO alias_candidates VALUES (?, ?)', (alias, accession))
    return {'proteome': proteome, 'canonical_sequences': len(canonical_seen),
            'additional_isoform_sequences': isoforms, 'sequences': len(seen)}


def build_index(datasets, output, provenance):
    """Refuse to replace an existing library; publish only a completely valid DB."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError('Choose a new output directory: ' + str(output))
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.partial', delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with contextlib.closing(sqlite3.connect(temporary)) as connection:
            connection.executescript('''
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
                CREATE TABLE sequences (accession TEXT PRIMARY KEY, sequence TEXT NOT NULL,
                                        proteome TEXT NOT NULL) WITHOUT ROWID;
                CREATE TABLE alias_candidates (alias TEXT, accession TEXT,
                                               PRIMARY KEY(alias, accession)) WITHOUT ROWID;
                CREATE TABLE aliases (alias TEXT PRIMARY KEY, accession TEXT NOT NULL) WITHOUT ROWID;
            ''')
            counts = [add_proteome(connection, **dataset) for dataset in datasets]
            # Secondary IDs can be ambiguous after a split. Never guess a target.
            connection.execute('''INSERT INTO aliases SELECT alias, MIN(accession)
                FROM alias_candidates WHERE alias NOT IN (SELECT accession FROM sequences)
                GROUP BY alias HAVING COUNT(DISTINCT accession) = 1''')
            ambiguous = connection.execute('''SELECT COUNT(*) FROM (SELECT alias FROM alias_candidates
                GROUP BY alias HAVING COUNT(DISTINCT accession) > 1)''').fetchone()[0]
            connection.execute('DROP TABLE alias_candidates')
            report = dict(provenance, format=FORMAT, datasets=counts,
                          sequence_count=sum(item['sequences'] for item in counts),
                          alias_count=connection.execute('SELECT COUNT(*) FROM aliases').fetchone()[0],
                          ambiguous_aliases_omitted=ambiguous)
            connection.executemany('INSERT INTO metadata VALUES (?, ?)',
                                  [('format', FORMAT), ('manifest', json.dumps(report, ensure_ascii=True))])
            connection.commit()
            if connection.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                raise ValueError('SQLite integrity check failed')
            connection.execute('VACUUM')
        temporary.rename(output)
        return dict(report, database_bytes=output.stat().st_size, database_sha256=sha256(output))
    finally:
        temporary.unlink(missing_ok=True)


def download_proteome(proteome, directory):
    """Small resumable API pages avoid fragile long-running streaming responses."""
    directory = Path(directory)
    details_path = directory / (proteome + '.json')
    receipt = download('https://rest.uniprot.org/proteomes/' + proteome, details_path)
    details = json.loads(details_path.read_text(encoding='utf-8'))
    source = {'proteome': proteome, 'taxonomy': details['taxonomy'], 'downloads': [receipt]}
    releases = {receipt['release']}
    for fmt, options, page_size in [('fasta', {'includeIsoform': 'true'}, 500),
                                    ('tsv', {'fields': 'accession,length'}, 500)]:
        url = 'https://rest.uniprot.org/uniprotkb/search?' + urllib.parse.urlencode(
            dict(query='proteome:' + proteome, format=fmt, size=page_size, **options))
        pages, seen_urls, expected = [], set(), None
        while url:
            if url in seen_urls:
                raise ValueError('Repeated pagination cursor')
            seen_urls.add(url)
            path = directory / proteome / (str(len(pages) + 1).zfill(5) + '.' + fmt)
            receipt = download(url, path)
            if not receipt['release'] or not receipt['total_results']:
                raise ValueError('Missing release or expected result count')
            if expected is not None and expected != int(receipt['total_results']):
                raise ValueError('Result count changed during download')
            expected = int(receipt['total_results'])
            releases.add(receipt['release'])
            pages.append(path)
            source['downloads'].append(receipt)
            match = re.search(r'<([^>]+)>;\s*rel="next"', receipt.get('link') or '')
            url = match.group(1) if match else None
            if url and urllib.parse.urlparse(url).netloc != 'rest.uniprot.org':
                raise ValueError('Unexpected pagination host')
            print(proteome + ' ' + fmt + ': page ' + str(len(pages)) + ', entries ' + str(expected), flush=True)
        target = directory / (proteome + '.' + fmt + '.gz')
        with gzip.open(target, 'wb') as output:
            for number, page in enumerate(pages):
                with page.open('rb') as handle:
                    if fmt == 'tsv' and number:
                        handle.readline()  # One header across all pages.
                    shutil.copyfileobj(handle, output)
        source[fmt + '_sha256'] = sha256(target)
        source[fmt + '_expected_count'] = expected
    return (dict(proteome=proteome, expected_count=expected, expected_fasta=source['fasta_expected_count'],
                 fasta_path=directory / (proteome + '.fasta.gz'),
                 tsv_path=directory / (proteome + '.tsv.gz')), source, releases)


def main():
    global HTTP_CLIENT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True,
                        help='New directory for uniprot.sqlite3 and manifest.json')
    parser.add_argument('--proteomes', nargs='+', default=DEFAULT_PROTEOMES)
    parser.add_argument('--http-client', choices=('urllib', 'requests'), default='urllib',
                        help='Optional requests package reuses HTTPS connections for faster downloads')
    args = parser.parse_args()
    HTTP_CLIENT = args.http_client
    if HTTP_CLIENT == 'requests':
        import requests
    if (args.output_dir / 'uniprot.sqlite3').exists():
        parser.error('Output already exists; use a new directory to preserve it.')
    for proteome in args.proteomes:
        if not re.fullmatch(r'UP[0-9]{9}', proteome):
            parser.error('Invalid proteome ID')
    datasets, sources, releases = [], [], set()
    with ThreadPoolExecutor(max_workers=4) as executor:
        for dataset, source, dataset_releases in executor.map(
                lambda proteome: download_proteome(proteome, args.download_dir), args.proteomes):
            datasets.append(dataset)
            sources.append(source)
            releases.update(dataset_releases)
    if len(releases) != 1:
        raise ValueError('Mixed UniProt releases; download into a fresh directory')
    manifest = build_index(datasets, args.output_dir / 'uniprot.sqlite3', {
        'built_utc': datetime.now(timezone.utc).isoformat(), 'uniprot_release': releases.pop(),
        'sources': sources, 'license': 'CC-BY-4.0', 'attribution': 'The UniProt Consortium',
        'license_url': 'https://www.uniprot.org/help/license',
        'scope': 'Selected reference proteomes; canonical sequences and additional exported isoforms. '
                 'Only the primary and isoform accessions exported in FASTA are covered; '
                 'not all strains, variants, secondary or historical IDs.',
        'transformations': 'FASTA sequences preserved; indexed by accession in SQLite. No sequence changes.'})
    (args.output_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: manifest[key] for key in ('uniprot_release', 'sequence_count', 'alias_count',
                                                  'database_bytes', 'database_sha256', 'datasets')}, indent=2))


if __name__ == '__main__':
    main()
