"""Device trust gate regressions: Win10/Win11 support, malformed identities still blocked."""
import hashlib
from pathlib import Path
import tempfile
from unittest.mock import patch, PropertyMock
import unittest

from hpa.continuation import local_device

VALID_ID = 'a' * 32


def make_home(identity: str) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix='hpa-trust-'))
    (tmp / 'install_id').write_text(identity, encoding='utf-8')
    (tmp / 'work').mkdir()
    return tmp


def run_local_device(identity: str, system: str, version: str) -> object:
    home = make_home(identity)
    with patch('hpa.continuation.socket.gethostname', return_value='fixture-host'), \
         patch('hpa.continuation.getpass.getuser', return_value='fixture-user'), \
         patch('hpa.continuation.shutil.which', return_value=None):
        with patch('hpa.continuation.platform') as plat:
            plat.system.return_value = system
            plat.version.return_value = version
            plat.platform.return_value = f'Windows-{version}'
            return local_device(home, [home / 'work'])


class DeviceTrustTests(unittest.TestCase):
    def test_win10_19045_trusted(self):
        dev = run_local_device(VALID_ID, 'Windows', '10.0.19045')
        self.assertEqual(dev.trust_status, 'TRUSTED')
        self.assertEqual(dev.confidence, 1.0)

    def test_win11_26300_trusted(self):
        dev = run_local_device(VALID_ID, 'Windows', '10.0.26300')
        self.assertEqual(dev.trust_status, 'TRUSTED')
        self.assertEqual(dev.confidence, 1.0)

    def test_win11_22000_boundary_trusted(self):
        dev = run_local_device(VALID_ID, 'Windows', '10.0.22000')
        self.assertEqual(dev.trust_status, 'TRUSTED')

    def test_below_win10_rtm_blocked(self):
        dev = run_local_device(VALID_ID, 'Windows', '10.0.1')
        self.assertEqual(dev.trust_status, 'UNTRUSTED')
        self.assertEqual(dev.confidence, 0.0)

    def test_non_windows_blocked(self):
        dev = run_local_device(VALID_ID, 'Linux', '5.10.100')
        self.assertEqual(dev.trust_status, 'UNTRUSTED')

    def test_invalid_install_id_blocked(self):
        for bad in ('not-a-hex-id', '', 'A' * 32, 'b' * 31):
            with self.subTest(identity=bad):
                dev = run_local_device(bad, 'Windows', '10.0.26300')
                self.assertEqual(dev.trust_status, 'UNTRUSTED')

    def test_malformed_version_blocked(self):
        for version in ('10.0', '10.0.abc', '9.0.19045', '11.0.26300', '10.26300'):
            with self.subTest(version=version):
                dev = run_local_device(VALID_ID, 'Windows', version)
                self.assertEqual(dev.trust_status, 'UNTRUSTED')

    def test_device_id_formula_stable(self):
        # machine_id = sha256(install_id|host|user)[:32]; unchanged by trust gate.
        expected = hashlib.sha256(f'{VALID_ID}|fixture-host|fixture-user'.encode()).hexdigest()[:32]
        for version in ('10.0.19045', '10.0.26300'):
            dev = run_local_device(VALID_ID, 'Windows', version)
            self.assertEqual(dev.device_id, expected)
            self.assertEqual(dev.os, f'Windows-{version}')


if __name__ == '__main__':
    unittest.main()
