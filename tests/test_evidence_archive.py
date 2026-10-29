import pytest
from app.platform.evidence import EvidenceArchive


def test_archive_roundtrip_and_tamper_detection(tmp_path):
    archive = EvidenceArchive(tmp_path)
    reference = archive.put('RUN-' + 'a' * 32, {'id': 'EV-' + 'b' * 32, 'data': {'facts': ['record-1']}})
    assert archive.get(reference)['data']['facts'] == ['record-1']
    (tmp_path / reference['key']).write_bytes(b'{}')
    with pytest.raises(ValueError):
        archive.get(reference)


def test_archive_rejects_path_escape(tmp_path):
    archive = EvidenceArchive(tmp_path)
    with pytest.raises(ValueError):
        archive.put('../escape', {'id': 'EV-' + 'b' * 32})
