"""KOReader document identifiers."""
import hashlib
import os

SAMPLE_OFFSETS = (
    0,
    1024,
    4096,
    16384,
    65536,
    262144,
    1048576,
    4194304,
    16777216,
    67108864,
    268435456,
    1073741824,
)
SAMPLE_SIZE = 1024


def partial_md5_checksum(path):
    """KOReader's partial-content MD5 (first 1 KiB at fixed offsets)."""
    digest = hashlib.md5()
    with open(path, 'rb') as stream:
        for offset in SAMPLE_OFFSETS:
            try:
                stream.seek(offset)
                chunk = stream.read(SAMPLE_SIZE)
            except OSError:
                break
            if not chunk:
                break
            digest.update(chunk)
            if len(chunk) < SAMPLE_SIZE:
                break
    return digest.hexdigest()


def filename_checksum(path):
    return hashlib.md5(os.path.basename(path).encode('utf-8')).hexdigest()


def document_id(path, method='binary'):
    if method == 'filename':
        return filename_checksum(path)
    return partial_md5_checksum(path)


def book_document_id(book, method='binary'):
    return document_id(book.abs_path(), method)
