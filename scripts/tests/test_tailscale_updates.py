"""Tailscale updates must neither drop provisioning nor leave the gateway's Serve route down."""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_tasks(path):
    return yaml.safe_load((ROOT / path).read_text())


class TailscaleUpdatesTest(unittest.TestCase):
    def test_provisioning_never_upgrades_tailscale_itself(self):
        # Upgrading Tailscale restarts tailscaled, which carries the provisioning connection.
        tasks = load_tasks('ansible/roles/system/tasks/main.yml')
        upgrade = next((t for t in tasks if any(c.get('ansible.builtin.apt', {}).get('upgrade') == 'dist'
                                                for c in t.get('block', []))), None)
        self.assertIsNotNone(upgrade, 'the dist-upgrade must run inside a block that holds Tailscale')
        self.assertFalse(any(t.get('ansible.builtin.apt', {}).get('upgrade') == 'dist' for t in tasks),
                         'no dist-upgrade may run outside the Tailscale hold')
        hold = upgrade['block'][0]['ansible.builtin.dpkg_selections']
        self.assertEqual((hold['name'], hold['selection']), ('tailscale', 'hold'))
        release = upgrade['always'][0]['ansible.builtin.dpkg_selections']
        self.assertEqual((release['name'], release['selection']), ('tailscale', 'install'))
        names = [t.get('name') for t in tasks]
        # Tailscale's own updater keeps it current; its check needs jq from the base packages.
        self.assertGreater(names.index('Let Tailscale update itself'), names.index('Install base packages'))

    def test_the_gateway_restarts_whenever_tailscaled_restarts(self):
        # The gateway's Serve route ends with tailscaled and is not claimed again.
        main = load_tasks('ansible/roles/openclaw/tasks/main.yml')
        names = [t.get('name') for t in main]
        self.assertIn('Restart the gateway whenever tailscaled restarts', names)
        unit = next(t for t in main if t.get('name') == 'Restart the gateway whenever tailscaled restarts')
        content = unit['ansible.builtin.copy']['content']
        for line in ('PartOf=tailscaled.service', 'RemainAfterExit=yes', 'WantedBy=tailscaled.service',
                     'try-restart openclaw-gateway.service'):
            self.assertIn(line, content)
        enable = next(t for t in main if t.get('name') == 'Enable the gateway restart after tailscaled restarts')
        self.assertTrue(enable['ansible.builtin.systemd']['enabled'])
        # The unit must come after the daemon is installed.
        self.assertGreater(names.index('Restart the gateway whenever tailscaled restarts'),
                           names.index('Install OpenClaw daemon'))


if __name__ == '__main__':
    unittest.main()
